"""Backend-agnostic parts of the trace model (see claude_trace for the item shapes).

A multi-turn run's trace is the turns' traces joined in order, each turn after the first
preceded by the user message that started it:
    {"kind": "user_message", "turn": int, "text": str, "source": "scripted" | "live"}
"""

from dataclasses import dataclass
from typing import Any


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
