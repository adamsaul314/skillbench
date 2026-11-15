"""Creating the fresh temp workspace each run happens in, from a local folder or a git repo."""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# git:// ssh:// https:// file://, scp-style user@host:path, or anything ending in .git
_GIT_URL = re.compile(r"^(https?|ssh|git|file)://|^[\w.-]+@[\w.-]+:|\.git/?$")
# Never let git stop to ask for a password: a headless run would hang on it.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
# Windows' 260-character path limit trips over deep repos without this; harmless elsewhere.
_GIT_CONFIG = ["-c", "core.longpaths=true"]


class FixtureError(Exception):
    """The fixture could not be copied or cloned; the message says why."""


@dataclass(frozen=True)
class GitFixture:
    url: str
    ref: str | None = None  # branch, tag or commit; None = the default branch

    def __str__(self) -> str:
        return f"{self.url}@{self.ref}" if self.ref else self.url


Fixture = Path | GitFixture


def is_git_url(text: str) -> bool:
    return bool(_GIT_URL.search(text))


def create_workspace(fixture: Fixture | None, exclude: Path) -> tuple[Path, str | None]:
    """Make a new temp dir (kept after the run) holding the fixture, with git initialised.
    Returns it and, for a git fixture, the commit it was checked out at. `exclude` (the
    skillbench folder) is never copied. Raises FixtureError."""
    workspace = Path(tempfile.mkdtemp(prefix="skillbench-ws-"))
    if isinstance(fixture, GitFixture):
        return workspace, _clone(fixture, workspace)
    if fixture is not None:
        _copy_folder(fixture, workspace, exclude)
    _git(["init", "--quiet"], workspace, "initialising the workspace")
    return workspace, None


def _clone(fixture: GitFixture, workspace: Path) -> str:
    """Clone into the workspace, check out the ref if any, and return the commit."""
    _git(["clone", "--quiet", fixture.url, "."], workspace, f"cloning {fixture.url}")
    if fixture.ref is not None:
        doing = f"checking out ref '{fixture.ref}' of {fixture.url}"
        _git(["checkout", "--quiet", fixture.ref], workspace, doing)
    return _git(["rev-parse", "HEAD"], workspace, f"reading the commit of {fixture.url}").strip()


def _copy_folder(source: Path, workspace: Path, exclude: Path) -> None:
    """Copy what git would see (tracked and untracked files, not ignored ones) when `source`
    is in a git repo, otherwise everything; never the excluded folder, unless the fixture
    lives inside it."""
    excluded: Path | None = exclude.resolve()
    if source.resolve().is_relative_to(excluded):
        excluded = None
    listed = _git_visible_files(source)
    if listed is None:
        ignore = None if excluded is None else _ignore_path(excluded)
        shutil.copytree(source, workspace, dirs_exist_ok=True, ignore=ignore)
        return
    for relative in listed:
        file_path = source / relative
        is_excluded = excluded is not None and file_path.resolve().is_relative_to(excluded)
        if file_path.is_file() and not is_excluded:  # tracked files may be deleted locally
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target)


def _git_visible_files(source: Path) -> list[str] | None:
    """Paths relative to `source`, or None if it isn't inside a git work tree."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=source,
            capture_output=True,
            env=_GIT_ENV,
            check=False,
        )
    except OSError:
        return None
    if listed.returncode != 0:
        return None
    return [path for path in listed.stdout.decode("utf-8").split("\0") if path]


def _ignore_path(excluded: Path):
    """A copytree `ignore` that skips one folder."""

    def ignore(directory: str, names: list[str]) -> list[str]:
        return [name for name in names if (Path(directory) / name).resolve() == excluded]

    return ignore


def _git(args: list[str], cwd: Path, doing: str) -> str:
    """Run git and return its stdout. Raises FixtureError saying what was being done."""
    try:
        completed = subprocess.run(
            ["git", *_GIT_CONFIG, *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_GIT_ENV,
            check=False,
        )
    except OSError as error:
        raise FixtureError(f"{doing}: git could not be run ({error})") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FixtureError(f"{doing} failed: {detail or f'git exited {completed.returncode}'}")
    return completed.stdout.decode("utf-8", errors="replace")
