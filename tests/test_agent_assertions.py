"""Agent loop assertions: skill_not_invoked and tool_order; command_succeeds' runner."""

import sys
from pathlib import Path

import pytest

from skillbench.assertions import command_argv

from conftest import load_run_record, run_skillbench, write_test_yaml


def _run_with(monkeypatch, fake_claude, stream: str, assertions: list) -> list[dict]:
    fake_claude.use_stream(monkeypatch, stream)
    test_path = write_test_yaml(
        Path.cwd(), {"name": "loop", "prompt": "do it", "assert": assertions}
    )
    run_skillbench(str(test_path))
    return load_run_record()["assertions"]


def test_skill_not_invoked_passes_when_the_skill_never_ran(fake_claude, monkeypatch):
    [result] = _run_with(
        monkeypatch, fake_claude, "assertions.jsonl", [{"skill_not_invoked": "build"}]
    )

    assert result["passed"], result
    assert "overpowered:grilling" in result["reason"]


@pytest.mark.parametrize("name", ["grilling", "overpowered:grilling"])
def test_skill_not_invoked_fails_when_the_skill_ran(fake_claude, monkeypatch, name):
    [result] = _run_with(
        monkeypatch, fake_claude, "assertions.jsonl", [{"skill_not_invoked": name}]
    )

    assert not result["passed"]
    assert "was invoked 1 time" in result["reason"]


def test_a_plugin_prefix_must_match_when_given(fake_claude, monkeypatch):
    [result] = _run_with(
        monkeypatch, fake_claude, "assertions.jsonl", [{"skill_invoked": "other:grilling"}]
    )

    assert not result["passed"]


def test_tool_order_passes_for_an_ordered_subsequence(fake_claude, monkeypatch):
    [result] = _run_with(
        monkeypatch, fake_claude, "hello_skill.jsonl", [{"tool_order": ["Skill", "Bash"]}]
    )

    assert result["passed"], result
    assert result["reason"] == "called in order: Skill -> Bash"


def test_tool_order_fails_when_out_of_order_and_shows_the_calls(fake_claude, monkeypatch):
    [result] = _run_with(
        monkeypatch, fake_claude, "hello_skill.jsonl", [{"tool_order": ["Bash", "Read"]}]
    )

    assert not result["passed"]
    assert result["reason"] == (
        "Read was not called after Bash (calls in order: Skill, Read, Bash)"
    )


def test_tool_order_counts_calls_made_inside_subagents(fake_claude, monkeypatch):
    # assertions.jsonl: Skill, Write, then a Task whose subagent calls Write.
    [result] = _run_with(
        monkeypatch, fake_claude, "assertions.jsonl", [{"tool_order": ["Skill", "Task", "Write"]}]
    )

    assert result["passed"], result


@pytest.mark.parametrize(
    "entry",
    [
        {"tool_order": []},
        {"tool_order": "Read"},
        {"tool_order": ["Read", ""]},
        {"skill_not_invoked": ""},
        {"skil_not_invoked": "x"},
    ],
)
def test_malformed_agent_assertions_are_rejected_before_running(fake_claude, capsys, entry):
    test_path = write_test_yaml(Path.cwd(), {"name": "bad", "prompt": "/x", "assert": [entry]})

    assert run_skillbench(str(test_path)) == 2

    assert "assert[0]" in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_a_plain_command_is_run_directly_with_the_program_found_on_path():
    argv = command_argv("python -m pytest -q")

    assert isinstance(argv, list)
    assert Path(argv[0]).stem.lower().startswith("python")
    assert argv[1:] == ["-m", "pytest", "-q"]


@pytest.mark.parametrize("command", ["pytest -q && echo ok", "cat a | wc -l", "echo $HOME"])
def test_a_command_with_shell_syntax_goes_to_the_shell(command):
    assert command_argv(command) == command


def test_command_succeeds_runs_a_plain_command_in_the_workspace(fake_claude, monkeypatch):
    command = "git init --quiet made-by-check"
    assert isinstance(command_argv(command), list)  # the direct, no-shell path

    [result] = _run_with(monkeypatch, fake_claude, "basic.jsonl", [{"command_succeeds": command}])

    assert result["passed"], result
    assert (Path(load_run_record()["workspace"]) / "made-by-check" / ".git").is_dir()


def test_command_succeeds_reports_a_missing_program(fake_claude, monkeypatch):
    [result] = _run_with(
        monkeypatch, fake_claude, "basic.jsonl", [{"command_succeeds": "no-such-program-xyz"}]
    )

    assert not result["passed"]
    assert "no-such-program-xyz" in result["reason"]


def test_command_succeeds_quoted_arguments_keep_their_spaces():
    argv = command_argv(f'"{sys.executable}" -m "some module"')

    assert argv[1:] == ["-m", "some module"]
