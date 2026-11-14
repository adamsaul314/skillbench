"""Normalizing GitHub Copilot CLI `--output-format json` events into skillbench's trace model.

The event names and fields follow Copilot's documentation and have not yet been checked against
a real run. Each event is `{"type": ..., "data": {...}}`; events emitted inside a subagent carry
`data.parentToolCallId`, the tool call that started the subagent.
"""

import json
from collections.abc import Callable
from typing import Any

from skillbench.trace import (
    UNPARSEABLE_LINE,
    TurnInfo,
    build_turn_info,
    empty_trace,
    new_tool_call,
    unknown_item,
)

# Copilot's own tool names are kept, except the skill tool: it takes the name Claude uses so the
# `skill_invoked` assertion reads both backends' traces the same way.
_TOOL_NAME_ALIASES = {"skill": "Skill"}
# Subagent lifecycle events add nothing to the trace: subagent activity is already nested under
# its parent tool call through `parentToolCallId`.
_SUBAGENT_EVENTS = ("subagent.started", "subagent.completed", "subagent.failed")


class _TraceBuilder:
    def __init__(self) -> None:
        self.info = TurnInfo(trace=empty_trace())
        self._tool_calls_by_id: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "session.start": self._add_session_start,
            "assistant.message": self._add_assistant_message,
            "tool.execution_start": self._add_tool_start,
            "tool.execution_complete": self._add_tool_complete,
            "assistant.usage": self._add_usage,
            "session.shutdown": self._add_shutdown,
            **{event_type: _ignore for event_type in _SUBAGENT_EVENTS},
        }

    def add_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type"))
        data = event.get("data")
        data = data if isinstance(data, dict) else {}
        handler = self._handlers.get(event_type)
        if handler is None:
            self._items_under(data.get("parentToolCallId")).append(unknown_item(event_type))
        else:
            handler(data)

    def add_unparseable_line(self) -> None:
        self.info.trace["items"].append(unknown_item(UNPARSEABLE_LINE))

    def _add_session_start(self, data: dict[str, Any]) -> None:
        self.info.session_id = data.get("sessionId")
        self.info.reported_model = data.get("selectedModel")

    def _add_assistant_message(self, data: dict[str, Any]) -> None:
        # Tool requests in the message are skipped: tool.execution_start reports each call.
        text = data.get("content")
        if text:
            items = self._items_under(data.get("parentToolCallId"))
            items.append({"kind": "assistant_text", "text": text})

    def _add_tool_start(self, data: dict[str, Any]) -> None:
        name = str(data.get("toolName"))
        tool_call = new_tool_call(
            str(data.get("toolCallId")),
            _TOOL_NAME_ALIASES.get(name, name),
            data.get("arguments", {}),
        )
        self._tool_calls_by_id[tool_call["id"]] = tool_call
        self._items_under(data.get("parentToolCallId")).append(tool_call)

    def _add_tool_complete(self, data: dict[str, Any]) -> None:
        tool_call = self._tool_calls_by_id.get(str(data.get("toolCallId")))
        if tool_call is None:
            self._items_under(data.get("parentToolCallId")).append(
                unknown_item("tool.execution_complete")
            )
            return
        succeeded = bool(data.get("success"))
        tool_call["ok"] = succeeded
        tool_call["output"] = _text_of(data.get("result") if succeeded else data.get("error"))

    def _add_usage(self, data: dict[str, Any]) -> None:
        trace = self.info.trace
        usage = trace["usage"]
        usage["input_tokens"] = usage.get("input_tokens", 0) + data.get("inputTokens", 0)
        usage["output_tokens"] = usage.get("output_tokens", 0) + data.get("outputTokens", 0)
        trace["num_turns"] = (trace["num_turns"] or 0) + 1
        self.info.reported_model = self.info.reported_model or data.get("model")

    def _add_shutdown(self, data: dict[str, Any]) -> None:
        self.info.trace["premium_requests"] = data.get("totalPremiumRequests")

    def _items_under(self, parent_id: str | None) -> list[dict[str, Any]]:
        parent = self._tool_calls_by_id.get(parent_id) if parent_id else None
        return parent["children"] if parent is not None else self.info.trace["items"]


def _ignore(_data: dict[str, Any]) -> None:
    pass


def _text_of(payload: Any) -> str:
    """A tool result's `content` or an error's `message`; anything else as JSON."""
    if isinstance(payload, dict):
        for key in ("content", "message"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return json.dumps(payload)


def parse_copilot_stream(raw_stdout: bytes) -> TurnInfo:
    """Parse raw JSON-lines stdout. Never raises on odd input; odd lines become `unknown`."""
    return build_turn_info(raw_stdout, _TraceBuilder())
