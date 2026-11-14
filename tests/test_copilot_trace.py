"""Odd Copilot event streams still parse into a trace instead of crashing."""

import json

from skillbench.copilot_trace import parse_copilot_stream


def _stream(*events: dict) -> bytes:
    return "".join(json.dumps(event) + "\n" for event in events).encode()


def test_ac5_2_a_result_for_an_unknown_tool_call_is_kept_as_unknown():
    info = parse_copilot_stream(
        _stream(
            {"type": "tool.execution_complete", "data": {"toolCallId": "nope", "success": True}}
        )
    )

    assert info.trace["items"] == [{"kind": "unknown", "raw_type": "tool.execution_complete"}]


def test_ac5_2_a_result_without_text_content_is_recorded_as_json():
    info = parse_copilot_stream(
        _stream(
            {"type": "tool.execution_start", "data": {"toolCallId": "c1", "toolName": "bash"}},
            {
                "type": "tool.execution_complete",
                "data": {"toolCallId": "c1", "success": True, "result": {"lines": 3}},
            },
        )
    )

    [tool_call] = info.trace["items"]
    assert tool_call["output"] == '{"lines": 3}'
    assert tool_call["ok"] is True


def test_ac5_2_an_event_without_a_data_object_does_not_crash():
    info = parse_copilot_stream(_stream({"type": "assistant.message", "data": "oops"}))

    assert info.trace["items"] == []
