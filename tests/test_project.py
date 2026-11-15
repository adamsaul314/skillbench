"""`skillbench init`, finding the skillbench/ folder from anywhere below it, and its defaults."""

import os
import shutil
from pathlib import Path

import yaml

from conftest import init_project, project_records, skillbench, write_test_yaml

SCAFFOLDED = (
    "skillbench/skillbench.yaml",
    "skillbench/.gitignore",
    "skillbench/skills/example.yaml",
    "skillbench/agent/example.yaml",
    "skillbench/fixtures/sample-repo/README.md",
    "skillbench/fixtures/sample-repo/.claude/skills/hello/SKILL.md",
)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_init_creates_the_layout_in_an_empty_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert skillbench("init") == 0

    for relative in SCAFFOLDED:
        assert (tmp_path / relative).is_file(), relative
    assert (tmp_path / "skillbench/.gitignore").read_text().split() == ["runs/", ".cache/"]
    assert "created  skillbench/skillbench.yaml" in capsys.readouterr().out


def test_init_samples_are_valid_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skillbench("init")
    from skillbench.config import load_test
    from skillbench.project import find_project

    project = find_project(tmp_path)
    for sample in ("skills/example.yaml", "agent/example.yaml"):
        spec = load_test(project.directory / sample, project)
        assert spec.fixture == (tmp_path / "skillbench/fixtures/sample-repo").resolve()
    assert yaml.safe_load((tmp_path / "skillbench/skillbench.yaml").read_text()) == {
        "timeout_s": 600
    }


def test_init_again_changes_nothing_and_says_what_exists(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    skillbench("init")
    (tmp_path / "skillbench/skills/example.yaml").write_text("name: mine\nprompt: hi\n")
    before = _snapshot(tmp_path)
    capsys.readouterr()

    assert skillbench("init") == 0

    assert _snapshot(tmp_path) == before
    output = capsys.readouterr().out
    assert "created" not in output
    assert "exists   skillbench/skills/example.yaml" in output


def test_init_leaves_the_repo_root_gitignore_alone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n")

    skillbench("init")

    assert (tmp_path / ".gitignore").read_text() == "node_modules/\n"


def test_commands_find_the_project_from_a_subfolder_and_write_runs_inside_it(
    fake_claude, monkeypatch
):
    project_dir = init_project()
    write_test_yaml(project_dir / "agent", {"name": "loop", "prompt": "hi"}, "loop.yaml")
    deep = Path.cwd() / "src" / "pkg"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    assert skillbench("agent") == 0

    assert not (deep / "runs").exists()
    assert len(list((project_dir / "runs").glob("*/run.json"))) == 1
    assert (project_dir / "runs" / "index.html").is_file()


def test_commands_work_from_inside_the_project_folder(fake_claude, monkeypatch):
    project_dir = init_project()
    write_test_yaml(project_dir / "agent", {"name": "loop", "prompt": "hi"}, "loop.yaml")
    monkeypatch.chdir(project_dir)

    assert skillbench("agent") == 0

    assert len(list((project_dir / "runs").glob("*/run.json"))) == 1


def test_commands_without_a_project_say_to_run_init(fake_claude, capsys):
    shutil.rmtree(Path.cwd() / "skillbench")

    exit_code = skillbench("skill")

    assert exit_code == 2
    assert "skillbench init" in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_dashboard_command_rebuilds_the_project_dashboard(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    skillbench("init")
    capsys.readouterr()

    assert skillbench("dashboard") == 0

    assert (tmp_path / "skillbench/runs/index.html").is_file()
    assert os.path.join("skillbench", "runs", "index.html") in capsys.readouterr().out


def test_project_defaults_apply_when_the_test_sets_nothing(fake_claude):
    project_dir = init_project("model: from-config\ntimeout_s: 42\n")
    write_test_yaml(project_dir / "agent", {"name": "loop", "prompt": "hi"}, "loop.yaml")

    skillbench("agent")

    argv = fake_claude.argv
    assert argv[argv.index("--model") + 1] == "from-config"


def test_the_test_wins_over_project_defaults_and_the_cli_wins_over_both(fake_claude):
    project_dir = init_project("model: from-config\n")
    write_test_yaml(
        project_dir / "agent", {"name": "loop", "prompt": "hi", "model": "from-test"}, "loop.yaml"
    )

    skillbench("agent")
    test_model = fake_claude.invocations[0]["argv"]
    skillbench("agent", "--model", "from-cli")
    cli_model = fake_claude.invocations[1]["argv"]

    assert test_model[test_model.index("--model") + 1] == "from-test"
    assert cli_model[cli_model.index("--model") + 1] == "from-cli"


def test_project_backend_default_is_used_without_flags(fake_agents):
    project_dir = init_project("backend: copilot\n")
    write_test_yaml(project_dir / "agent", {"name": "loop", "prompt": "hi"}, "loop.yaml")

    assert skillbench("agent") == 0

    assert [record["backend"] for record in project_records()] == ["copilot"]
    assert not fake_agents.claude.was_launched


def test_an_invalid_project_config_is_a_usage_error(fake_claude, capsys):
    init_project("backend: gpt\n")

    exit_code = skillbench("agent")

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "skillbench.yaml" in error_output
    assert "'backend'" in error_output


def test_fixture_paths_in_a_project_start_at_the_repo_root(fake_claude):
    project_dir = init_project()
    (Path.cwd() / "app").mkdir()
    (Path.cwd() / "app" / "main.py").write_text("print('hi')\n")
    write_test_yaml(
        project_dir / "agent", {"name": "loop", "prompt": "hi", "fixture": "app"}, "loop.yaml"
    )

    assert skillbench("agent") == 0

    workspace = Path(project_records()[0]["workspace"])
    assert (workspace / "main.py").is_file()
