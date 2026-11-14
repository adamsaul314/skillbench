"""Multi-turn conversations: scripted answers, live answers, saving answers, turn limits."""

import sys
from pathlib import Path

import pytest
import yaml

from conftest import (
    FakeTerminal,
    load_run_record,
    only_run_dir,
    run_skillbench,
    type_into_terminal,
    write_test_yaml,
)

PROMPT = "/overpowered:grilling an idea"
THREE_TURN_STREAMS = ("basic.jsonl", "turn2.jsonl", "turn3.jsonl")
BASIC_FINAL_TEXT = "Q1: What is the <app> for?"


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _user_messages(record: dict) -> list[dict]:
    return [item for item in record["trace"]["items"] if item["kind"] == "user_message"]


def _run(spec: dict) -> int:
    test_path = write_test_yaml(Path.cwd(), {"name": "multi", "prompt": PROMPT, **spec})
    return run_skillbench(str(test_path))


def test_ac3_1_scripted_answers_resume_the_session_as_turns_2_and_3(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)

    _run({"answers": ["a", "b"]})

    first, second, third = fake_claude.invocations
    assert first["argv"][:2] == ["-p", PROMPT]
    assert "--resume" not in first["argv"]
    assert second["argv"][:2] == ["-p", "a"]
    assert _flag_value(second["argv"], "--resume") == "sess-basic-1"
    assert third["argv"][:2] == ["-p", "b"]
    assert _flag_value(third["argv"], "--resume") == "sess-basic-1"
    assert first["cwd"] == second["cwd"] == third["cwd"]


def test_ac3_1_each_turn_has_its_own_raw_log(fake_claude, monkeypatch):
    stream_paths = fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)

    _run({"answers": ["a", "b"]})

    raw_dir = only_run_dir() / "raw"
    for turn, stream_path in enumerate(stream_paths, start=1):
        assert (raw_dir / f"{turn}.jsonl").read_bytes() == stream_path.read_bytes()
    assert not (raw_dir / "4.jsonl").exists()


def test_ac3_1_trace_shows_turns_in_order_with_each_user_answer(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)

    _run({"answers": ["a", "b"]})

    record = load_run_record()
    items = record["trace"]["items"]
    assert _user_messages(record) == [
        {"kind": "user_message", "turn": 2, "text": "a", "source": "scripted"},
        {"kind": "user_message", "turn": 3, "text": "b", "source": "scripted"},
    ]
    answer_a, answer_b = (items.index(message) for message in _user_messages(record))
    assert items[answer_a - 1] == {"kind": "assistant_text", "text": BASIC_FINAL_TEXT}
    assert items[answer_a + 1] == {"kind": "assistant_text", "text": "Q2: Who uses it?"}
    assert items[answer_b + 1]["name"] == "Write"
    assert items[-1] == {"kind": "assistant_text", "text": "Plan written."}
    assert record["status"] == "pass"


def test_usage_cost_and_turn_counts_add_up_across_turns(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)

    _run({"answers": ["a", "b"]})

    trace = load_run_record()["trace"]
    assert trace["cost_usd"] == pytest.approx(0.0123 + 0.002 + 0.003)
    assert trace["usage"] == {
        "input_tokens": 42 + 30 + 85,
        "output_tokens": 17 + 6 + 12,
        "cache_read_input_tokens": 5,
    }
    assert trace["num_turns"] == 3 + 1 + 2


