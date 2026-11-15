"""Claude Code: finding the CLI, the command line for each turn, and reading its `stream-json`
output into a trace.

A trace is plain JSON data:
    {"items": [...], "usage": {...}, "cost_usd": float | None, "num_turns": int | None}
Each item is one of
    {"kind": "assistant_text", "text": str}
    {"kind": "tool_call", "id", "name", "input", "output", "ok", "children": [items]}
    {"kind": "user_message", "turn": int, "text": str, "source": "scripted" | "live"}
    {"kind": "user_command", "command": str, "exit_code": int, "output": str}
    {"kind": "new_session"}
    {"kind": "unknown", "raw_type": str}
Items emitted inside a subagent go into the `children` of the tool call that spawned it. A
multi-turn run's trace is the turns' traces joined in order, each turn after the first preceded
by the user message that started it.
"""

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKILL_TOOL = "Skill"
_INSTALL_HINT = "install Claude Code: npm install -g @anthropic-ai/claude-code"
# A healthy CLI prints its version in well under this; a hung stub must not stall the run.
_VERSION_CHECK_TIMEOUT_S = 30
# Asking the user mid-turn would block a headless run; skillbench answers between turns instead.
_ALWAYS_DENIED_TOOLS = ("AskUserQuestion",)
_EMPTY_MCP_CONFIG = json.dumps({"mcpServers": {}})


class ClaudeNotFoundError(Exception):
    """`claude` is not on PATH, or is installed but won't run."""


@dataclass
class TurnInfo:
    """Everything recovered from one turn's output."""

    trace: dict[str, Any]
    session_id: str | None = None
    reported_model: str | None = None
    plugins: list[Any] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class UserMessage:
    """A message sent to the agent to start a follow-up turn."""

    text: str
    source: str  # "scripted" (from the test's answers:) or "live" (typed in the terminal)


def find_claude() -> str:
    """The path of a `claude` that answers `--version`. Raises ClaudeNotFoundError."""
    executable = shutil.which("claude")
    if executable is None:
        raise ClaudeNotFoundError(f"claude is not on PATH ({_INSTALL_HINT})")
    try:
        check = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_VERSION_CHECK_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClaudeNotFoundError(f"{executable} does not run ({error})") from error
    if check.returncode != 0:
        raise ClaudeNotFoundError(f"{executable} --version failed ({_INSTALL_HINT})")
    return executable


def turn_command(
    executable: str,
    message: str,
    session_id: str | None,
    model: str | None,
    plugin_dirs: list[Path],
) -> list[str]:
    """The full command for one turn. The message comes first: it is positional, and the
    list-valued flags (--disallowedTools, ...) would otherwise swallow it."""
    command = [executable, "-p", message]
    if session_id is not None:
        command += ["--resume", session_id]
    command += ["--output-format", "stream-json", "--verbose"]
    command += ["--permission-mode", "bypassPermissions"]
    command += ["--disallowedTools", *_ALWAYS_DENIED_TOOLS]
    if model is not None:
        command += ["--model", model]
    command += _ignore_user_config_args()
    for plugin_dir in plugin_dirs:
        command += ["--plugin-dir", str(plugin_dir)]
    return command


def _ignore_user_config_args() -> list[str]:
    """--bare needs an API key; with a subscription login, keep only project settings and no
    MCP servers instead."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ["--bare"]
    return [
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        _EMPTY_MCP_CONFIG,
    ]


def parse_stream(raw_stdout: bytes) -> TurnInfo:
    """Parse raw stream-json stdout. Never raises on odd input; odd lines become `unknown`."""
    builder = _TraceBuilder()
    for line in raw_stdout.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            builder.add_line(line)
    return builder.info


def empty_trace() -> dict[str, Any]:
    return {"items": [], "usage": {}, "cost_usd": None, "num_turns": None}


def walk_tool_calls(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every tool call in order, including those made inside subagents."""
    for item in items:
        if item["kind"] == "tool_call":
            yield item
            yield from walk_tool_calls(item["children"])


def final_text(items: list[dict[str, Any]]) -> str | None:
    """The last top-level assistant text: what the agent ended on."""
    texts = [item["text"] for item in items if item["kind"] == "assistant_text"]
    return texts[-1] if texts else None


def merge_turns(
    turns: list[tuple[list[dict[str, Any]], UserMessage | None, dict[str, Any]]],
) -> dict[str, Any]:
    """One trace for the whole conversation from each turn's (items before it, starting message,
    trace): items in order, usage, cost and turn counts summed. The items before a turn are the
    user's own actions between turns (commands run, a fresh session started)."""
    items: list[dict[str, Any]] = []
    for turn_number, (preface, message, trace) in enumerate(turns, start=1):
        items += preface
        if message is not None:
            items.append({"kind": "user_message", "turn": turn_number, **vars(message)})
        items += trace["items"]
    traces = [trace for _, _, trace in turns]
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


class _TraceBuilder:
    """Builds a TurnInfo from stream-json events, nesting subagent events under their parent."""

    def __init__(self) -> None:
        self.info = TurnInfo(trace=empty_trace())
        self._tool_calls_by_id: dict[str, dict[str, Any]] = {}

    def add_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        if isinstance(event, dict):
            self._add_event(event)
        else:
            self._add_unknown(None, "unparseable")

    def _add_event(self, event: dict[str, Any]) -> None:
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
                text_item = {"kind": "assistant_text", "text": block["text"]}
                self._items_under(parent_id).append(text_item)
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
    is_all_text = isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") == "text" for block in content
    )
    return "".join(block["text"] for block in content) if is_all_text else json.dumps(content)
