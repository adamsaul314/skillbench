"""Built-in assertions and the pass/fail/error verdict they produce."""

from pathlib import Path

import pytest

from conftest import load_run_record, run_skillbench, write_test_yaml

PROMPT = "/overpowered:grilling an idea for a todo app"


def _make_fixture(parent: Path) -> Path:
    fixture_dir = parent / "fixture"
    (fixture_dir / "docs").mkdir(parents=True)
    (fixture_dir / "docs" / "notes.md").write_text("approval: approved\n", encoding="utf-8")
    return fixture_dir


def _run_assertions(fake_claude, monkeypatch, assertions: list) -> int:
    fake_claude.use_stream(monkeypatch, "assertions.jsonl")
    _make_fixture(Path.cwd())
    test_path = write_test_yaml(
        Path.cwd(),
        {"name": "asserts", "prompt": PROMPT, "fixture": "fixture", "assert": assertions},
    )
    return run_skillbench(str(test_path))


# The assertions.jsonl stream: Skill(overpowered:grilling), Write at top level, Write inside a
# Task subagent, an early text "Starting the grilling." and a final text "Q1: What problem...";
# the CLI reports 4 turns. The fixture holds docs/notes.md with "approval: approved".
@pytest.mark.parametrize(
    ("assertion", "should_pass", "reason_fragment"),
    [
        ({"skill_invoked": "overpowered:grilling"}, True, "overpowered:grilling"),
        ({"skill_invoked": "overpowered:build"}, False, "overpowered:build was never invoked"),
        ({"tool_called": "Write"}, True, "Write was called 2 times"),
        ({"tool_called": "Edit"}, False, "Edit was never called"),
        ({"tool_not_called": "Edit"}, True, "Edit was never called"),
        ({"tool_not_called": "Write"}, False, "Write was called 2 times"),
        ({"file_exists": "docs/notes.md"}, True, "docs/notes.md"),
        ({"file_exists": "docs/plan.md"}, False, "docs/plan.md does not exist"),
        (
            {"file_contains": {"path": "docs/notes.md", "text": "approval: approved"}},
            True,
            "docs/notes.md",
        ),
        (
            {"file_contains": {"path": "docs/notes.md", "text": "approval: pending"}},
            False,
            "does not contain 'approval: pending'",
        ),
        (
            {"file_contains": {"path": "docs/plan.md", "text": "anything"}},
            False,
            "docs/plan.md does not exist",
        ),
        ({"final_text_contains": "Q1"}, True, "Q1"),
        ({"final_text_contains": "Starting the grilling"}, False, "does not contain"),
        ({"max_turns": 4}, True, "4 turns"),
        ({"max_turns": 3}, False, "4 turns exceeds the limit of 3"),
        ({"exit_ok": True}, True, "exited with code 0"),
    ],
)
def test_ac2_1_each_assertion_type_passes_and_fails(
    fake_claude, monkeypatch, assertion, should_pass, reason_fragment
):
    _run_assertions(fake_claude, monkeypatch, [assertion])

    [result] = load_run_record()["assertions"]
    assert result["passed"] is should_pass
    assert reason_fragment in result["reason"]


