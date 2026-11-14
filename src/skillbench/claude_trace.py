"""Normalizing Claude Code `stream-json` output into skillbench's trace model.

A trace is plain JSON data:
    {"items": [...], "usage": {...}, "cost_usd": float | None, "num_turns": int | None}
Each item is one of
    {"kind": "assistant_text", "text": str}
    {"kind": "tool_call", "id", "name", "input", "output", "ok", "children": [items]}
    {"kind": "unknown", "raw_type": str}
Items emitted inside a subagent go into the `children` of the tool call that spawned it.
"""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClaudeRunInfo:
    """Everything recovered from one turn's event stream."""

    trace: dict[str, Any]
    session_id: str | None = None
    reported_model: str | None = None
    plugins: list[Any] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)


class _TraceBuilder:
    def __init__(self) -> None:
        self.info = ClaudeRunInfo(
            trace={"items": [], "usage": {}, "cost_usd": None, "num_turns": None}
        )
        self._tool_calls_by_id: dict[str, dict[str, Any]] = {}

    def add_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "system" and event.get("subtype") == "init":
            self._add_init(event)
        elif event_type == "assistant":
            self._add_assistant(event)
        elif event_type == "user":
            self._add_user(event)
        elif event_type == "result":
            self._add_result(event)
        elif event_type == "system":
            self._add_unknown(None, f"system.{event.get('subtype')}")
        else:
            self._add_unknown(None, str(event_type))

    def add_unparseable_line(self) -> None:
        self._add_unknown(None, "unparseable")

    def _add_init(self, event: dict[str, Any]) -> None:
        self.info.session_id = event.get("session_id")
        self.info.reported_model = event.get("model")
        self.info.plugins = event.get("plugins", [])
        self.info.skills = event.get("skills", [])

    def _add_assistant(self, event: dict[str, Any]) -> None:
        parent_id = event.get("parent_tool_use_id")
        for block in event.get("message", {}).get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                self._items_under(parent_id).append(
                    {"kind": "assistant_text", "text": block["text"]}
                )
            elif block_type == "tool_use":
                self._add_tool_call(parent_id, block)
            else:
                self._add_unknown(parent_id, f"assistant.{block_type}")

    def _add_tool_call(self, parent_id: str | None, block: dict[str, Any]) -> None:
        tool_call = {
            "kind": "tool_call",
            "id": block["id"],
            "name": block["name"],
            "input": block.get("input", {}),
            "output": None,
            "ok": None,
            "children": [],
        }
        self._tool_calls_by_id[block["id"]] = tool_call
        self._items_under(parent_id).append(tool_call)

    def _add_user(self, event: dict[str, Any]) -> None:
        parent_id = event.get("parent_tool_use_id")
        content = event.get("message", {}).get("content", [])
        if isinstance(content, str):
            self._add_unknown(parent_id, "user.text")
            return
        for block in content:
            tool_call = self._tool_calls_by_id.get(block.get("tool_use_id", ""))
            if block.get("type") == "tool_result" and tool_call is not None:
                tool_call["output"] = _tool_result_text(block.get("content"))
                tool_call["ok"] = not block.get("is_error", False)
            else:
                self._add_unknown(parent_id, f"user.{block.get('type')}")

    def _add_result(self, event: dict[str, Any]) -> None:
        self.info.session_id = event.get("session_id", self.info.session_id)
        trace = self.info.trace
        trace["usage"] = event.get("usage", {})
        trace["cost_usd"] = event.get("total_cost_usd")
        trace["num_turns"] = event.get("num_turns")

    def _add_unknown(self, parent_id: str | None, raw_type: str) -> None:
        self._items_under(parent_id).append({"kind": "unknown", "raw_type": raw_type})

    def _items_under(self, parent_id: str | None) -> list[dict[str, Any]]:
        parent = self._tool_calls_by_id.get(parent_id) if parent_id else None
        return parent["children"] if parent is not None else self.info.trace["items"]


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") == "text" for block in content
    ):
        return "".join(block["text"] for block in content)
    return json.dumps(content)


def parse_claude_stream(raw_stdout: bytes) -> ClaudeRunInfo:
    """Parse raw stream-json stdout. Never raises on odd input; odd lines become `unknown`."""
    builder = _TraceBuilder()
    for line in raw_stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            builder.add_unparseable_line()
            continue
        if isinstance(event, dict):
            builder.add_event(event)
        else:
            builder.add_unparseable_line()
    return builder.info
