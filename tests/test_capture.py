"""What skillbench records about a run: run.json, raw stdout, nesting, timeouts."""

import time
from datetime import datetime
from pathlib import Path

from conftest import load_run_record, only_run_dir, run_skillbench, write_test_yaml

PROMPT = "/overpowered:grilling an idea"


def _run_with_stream(fake_claude, monkeypatch, stream: str = "basic.jsonl", **extra) -> int:
    fake_claude.use_stream(monkeypatch, stream)
    test_path = write_test_yaml(Path.cwd(), {"name": "capture", "prompt": PROMPT, **extra})
    return run_skillbench(str(test_path))


def test_ac1_4_run_json_records_run_metadata(fake_claude, monkeypatch):
    _run_with_stream(fake_claude, monkeypatch)

    record = load_run_record()
    assert record["test_name"] == "capture"
    assert record["backend"] == "claude"
    assert record["model"] == "claude-sonnet-5-5"
    assert record["session_id"] == "sess-basic-1"
    assert record["status"] == "completed"
    assert record["exit_code"] == 0
    assert Path(record["workspace"]).resolve() == fake_claude.cwd.resolve()
    assert record["plugins"] == [{"name": "overpowered", "path": "/plugins/overpowered"}]
    assert record["skills"] == ["overpowered:grilling"]
    start = datetime.fromisoformat(record["start_time"])
    end = datetime.fromisoformat(record["end_time"])
    assert start <= end
    assert record["duration_s"] >= 0


def test_ac1_4_trace_lists_texts_and_tool_calls_in_order(fake_claude, monkeypatch):
    _run_with_stream(fake_claude, monkeypatch)

    items = load_run_record()["trace"]["items"]
    assert items == [
        {"kind": "assistant_text", "text": "Let me look at the repo."},
        {
            "kind": "tool_call",
            "id": "toolu_ls",
            "name": "Bash",
            "input": {"command": "ls"},
            "output": "README.md\n",
            "ok": True,
            "children": [],
        },
        {
            "kind": "tool_call",
            "id": "toolu_cat",
            "name": "Read",
            "input": {"file_path": "missing.txt"},
            "output": "File does not exist.",
            "ok": False,
            "children": [],
        },
        {"kind": "unknown", "raw_type": "stream_event"},
        {"kind": "assistant_text", "text": "Q1: What is the <app> for?"},
    ]


def test_ac1_4_trace_records_usage_and_cost(fake_claude, monkeypatch):
    _run_with_stream(fake_claude, monkeypatch)

    trace = load_run_record()["trace"]
    assert trace["cost_usd"] == 0.0123
    assert trace["usage"] == {
        "input_tokens": 42,
        "output_tokens": 17,
        "cache_read_input_tokens": 0,
    }
    assert trace["num_turns"] == 3


def test_ac1_4_nonzero_exit_is_recorded_as_error(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")

    exit_code = _run_with_stream(fake_claude, monkeypatch)

    record = load_run_record()
    assert record["status"] == "error"
    assert record["exit_code"] == 3
    assert exit_code != 0


def test_ac1_5_raw_stdout_is_saved_byte_for_byte(fake_claude, monkeypatch):
    stream_path = fake_claude.use_stream(monkeypatch, "basic.jsonl")
    test_path = write_test_yaml(Path.cwd(), {"name": "raw", "prompt": PROMPT})

    run_skillbench(str(test_path))

    assert (only_run_dir() / "raw" / "1.jsonl").read_bytes() == stream_path.read_bytes()


def test_ac1_6_subagent_tool_calls_nest_under_parent_tool_call(fake_claude, monkeypatch):
    _run_with_stream(fake_claude, monkeypatch, stream="subagent.jsonl")

    items = load_run_record()["trace"]["items"]
    assert items == [
        {
            "kind": "tool_call",
            "id": "toolu_task",
            "name": "Task",
            "input": {
                "description": "review",
                "prompt": "Review the diff",
                "subagent_type": "reviewer",
            },
            "output": "Review: PASS",
            "ok": True,
            "children": [
                {"kind": "assistant_text", "text": "Searching for TODOs."},
                {
                    "kind": "tool_call",
                    "id": "toolu_grep",
                    "name": "Grep",
                    "input": {"pattern": "TODO"},
                    "output": "no matches",
                    "ok": True,
                    "children": [],
                },
            ],
        },
        {"kind": "assistant_text", "text": "Review passed."},
    ]


def test_ac1_9_timeout_kills_process_and_keeps_partial_trace(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP_S", "60")
    started = time.monotonic()

    exit_code = _run_with_stream(fake_claude, monkeypatch, stream="partial.jsonl", timeout_s=2)

    elapsed_s = time.monotonic() - started
    record = load_run_record()
    assert elapsed_s < 30
    assert exit_code != 0
    assert record["status"] == "timeout"
    assert record["session_id"] == "sess-hang-1"
    assert record["trace"]["items"] == [{"kind": "assistant_text", "text": "Starting a long task."}]


def test_unrecognized_events_and_truncated_lines_are_listed_as_unknown(fake_claude, monkeypatch):
    exit_code = _run_with_stream(fake_claude, monkeypatch, stream="odd.jsonl")

    record = load_run_record()
    assert exit_code == 0
    assert record["session_id"] == "sess-odd-1"
    assert record["trace"]["items"] == [
        {"kind": "unknown", "raw_type": "system.compact_boundary"},
        {"kind": "unknown", "raw_type": "assistant.thinking"},
        {"kind": "assistant_text", "text": "Done thinking."},
        {"kind": "unknown", "raw_type": "user.text"},
        {"kind": "unknown", "raw_type": "unparseable"},
    ]