def test_ac2_1_exit_ok_fails_when_the_cli_exits_non_zero(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")

    _run_assertions(fake_claude, monkeypatch, [{"exit_ok": True}])

    [result] = load_run_record()["assertions"]
    assert result["passed"] is False
    assert "exited with code 3" in result["reason"]


def test_ac2_1_max_turns_fails_when_the_cli_reports_no_turn_count(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "partial.jsonl")
    test_path = write_test_yaml(
        Path.cwd(), {"name": "no-turns", "prompt": PROMPT, "assert": [{"max_turns": 5}]}
    )

    run_skillbench(str(test_path))

    [result] = load_run_record()["assertions"]
    assert result["passed"] is False
    assert "not reported" in result["reason"]


def test_ac2_2_any_failing_assertion_fails_the_test_and_each_result_is_recorded(
    fake_claude, monkeypatch
):
    exit_code = _run_assertions(
        fake_claude,
        monkeypatch,
        [{"tool_called": "Write"}, {"tool_not_called": "Write"}],
    )

    record = load_run_record()
    assert exit_code == 1
    assert record["status"] == "fail"
    assert record["assertions"] == [
        {
            "type": "tool_called",
            "expected": "Write",
            "passed": True,
            "reason": "Write was called 2 times",
        },
        {
            "type": "tool_not_called",
            "expected": "Write",
            "passed": False,
            "reason": "Write was called 2 times",
        },
    ]


def test_ac2_2_all_passing_assertions_pass_the_test(fake_claude, monkeypatch):
    exit_code = _run_assertions(
        fake_claude, monkeypatch, [{"tool_called": "Skill"}, {"final_text_contains": "Q1"}]
    )

    assert exit_code == 0
    assert load_run_record()["status"] == "pass"


def test_ac2_3_no_assertions_and_completed_run_passes(fake_claude, monkeypatch):
    test_path = write_test_yaml(Path.cwd(), {"name": "capture-only", "prompt": PROMPT})

    exit_code = run_skillbench(str(test_path))

    record = load_run_record()
    assert exit_code == 0
    assert record["status"] == "pass"
    assert record["assertions"] == []


def test_ac2_3_no_assertions_and_non_zero_exit_is_an_error(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    test_path = write_test_yaml(Path.cwd(), {"name": "crashes", "prompt": PROMPT})

    exit_code = run_skillbench(str(test_path))

    assert exit_code == 1
    assert load_run_record()["status"] == "error"


def test_ac2_3_no_assertions_and_timeout_is_an_error(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "partial.jsonl")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP_S", "60")
    test_path = write_test_yaml(Path.cwd(), {"name": "hangs", "prompt": PROMPT, "timeout_s": 2})

    exit_code = run_skillbench(str(test_path))

    record = load_run_record()
    assert exit_code == 1
    assert record["status"] == "error"
    assert record["run_status"] == "timeout"


@pytest.mark.parametrize(
    ("assertions", "named"),
    [
        ([{"tool_caled": "Write"}], "tool_caled"),
        ([{"tool_called": "Write", "exit_ok": True}], "assert[0]"),
        (["tool_called"], "assert[0]"),
        ([{"tool_called": "Write"}, {"tool_called": 3}], "assert[1]"),
        ([{"max_turns": 0}], "assert[0]"),
        ([{"exit_ok": "yes"}], "assert[0]"),
        ([{"file_contains": {"path": "a.md"}}], "assert[0]"),
        ([{"file_contains": {"path": "a.md", "text": "x", "regex": True}}], "assert[0]"),
    ],
)
def test_ac2_6_invalid_assertion_is_a_validation_error_and_launches_nothing(
    fake_claude, capsys, assertions, named
):
    test_path = write_test_yaml(
        Path.cwd(), {"name": "bad", "prompt": PROMPT, "assert": assertions}, "bad-assert.yaml"
    )

    exit_code = run_skillbench(str(test_path))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "bad-assert.yaml" in error_output
    assert named in error_output
    assert not fake_claude.was_launched
    assert not (Path.cwd() / "runs").exists()


@pytest.mark.parametrize(
    "assertion",
    [
        {"file_exists": "../outside.md"},
        {"file_exists": "docs/../../outside.md"},
        {"file_exists": "/etc/passwd"},
        {"file_exists": "C:\\Windows\\win.ini"},
        {"file_contains": {"path": "../outside.md", "text": "x"}},
    ],
)
def test_file_paths_escaping_the_workspace_are_a_validation_error(fake_claude, capsys, assertion):
    test_path = write_test_yaml(
        Path.cwd(), {"name": "escape", "prompt": PROMPT, "assert": [assertion]}, "escape.yaml"
    )

    exit_code = run_skillbench(str(test_path))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "escape.yaml" in error_output
    assert "workspace" in error_output
    assert not fake_claude.was_launched


def test_ac2_1_skill_invoked_counts_a_slash_command_prompt_for_a_loaded_skill(
    fake_claude, monkeypatch
):
    # basic.jsonl loads overpowered:grilling but makes no Skill tool call.
    test_path = write_test_yaml(
        Path.cwd(),
        {"name": "slash", "prompt": PROMPT, "assert": [{"skill_invoked": "overpowered:grilling"}]},
    )

    run_skillbench(str(test_path))

    [result] = load_run_record()["assertions"]
    assert result["passed"] is True
    assert "prompt" in result["reason"]


@pytest.mark.parametrize(
    ("stream", "prompt"),
    [
        # partial.jsonl loads no skills, so the slash command could not have run.
        ("partial.jsonl", PROMPT),
        # The skill is loaded but the prompt names a different command that merely shares a prefix.
        ("basic.jsonl", "/overpowered:grilling-extra an idea"),
    ],
)
def test_ac2_1_skill_invoked_rejects_a_prompt_that_did_not_run_the_skill(
    fake_claude, monkeypatch, stream, prompt
):
    fake_claude.use_stream(monkeypatch, stream)
    test_path = write_test_yaml(
        Path.cwd(),
        {"name": "slash", "prompt": prompt, "assert": [{"skill_invoked": "overpowered:grilling"}]},
    )

    run_skillbench(str(test_path))

    [result] = load_run_record()["assertions"]
    assert result["passed"] is False
