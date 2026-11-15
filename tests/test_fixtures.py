"""Repo fixtures: git URLs and {git, ref} cloned through a cache, and "." copying the repo."""

import subprocess
from pathlib import Path

from conftest import init_project, project_records, skillbench, write_skill, write_test_yaml

_GIT_IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *_GIT_IDENTITY, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _make_remote(root: Path) -> tuple[Path, str, str]:
    """A bare repo with two commits on main (tagged v1 and v2); returns it and both SHAs."""
    source = root / "source"
    source.mkdir()
    _git(source, "init", "--quiet", "--initial-branch=main")
    (source / "app.py").write_text("version = 1\n")
    write_skill(source, "grill")
    _git(source, "add", "-A")
    _git(source, "commit", "--quiet", "-m", "one")
    _git(source, "tag", "v1")
    first = _git(source, "rev-parse", "HEAD")
    (source / "app.py").write_text("version = 2\n")
    _git(source, "commit", "--quiet", "-am", "two")
    second = _git(source, "rev-parse", "HEAD")
    remote = root / "remote.git"
    _git(root, "clone", "--quiet", "--bare", str(source), str(remote))
    return remote, first, second


def _agent_test(project_dir: Path, fixture, file_name: str = "t.yaml") -> None:
    write_test_yaml(
        project_dir / "agent",
        {"name": file_name.removesuffix(".yaml"), "prompt": "hi", "fixture": fixture},
        file_name,
    )


def test_a_git_url_is_cloned_at_its_default_branch_and_the_commit_recorded(fake_claude, tmp_path):
    remote, _, second = _make_remote(tmp_path)
    project_dir = init_project()
    _agent_test(project_dir, remote.as_uri())

    assert skillbench("agent") == 0

    record = project_records()[0]
    workspace = Path(record["workspace"])
    assert (workspace / "app.py").read_text() == "version = 2\n"
    assert record["fixture_commit"] == second
    assert record["fixture"] == remote.as_uri()
    assert second in (project_dir / "runs" / "index.html").read_text(encoding="utf-8")


def test_a_pinned_ref_is_checked_out(fake_claude, tmp_path):
    remote, first, _ = _make_remote(tmp_path)
    project_dir = init_project()
    _agent_test(project_dir, {"git": str(remote), "ref": "v1"})

    skillbench("agent")

    record = project_records()[0]
    assert (Path(record["workspace"]) / "app.py").read_text() == "version = 1\n"
    assert record["fixture_commit"] == first


def test_a_cloned_repos_skills_are_found(fake_claude, tmp_path):
    remote, _, _ = _make_remote(tmp_path)
    project_dir = init_project()
    write_test_yaml(
        project_dir / "skills", {"name": "s", "skill": "grill", "fixture": {"git": str(remote)}}
    )

    assert skillbench("skill") == 0

    assert fake_claude.argv[fake_claude.argv.index("-p") + 1] == "/grill"


def test_a_second_run_uses_the_cache_even_when_the_remote_is_gone(fake_claude, tmp_path):
    remote, _, second = _make_remote(tmp_path)
    project_dir = init_project()
    _agent_test(project_dir, {"git": str(remote)})
    skillbench("agent")
    mirrors = list((project_dir / ".cache" / "repos").iterdir())

    remote.rename(tmp_path / "moved-away.git")
    assert skillbench("agent") == 0

    assert list((project_dir / ".cache" / "repos").iterdir()) == mirrors
    assert len(mirrors) == 1
    assert project_records()[1]["fixture_commit"] == second


def test_a_bad_url_is_an_error_row_and_the_suite_carries_on(fake_claude, tmp_path):
    project_dir = init_project()
    _agent_test(project_dir, {"git": str(tmp_path / "missing.git")}, "a.yaml")
    _agent_test(project_dir, ".", "b.yaml")

    assert skillbench("agent") == 1

    bad, good = project_records()
    assert bad["status"] == "error"
    assert "cloning" in bad["error"]
    assert bad["stop_reason"] == "setup_failed"
    assert good["status"] == "pass"
    assert fake_claude.invocations  # the good test still ran
    assert (project_dir / "runs" / "index.html").is_file()


def test_a_bad_ref_is_an_error_row(fake_claude, tmp_path):
    remote, _, _ = _make_remote(tmp_path)
    project_dir = init_project()
    _agent_test(project_dir, {"git": str(remote), "ref": "no-such-ref"})

    assert skillbench("agent") == 1

    assert "no-such-ref" in project_records()[0]["error"]
    assert not fake_claude.was_launched


def test_dot_copies_the_repo_with_uncommitted_work_but_not_ignored_files(fake_claude):
    repo = Path.cwd()
    _git(repo, "init", "--quiet")
    (repo / ".gitignore").write_text("secrets.env\n")
    (repo / "committed.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    (repo / "committed.py").write_text("a = 2\n")  # uncommitted edit
    (repo / "new.py").write_text("b = 1\n")  # untracked
    (repo / "secrets.env").write_text("TOKEN=x\n")  # ignored
    project_dir = init_project()
    _agent_test(project_dir, ".")

    skillbench("agent")

    workspace = Path(project_records()[0]["workspace"])
    assert (workspace / "committed.py").read_text() == "a = 2\n"
    assert (workspace / "new.py").is_file()
    assert not (workspace / "secrets.env").exists()
    assert not (workspace / "skillbench").exists()
    assert (workspace / ".git").is_dir()


def test_a_project_default_fixture_applies_to_tests_without_one(fake_claude):
    (Path.cwd() / "app").mkdir()
    (Path.cwd() / "app" / "main.py").write_text("x = 1\n")
    project_dir = init_project("fixture: app\n")
    write_test_yaml(project_dir / "agent", {"name": "t", "prompt": "hi"})

    skillbench("agent")

    assert (Path(project_records()[0]["workspace"]) / "main.py").is_file()
