"""Skill tests: `skill:` + `input:` found in the workspace repo, the right /command per backend,
and the `skill` / `agent` commands picking tests."""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import (
    init_project,
    project_records,
    skillbench,
    write_plugin,
    write_skill,
    write_test_yaml,
)


def _prompt(argv: list[str]) -> str:
    return argv[argv.index("-p") + 1]


def _flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[index + 1] for index, arg in enumerate(argv) if arg == flag]


def _skill_test(project_dir: Path, file_name: str = "t.yaml", **fields) -> Path:
    spec = {"name": file_name.removesuffix(".yaml"), "fixture": ".", **fields}
    return write_test_yaml(project_dir / "skills", spec, file_name)


def test_the_init_samples_run_end_to_end(fake_claude, monkeypatch, capsys):
    fake_claude.use_stream(monkeypatch, "hello_skill.jsonl")
    skillbench("init")

    assert skillbench("skill") == 0
    assert skillbench("agent") == 0

    skill_run, agent_run = project_records()
    assert skill_run["status"] == "pass", skill_run["assertions"]
    assert agent_run["status"] == "pass", agent_run["assertions"]
    assert _prompt(fake_claude.invocations[0]["argv"]) == "/hello the platform team"
    assert _prompt(fake_claude.invocations[1]["argv"]) == "Please greet the platform team for me."
    assert skill_run["kind"] == "skill"
    assert agent_run["kind"] == "agent"


@pytest.mark.parametrize("backend", ["claude", "copilot"])
def test_a_project_skill_runs_as_its_bare_command(fake_agents, backend):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill")
    _skill_test(project_dir, skill="grill", input="an idea")

    skillbench("skill", f"--{backend}")

    agent = getattr(fake_agents, backend)
    assert _prompt(agent.argv) == "/grill an idea"


def test_a_plugin_skill_is_prefixed_on_claude_and_bare_on_copilot(fake_agents):
    project_dir = init_project()
    plugin_dir = write_plugin(Path.cwd(), "overpowered", ["grilling", "build"])
    _skill_test(project_dir, skill="grilling", input="an idea")

    skillbench("skill", "--claude", "--copilot")

    claude_argv, copilot_argv = fake_agents.claude.argv, fake_agents.copilot.argv
    assert _prompt(claude_argv) == "/overpowered:grilling an idea"
    assert _prompt(copilot_argv) == "/grilling an idea"
    workspace_plugin = Path(project_records()[0]["workspace"]) / plugin_dir.name
    assert [Path(p) for p in _flag_values(claude_argv, "--plugin-dir")] == [workspace_plugin]
    assert len(_flag_values(copilot_argv, "--plugin-dir")) == 1


def test_every_plugin_in_the_repo_is_loaded_for_agent_tests(fake_claude):
    project_dir = init_project()
    write_plugin(Path.cwd() / "plugins", "one", ["a"])
    write_plugin(Path.cwd() / "plugins", "two", ["b"])
    write_test_yaml(project_dir / "agent", {"name": "loop", "prompt": "hi", "fixture": "."})

    skillbench("agent")

    plugin_dirs = [Path(p).name for p in _flag_values(fake_claude.argv, "--plugin-dir")]
    assert plugin_dirs == ["one", "two"]


def test_a_skill_can_be_named_by_its_path(fake_claude):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill", "skills")
    write_skill(Path.cwd(), "grill")
    _skill_test(project_dir, skill="skills/grill")

    skillbench("skill")

    assert project_records()[0]["status"] == "pass"
    assert _prompt(fake_claude.argv) == "/grill"


def test_an_ambiguous_skill_name_is_an_error_that_lists_the_candidates(fake_claude, capsys):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill", "skills")
    write_skill(Path.cwd(), "grill", ".github/skills")
    _skill_test(project_dir, skill="grill")

    assert skillbench("skill") == 1

    record = project_records()[0]
    assert record["status"] == "error"
    assert "matches more than one skill" in record["error"]
    assert "skills/grill" in record["error"]
    assert ".github/skills/grill" in record["error"]
    assert not fake_claude.was_launched


def test_a_missing_skill_is_an_error_row_and_the_suite_carries_on(fake_claude, capsys):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill")
    _skill_test(project_dir, "a.yaml", skill="nope")
    _skill_test(project_dir, "b.yaml", skill="grill")

    assert skillbench("skill") == 1

    first, second = project_records()
    assert first["status"] == "error"
    assert "skill 'nope' not found" in first["error"]
    assert "found: grill" in first["error"]
    assert second["status"] == "pass"


def test_root_skills_are_copied_where_the_cli_looks_and_hidden_from_git(fake_claude):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill", "skills")
    _skill_test(project_dir, skill="grill")

    skillbench("skill")

    workspace = Path(project_records()[0]["workspace"])
    assert (workspace / ".claude/skills/grill/SKILL.md").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True
    ).stdout
    assert ".claude" not in status


def test_the_skillbench_folder_is_never_copied_into_the_workspace(fake_claude):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill")
    _skill_test(project_dir, skill="grill")

    skillbench("skill")

    workspace = Path(project_records()[0]["workspace"])
    assert not (workspace / "skillbench").exists()
    assert (workspace / ".claude/skills/grill").is_dir()


def test_the_skill_command_picks_tests_by_name_file_or_skill(fake_claude):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill")
    write_skill(Path.cwd(), "plan")
    _skill_test(project_dir, "a.yaml", name="grill-asks", skill="grill")
    _skill_test(project_dir, "b.yaml", name="plan-writes", skill="plan")

    skillbench("skill", "plan")
    skillbench("skill", "grill-asks")
    skillbench("skill", "b")

    assert [record["test_name"] for record in project_records()] == [
        "plan-writes",
        "grill-asks",
        "plan-writes",
    ]


def test_an_unknown_target_is_a_usage_error_listing_the_tests(fake_claude, capsys):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill")
    _skill_test(project_dir, name="grill-asks", skill="grill")

    assert skillbench("skill", "nothing") == 2

    assert "grill-asks" in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_the_skill_command_only_runs_skill_tests(fake_claude):
    project_dir = init_project()
    write_skill(Path.cwd(), "grill")
    _skill_test(project_dir, skill="grill")
    write_test_yaml(project_dir / "agent", {"name": "loop", "prompt": "hi"})

    skillbench("skill")

    assert [record["kind"] for record in project_records()] == ["skill"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"skill": "x", "prompt": "/x"}, "use 'input' with 'skill'"),
        ({"input": "hi", "prompt": "/x"}, "'input' needs 'skill'"),
        ({"name": "nothing"}, "missing required field 'prompt' (or 'skill')"),
        ({"skill": ""}, "'skill'"),
        ({"skill": "x", "triggers": {"maybe": ["hi"]}}, "'triggers'"),
        ({"skill": "x", "triggers": {"should": []}}, "'triggers'"),
    ],
)
def test_invalid_skill_fields_are_rejected_before_anything_runs(
    fake_claude, capsys, fields, message
):
    project_dir = init_project()
    write_test_yaml(project_dir / "skills", fields, "bad.yaml")

    assert skillbench("skill") == 2

    assert message in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_skill_invoked_matches_a_plugin_skill_by_its_bare_name(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "assertions.jsonl")  # invokes overpowered:grilling
    project_dir = init_project()
    write_test_yaml(
        project_dir / "agent",
        {"name": "loop", "prompt": "hi", "assert": [{"skill_invoked": "grilling"}]},
    )

    assert skillbench("agent") == 0

    [result] = project_records()[0]["assertions"]
    assert result["passed"], result
    assert json.dumps(result["reason"]).count("invoked 1 time") == 1
