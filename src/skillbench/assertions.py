"""Built-in assertions: parsing a test's `assert:` list and checking it against a finished run."""

import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from skillbench.trace import final_text

_SKILL_TOOL = "Skill"
_FILE_CONTAINS_KEYS = frozenset({"path", "text"})
# How much of a failed command's output its reason quotes: enough to see the error, not a log.
_COMMAND_OUTPUT_TAIL_CHARS = 2000


class InvalidAssertionError(Exception):
    """An `assert:` entry is malformed; the message says what is wrong with it."""


@dataclass(frozen=True)
class Assertion:
    type: str
    expected: Any


@dataclass(frozen=True)
class RunFacts:
    """What assertions are checked against."""

    trace: dict[str, Any]
    workspace: Path
    exit_code: int | None
    timed_out: bool
    prompt: str
    loaded_skills: list[Any]
    # The test's max_turns, if the conversation was cut off there with answers still to send.
    exceeded_turn_limit: int | None = None


@dataclass(frozen=True)
class AssertionResult:
    assertion: Assertion
    passed: bool
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.assertion.type,
            "expected": self.assertion.expected,
            "passed": self.passed,
            "reason": self.reason,
        }


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_true(value: Any) -> bool:
    return value is True


def _is_workspace_path(value: Any) -> bool:
    """A relative path that stays inside the workspace (checked lexically, before any run)."""
    if not _is_non_empty_string(value):
        return False
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).anchor:
        return False
    depth = 0
    for part in PureWindowsPath(value).parts:
        if part == "..":
            depth -= 1
        elif part != ".":
            depth += 1
        if depth < 0:
            return False
    return True


def _is_file_contains_spec(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == _FILE_CONTAINS_KEYS
        and _is_workspace_path(value["path"])
        and _is_non_empty_string(value["text"])
    )


