"""Creating the fresh temp workspace each run happens in, from a local folder or a git repo."""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_WORKSPACE_PREFIX = "skillbench-ws-"
_REPOS_CACHE_DIR = "repos"
_CACHE_KEY_LENGTH = 12
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

# URLs already fetched in this process: each repo is updated once per invocation, not per test.
_fetched_urls: set[str] = set()


def is_git_url(text: str) -> bool:
    return bool(_GIT_URL.search(text))


def create_workspace(
    fixture: Fixture | None, cache_dir: Path, exclude: Path | None = None
) -> tuple[Path, str | None]:
    """Make a new temp dir (kept after the run) holding the fixture, with git initialised.
    Returns it and, for a git fixture, the commit it was checked out at. `exclude` (the
    skillbench folder) is left out when copying a local folder that contains it."""
    workspace = Path(tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX))
    if isinstance(fixture, GitFixture):
        return workspace, _clone_into(fixture, cache_dir, workspace)
    if fixture is not None:
        _copy_folder(fixture, workspace, exclude)
    _git(["init", "--quiet"], workspace)
    return workspace, None


def _copy_folder(source: Path, workspace: Path, exclude: Path | None) -> None:
    """Copy what git would see (tracked and untracked files, not ignored ones) when `source`
    is in a git repo; otherwise everything. Never the excluded folder."""
    excluded = exclude.resolve() if exclude is not None else None
    if excluded is not None and source.resolve().is_relative_to(excluded):
        excluded = None  # the fixture lives inside the skillbench folder itself
    listed = _git_visible_files(source)
    if listed is None:
        shutil.copytree(source, workspace, dirs_exist_ok=True, ignore=_ignore_folder(excluded))
        return
    for relative in listed:
        file_path = source / relative
        if not file_path.is_file():
            continue  # tracked but deleted in the working tree
        if excluded is not None and file_path.resolve().is_relative_to(excluded):
            continue
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


def _ignore_folder(excluded: Path | None):
    def ignore(directory: str, names: list[str]) -> list[str]:
        if excluded is None:
            return []
        return [name for name in names if (Path(directory) / name).resolve() == excluded]

    return ignore


def _clone_into(fixture: GitFixture, cache_dir: Path, workspace: Path) -> str:
    mirror = _cached_mirror(fixture.url, cache_dir)
    _git_or_fail(["clone", "--quiet", str(mirror), "."], workspace, f"cloning {fixture.url}")
    if fixture.ref is not None:
        _git_or_fail(
            ["checkout", "--quiet", fixture.ref],
            workspace,
            f"checking out ref '{fixture.ref}' of {fixture.url}",
        )
    head = _git_or_fail(["rev-parse", "HEAD"], workspace, f"reading the commit of {fixture.url}")
    return head.strip()


def _cached_mirror(url: str, cache_dir: Path) -> Path:
    """A mirror clone of `url` under the cache, made once and fetched once per invocation."""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:_CACHE_KEY_LENGTH]
    mirror = cache_dir / _REPOS_CACHE_DIR / key
    if (mirror / "HEAD").is_file():
        if url not in _fetched_urls:
            # Offline or the remote moved: carry on with what the cache has.
            _git(["fetch", "--prune", "--quiet"], mirror, check=False)
    else:
        shutil.rmtree(mirror, ignore_errors=True)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git_or_fail(
            ["clone", "--mirror", "--quiet", url, str(mirror)], mirror.parent, f"cloning {url}"
        )
    _fetched_urls.add(url)
    return mirror


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *_GIT_CONFIG, *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=_GIT_ENV,
        check=check,
    )


def _git_or_fail(args: list[str], cwd: Path, doing: str) -> str:
    try:
        completed = _git(args, cwd, check=False)
    except OSError as error:
        raise FixtureError(f"{doing}: git could not be run ({error})") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FixtureError(f"{doing} failed: {detail or f'git exited {completed.returncode}'}")
    return completed.stdout.decode("utf-8", errors="replace")