def test_a_failed_turn_stops_the_conversation(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")

    _run({"answers": ["a", "b"]})

    assert len(fake_claude.invocations) == 1
    assert load_run_record()["status"] == "error"


def test_a_turn_without_a_session_id_cannot_be_resumed_and_is_an_error(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, "no_session.jsonl")

    exit_code = _run({"answers": ["a"]})

    record = load_run_record()
    assert len(fake_claude.invocations) == 1
    assert record["status"] == "error"
    assert "session id" in record["error"]
    assert exit_code == 1


def test_ac3_2_interactive_prints_final_text_and_sends_the_typed_answer(
    fake_claude, monkeypatch, capsys
):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    type_into_terminal(monkeypatch, "it tracks todos", "/done", "n")

    _run({"interactive": True})

    assert BASIC_FINAL_TEXT in capsys.readouterr().out
    first, second = fake_claude.invocations
    assert second["argv"][:2] == ["-p", "it tracks todos"]
    assert _flag_value(second["argv"], "--resume") == "sess-basic-1"
    assert _user_messages(load_run_record()) == [
        {"kind": "user_message", "turn": 2, "text": "it tracks todos", "source": "live"}
    ]


def test_ac3_2_live_answers_start_only_after_scripted_answers_run_out(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    type_into_terminal(monkeypatch, "typed", "/done", "n")

    _run({"answers": ["scripted"], "interactive": True})

    assert [message["source"] for message in _user_messages(load_run_record())] == [
        "scripted",
        "live",
    ]
    assert fake_claude.invocations[2]["argv"][:2] == ["-p", "typed"]


@pytest.mark.parametrize("ending", ["", "/done"])
def test_ac3_3_empty_answer_or_done_ends_the_conversation_and_assertions_run(
    fake_claude, monkeypatch, ending
):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    type_into_terminal(monkeypatch, ending)

    exit_code = _run({"interactive": True, "assert": [{"final_text_contains": "Q1"}]})

    record = load_run_record()
    assert len(fake_claude.invocations) == 1
    assert record["status"] == "pass"
    assert record["assertions"][0]["passed"] is True
    assert exit_code == 0


def test_ac3_3_end_of_input_ends_the_conversation(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    type_into_terminal(monkeypatch)

    _run({"interactive": True})

    assert len(fake_claude.invocations) == 1
    assert load_run_record()["status"] == "pass"


HAND_WRITTEN_YAML = """\
# A hand-written test; this comment must survive saving.
name: saved
prompt: "/overpowered:grilling an idea"
answers:
  - first   # scripted
interactive: true
timeout_s: 60  # seconds
"""


def test_ac3_4_yes_saves_scripted_and_live_answers_keeping_other_keys_and_comments(
    fake_claude, monkeypatch, capsys
):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    test_path = Path.cwd() / "saved.yaml"
    test_path.write_text(HAND_WRITTEN_YAML, encoding="utf-8")
    type_into_terminal(monkeypatch, "second: with a colon", "/done", "y")

    run_skillbench(str(test_path))

    assert f"save these answers to {test_path}?" in capsys.readouterr().out
    saved_text = test_path.read_text(encoding="utf-8")
    saved = yaml.safe_load(saved_text)
    assert saved["answers"] == ["first", "second: with a colon"]
    original = yaml.safe_load(HAND_WRITTEN_YAML)
    assert {key: value for key, value in saved.items() if key != "answers"} == {
        key: value for key, value in original.items() if key != "answers"
    }
    assert "# A hand-written test; this comment must survive saving." in saved_text
    assert "timeout_s: 60  # seconds" in saved_text


def test_ac3_4_yes_adds_an_answers_key_when_the_file_had_none(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    test_path = write_test_yaml(
        Path.cwd(), {"name": "fresh", "prompt": PROMPT, "interactive": True}
    )
    type_into_terminal(monkeypatch, "live answer", "", "yes")

    run_skillbench(str(test_path))

    saved = yaml.safe_load(test_path.read_text(encoding="utf-8"))
    assert saved == {
        "name": "fresh",
        "prompt": PROMPT,
        "interactive": True,
        "answers": ["live answer"],
    }


def test_ac3_4_no_leaves_the_file_unchanged(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    test_path = Path.cwd() / "saved.yaml"
    test_path.write_text(HAND_WRITTEN_YAML, encoding="utf-8")
    type_into_terminal(monkeypatch, "second", "/done", "n")

    run_skillbench(str(test_path))

    assert test_path.read_text(encoding="utf-8") == HAND_WRITTEN_YAML


def test_ac3_4_no_save_question_without_live_answers(fake_claude, monkeypatch, capsys):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    type_into_terminal(monkeypatch, "/done")

    _run({"answers": ["a"], "interactive": True})

    assert "save these answers" not in capsys.readouterr().out


@pytest.mark.parametrize("answers", [[], ["a"]])
def test_ac3_5_non_terminal_stdin_needing_a_live_answer_is_an_error(
    fake_claude, monkeypatch, capsys, answers
):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    monkeypatch.setattr(sys, "stdin", FakeTerminal("would hang\n", is_tty=False))

    exit_code = _run({"answers": answers, "interactive": True})

    record = load_run_record()
    assert len(fake_claude.invocations) == len(answers) + 1
    assert record["status"] == "error"
    assert "scripted answers are required" in record["error"]
    assert "scripted answers are required" in capsys.readouterr().out
    assert exit_code == 1


def test_ac3_5_non_terminal_stdin_is_fine_when_no_live_answer_is_needed(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    monkeypatch.setattr(sys, "stdin", FakeTerminal("", is_tty=False))

    exit_code = _run({"answers": ["a"]})

    assert load_run_record()["status"] == "pass"
    assert exit_code == 0


def test_ac3_6_max_turns_stops_the_conversation_and_fails_the_max_turns_assertion(
    fake_claude, monkeypatch
):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)

    exit_code = _run({"answers": ["a", "b"], "max_turns": 2, "assert": [{"max_turns": 50}]})

    record = load_run_record()
    assert len(fake_claude.invocations) == 2
    assert [message["text"] for message in _user_messages(record)] == ["a"]
    [result] = record["assertions"]
    assert result["passed"] is False
    assert "max_turns limit of 2" in result["reason"]
    assert record["status"] == "fail"
    assert exit_code == 1


def test_ac3_6_max_turns_counts_live_turns_too(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)
    type_into_terminal(monkeypatch, "typed", "never sent", "n")

    _run({"interactive": True, "max_turns": 2})

    first, second = fake_claude.invocations
    assert second["argv"][:2] == ["-p", "typed"]
    assert load_run_record()["stop_reason"] == "max_turns"


def test_ac3_6_a_conversation_within_max_turns_runs_every_answer(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, *THREE_TURN_STREAMS)

    _run({"answers": ["a", "b"], "max_turns": 3, "assert": [{"max_turns": 50}]})

    record = load_run_record()
    assert len(fake_claude.invocations) == 3
    assert record["assertions"][0]["passed"] is True
    assert record["status"] == "pass"
