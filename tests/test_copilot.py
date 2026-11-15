"""The Copilot backend: how `copilot` is launched and how its events become a trace."""

import uuid
from pathlib import Path

from skillbench.dashboard import INDEX_FILE_NAME

from conftest import load_run_record, only_run_dir, run_skillbench, runs_dir, write_test_yaml

PROMPT = "/overpowered:grilling an idea for a todo app"


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _run_on_copilot(spec: dict, *extra_args: str) -> int:
    test_path = write_test_yaml(Path.cwd(), {"name": "cop", "prompt": PROMPT, **spec})
    return run_skillbench(str(test_path), "--copilot", *extra_args)


def test_ac5_1_launches_copilot_print_mode_with_json_output_and_full_access(fake_agents):
    assert _run_on_copilot({}) == 0

    argv = fake_agents.copilot.argv
    assert argv[:2] == ["-p", PROMPT]
    assert _flag_value(argv, "--output-format") == "json"
    assert "--allow-all" in argv
    assert "--no-ask-user" in argv
    assert "--no-custom-instructions" in argv
    assert fake_agents.copilot.cwd == Path(load_run_record()["workspace"])
    assert not fake_agents.claude.was_launched


def test_ac5_1_follow_up_turns_continue_one_session_by_id(fake_agents, monkeypatch):
    # The stream never reports a session id, so the id must come from skillbench itself.
    fake_agents.copilot.use_stream(monkeypatch, "copilot_no_session.jsonl")

    assert _run_on_copilot({"answers": ["a todo app"]}) == 0

    first, second = (call["argv"] for call in fake_agents.copilot.invocations)
    session_id = _flag_value(second, "--session-id")
    assert uuid.UUID(session_id)
    assert _flag_value(first, "--session-id") == session_id
    assert second[:2] == ["-p", "a todo app"]
    assert load_run_record()["session_id"] == session_id


def test_ac5_2_copilot_events_normalize_into_the_trace(fake_agents):
    _run_on_copilot({})

    record = load_run_record()
    assert record["backend"] == "copilot"
    assert record["session_id"] == "cop-sess-1"
    assert record["model"] == "gpt-5"
    ls_call, task_call, skill_call = (
        item for item in record["trace"]["items"] if item["kind"] == "tool_call"
    )
    assert [item["kind"] for item in record["trace"]["items"]] == [
        "assistant_text",
        "tool_call",
        "tool_call",
        "tool_call",
        "unknown",
        "assistant_text",
        "unknown",
    ]
    assert (ls_call["name"], ls_call["input"], ls_call["output"], ls_call["ok"]) == (
        "bash",
        {"command": "ls"},
        "README.md\n",
        True,
    )
    assert (task_call["name"], task_call["output"], task_call["ok"]) == (
        "task",
        "a.py is missing.",
        True,
    )
    view_call, subagent_text = task_call["children"]
    assert (view_call["name"], view_call["output"], view_call["ok"]) == (
        "view",
        "Path does not exist: a.py",
        False,
    )
    assert subagent_text == {"kind": "assistant_text", "text": "a.py is missing."}
    assert skill_call["input"] == {"skill": "overpowered:grilling"}


def test_ac5_2_usage_reports_premium_requests_and_tokens_not_usd(fake_agents):
    _run_on_copilot({})

    trace = load_run_record()["trace"]
    assert trace["premium_requests"] == 2
    assert trace["cost_usd"] is None
    assert trace["usage"] == {"input_tokens": 250, "output_tokens": 50}
    assert trace["num_turns"] == 2


def test_ac5_2_premium_requests_add_up_across_turns(fake_agents, monkeypatch):
    fake_agents.copilot.use_streams(monkeypatch, "copilot_basic.jsonl", "copilot_turn2.jsonl")

    _run_on_copilot({"answers": ["a todo app"]})

    trace = load_run_record()["trace"]
    assert trace["premium_requests"] == 3
    assert trace["usage"] == {"input_tokens": 290, "output_tokens": 55}


def test_ac5_2_assertions_work_unchanged_on_a_copilot_trace(fake_agents):
    exit_code = _run_on_copilot(
        {
            "assert": [
                {"tool_called": "view"},
                {"skill_invoked": "overpowered:grilling"},
                {"final_text_contains": "Q1"},
                {"tool_not_called": "edit"},
            ]
        }
    )

    record = load_run_record()
    assert [result["passed"] for result in record["assertions"]] == [True, True, True, True]
    assert record["status"] == "pass"
    assert exit_code == 0


def test_ac5_2_dashboard_shows_premium_requests_instead_of_dollars(fake_agents):
    _run_on_copilot({})

    html = (runs_dir() / INDEX_FILE_NAME).read_text(encoding="utf-8")
    rows = (chunk.split("</tr>")[0] for chunk in html.split("<tr>"))
    row = next(row for row in rows if only_run_dir().name in row)
    assert "2 premium requests" in row
    assert "$" not in row


def test_ac5_1_denied_tools_are_passed_as_deny_tool(fake_agents):
    _run_on_copilot({"denied_tools": ["shell(rm)", "write"]})

    argv = fake_agents.copilot.argv
    denied = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--deny-tool"]
    assert denied == ["shell(rm)", "write"]
