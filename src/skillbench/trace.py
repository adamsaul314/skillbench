"""The backend-agnostic trace model each backend's event stream is normalized into.

A trace is plain JSON data:
    {"items": [...], "usage": {...}, "cost_usd": float | None,
     "premium_requests": int | None, "num_turns": int | None}
`cost_usd` is what Claude reports; `premium_requests` is what Copilot bills in instead.
Each item is one of
    {"kind": "assistant_text", "text": str}
    {"kind": "tool_call", "id", "name", "input", "output", "ok", "children": [items]}
    {"kind": "unknown", "raw_type": str}
Items emitted inside a subagent go into the `children` of the tool call that spawned it.

A multi-turn run's trace is the turns' traces joined in order, each turn after the first
preceded by the user message that started it:
    {"kind": "user_message", "turn": int, "text": str, "source": "scripted" | "live"}
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

UNPARSEABLE_LINE = "unparseable"


@dataclass
class TurnInfo:
    """Everything recovered from one turn's event stream."""

    trace: dict[str, Any]
    session_id: str | None = None
    reported_model: str | None = None
    plugins: list[Any] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)


def empty_trace() -> dict[str, Any]:
    return {"items": [], "usage": {}, "cost_usd": None, "premium_requests": None, "num_turns": None}


def new_tool_call(call_id: str, name: str, tool_input: Any) -> dict[str, Any]:
    """A tool call item whose result has not arrived yet."""
    return {
        "kind": "tool_call",
        "id": call_id,
        "name": name,
        "input": tool_input,
        "output": None,
        "ok": None,
        "children": [],
    }


def unknown_item(raw_type: str) -> dict[str, Any]:
    return {"kind": "unknown", "raw_type": raw_type}


class StreamTraceBuilder(Protocol):
    """Turns one backend's events into a TurnInfo."""

    info: TurnInfo

    def add_event(self, event: dict[str, Any]) -> None: ...

    def add_unparseable_line(self) -> None: ...


def build_turn_info(raw_stdout: bytes, builder: StreamTraceBuilder) -> TurnInfo:
    """Feed every line of a JSON-lines stream to `builder`. Never raises on odd input: a line
    that isn't a JSON object is reported as unparseable."""
    for line in raw_stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        if isinstance(event, dict):
            builder.add_event(event)
        else:
            builder.add_unparseable_line()
    return builder.info


@dataclass(frozen=True)
class UserMessage:
    """A message sent to the agent to start a follow-up turn."""

    text: str
    source: str  # "scripted" (from the test's answers:) or "live" (typed in the terminal)


@dataclass(frozen=True)
class TurnTrace:
    """One turn's trace and the user message that started it (None for the first turn)."""

    user_message: UserMessage | None
    trace: dict[str, Any]


def final_text(items: list[dict[str, Any]]) -> str | None:
    """The last top-level assistant text: what the agent ended on."""
    texts = [item["text"] for item in items if item["kind"] == "assistant_text"]
    return texts[-1] if texts else None


def merge_turn_traces(turns: list[TurnTrace]) -> dict[str, Any]:
    """One trace for the whole conversation: items in order, usage/cost/turn counts summed."""
    items: list[dict[str, Any]] = []
    for turn_number, turn in enumerate(turns, start=1):
        if turn.user_message is not None:
            items.append(
                {
                    "kind": "user_message",
                    "turn": turn_number,
                    "text": turn.user_message.text,
                    "source": turn.user_message.source,
                }
            )
        items += turn.trace["items"]
    traces = [turn.trace for turn in turns]
    return {
        "items": items,
        "usage": _sum_usage([trace["usage"] for trace in traces]),
        "cost_usd": _sum_known([trace["cost_usd"] for trace in traces]),
        "premium_requests": _sum_known([trace["premium_requests"] for trace in traces]),
        "num_turns": _sum_known([trace["num_turns"] for trace in traces]),
    }


def _sum_known(values: list[Any]) -> Any:
    """Sum of the values that were reported; None if no turn reported one."""
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _sum_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    """Numeric counters add up across turns; anything else keeps the latest turn's value."""
    total: dict[str, Any] = {}
    for usage in usages:
        for key, value in usage.items():
            is_counter = isinstance(value, int | float) and not isinstance(value, bool)
            if is_counter and isinstance(total.get(key, 0), int | float):
                total[key] = total.get(key, 0) + value
            else:
                total[key] = value
    return total