def _walk_tool_calls(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every tool call, including those made inside subagents."""
    for item in items:
        if item["kind"] == "tool_call":
            yield item
            yield from _walk_tool_calls(item["children"])


def _count_tool_calls(facts: RunFacts, tool_name: str) -> int:
    return sum(1 for call in _walk_tool_calls(facts.trace["items"]) if call["name"] == tool_name)


def _times(count: int) -> str:
    return "1 time" if count == 1 else f"{count} times"


def _check_skill_invoked(skill_name: str, facts: RunFacts) -> tuple[bool, str]:
    invoked = [
        call["input"].get("skill")
        for call in _walk_tool_calls(facts.trace["items"])
        if call["name"] == _SKILL_TOOL
    ]
    count = invoked.count(skill_name)
    if count:
        return True, f"{skill_name} was invoked {_times(count)}"
    if skill_name in facts.loaded_skills:
        if _is_slash_command_for(facts.prompt, skill_name):
            return True, f"{skill_name} was invoked by the prompt"
        if any(_is_slash_command_for(text, skill_name) for text in _user_message_texts(facts)):
            return True, f"{skill_name} was invoked by a user message"
    others = ", ".join(str(name) for name in invoked) or "none"
    return False, f"{skill_name} was never invoked (skills invoked: {others})"


def _is_slash_command_for(message: str, skill_name: str) -> bool:
    """A message starting `/skill args` runs the skill directly, without a Skill tool call
    (provided the CLI loaded that skill)."""
    command = message.split(maxsplit=1)[0] if message.strip() else ""
    return command == f"/{skill_name}"


def _user_message_texts(facts: RunFacts) -> list[str]:
    return [item["text"] for item in facts.trace["items"] if item["kind"] == "user_message"]


def _tool_call_reason(tool_name: str, count: int) -> str:
    if count == 0:
        return f"{tool_name} was never called"
    return f"{tool_name} was called {_times(count)}"


def _check_tool_called(tool_name: str, facts: RunFacts) -> tuple[bool, str]:
    count = _count_tool_calls(facts, tool_name)
    return count > 0, _tool_call_reason(tool_name, count)


def _check_tool_not_called(tool_name: str, facts: RunFacts) -> tuple[bool, str]:
    count = _count_tool_calls(facts, tool_name)
    return count == 0, _tool_call_reason(tool_name, count)


def _check_file_exists(relative_path: str, facts: RunFacts) -> tuple[bool, str]:
    if (facts.workspace / relative_path).is_file():
        return True, f"{relative_path} exists"
    return False, f"{relative_path} does not exist in the workspace"


def _check_file_contains(spec: dict[str, str], facts: RunFacts) -> tuple[bool, str]:
    relative_path, text = spec["path"], spec["text"]
    file_path = facts.workspace / relative_path
    if not file_path.is_file():
        return False, f"{relative_path} does not exist in the workspace"
    if text in file_path.read_text(encoding="utf-8", errors="replace"):
        return True, f"{relative_path} contains {text!r}"
    return False, f"{relative_path} does not contain {text!r}"


def _check_final_text_contains(text: str, facts: RunFacts) -> tuple[bool, str]:
    agent_final_text = final_text(facts.trace["items"])
    if agent_final_text is None:
        return False, "the agent produced no text"
    if text in agent_final_text:
        return True, f"final text contains {text!r}"
    return False, f"final text does not contain {text!r} (final text: {agent_final_text!r})"


def _check_max_turns(limit: int, facts: RunFacts) -> tuple[bool, str]:
    if facts.exceeded_turn_limit is not None:
        return False, (
            f"the conversation was stopped at the max_turns limit of {facts.exceeded_turn_limit} "
            "with answers still to send"
        )
    turns = facts.trace["num_turns"]
    if turns is None:
        return False, "the turn count was not reported by the CLI"
    if turns > limit:
        return False, f"{turns} turns exceeds the limit of {limit}"
    return True, f"{turns} turns (limit {limit})"


def _check_exit_ok(_expected: bool, facts: RunFacts) -> tuple[bool, str]:
    if facts.timed_out:
        return False, "the CLI timed out"
    return facts.exit_code == 0, f"the CLI exited with code {facts.exit_code}"


def _check_command_succeeds(command: str, facts: RunFacts) -> tuple[bool, str]:
    completed = subprocess.run(
        command,
        shell=True,
        cwd=facts.workspace,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    reason = f"{command!r} exited with code {completed.returncode}"
    if completed.returncode == 0:
        return True, reason
    output_tail = (completed.stdout + completed.stderr).strip()[-_COMMAND_OUTPUT_TAIL_CHARS:]
    return False, f"{reason}; output: {output_tail!r}"


_Checker = Callable[[Any, RunFacts], tuple[bool, str]]

# assertion type -> (value validator, description of the expected value, checker)
_ASSERTION_TYPES: dict[str, tuple[Callable[[Any], bool], str, _Checker]] = {
    "skill_invoked": (_is_non_empty_string, "a skill name", _check_skill_invoked),
    "tool_called": (_is_non_empty_string, "a tool name", _check_tool_called),
    "tool_not_called": (_is_non_empty_string, "a tool name", _check_tool_not_called),
    "file_exists": (
        _is_workspace_path,
        "a relative path inside the workspace",
        _check_file_exists,
    ),
    "file_contains": (
        _is_file_contains_spec,
        "a mapping with 'path' (a relative path inside the workspace) and 'text'",
        _check_file_contains,
    ),
    "final_text_contains": (_is_non_empty_string, "a string", _check_final_text_contains),
    "max_turns": (_is_positive_int, "a positive integer", _check_max_turns),
    "exit_ok": (_is_true, "true", _check_exit_ok),
    "command_succeeds": (
        _is_non_empty_string,
        "a shell command string",
        _check_command_succeeds,
    ),
}


def parse_assertion(entry: Any) -> Assertion:
    """One `assert:` list entry, e.g. `{"tool_called": "Write"}`."""
    if not isinstance(entry, dict) or len(entry) != 1:
        raise InvalidAssertionError(
            f"must be a mapping with exactly one assertion type, got {entry!r}"
        )
    [(assertion_type, expected)] = entry.items()
    if assertion_type not in _ASSERTION_TYPES:
        known = ", ".join(_ASSERTION_TYPES)
        raise InvalidAssertionError(
            f"unknown assertion '{assertion_type}' (known assertions: {known})"
        )
    is_valid, description, _ = _ASSERTION_TYPES[assertion_type]
    if not is_valid(expected):
        raise InvalidAssertionError(f"'{assertion_type}' must be {description}, got {expected!r}")
    return Assertion(type=assertion_type, expected=expected)


def evaluate_assertions(assertions: list[Assertion], facts: RunFacts) -> list[AssertionResult]:
    results = []
    for assertion in assertions:
        _, _, check = _ASSERTION_TYPES[assertion.type]
        passed, reason = check(assertion.expected, facts)
        results.append(AssertionResult(assertion=assertion, passed=passed, reason=reason))
    return results
