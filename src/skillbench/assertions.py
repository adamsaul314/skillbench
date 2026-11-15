"""Built-in assertions: parsing a test's `assert:` list and checking it against a finished run."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from skillbench.claude import SKILL_TOOL, final_text, walk_tool_calls
from skillbench.workspace import run_shell

# How much of a failed command's output its reason quotes: enough to see the error, not a log.
_COMMAND_OUTPUT_TAIL_CHARS = 2000
_TOOL_ORDER_SHOWN_CALLS = 20
_COMMAND_TIMEOUT_S = 1800


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
    prompt: str
    loaded_skills: list[Any]

    @property
    def tool_names(self) -> list[str]:
        """Every tool call's name in order, subagents included."""
        return [call["name"] for call in walk_tool_calls(self.trace["items"])]

    @property
    def invoked_skills(self) -> list[Any]:
        """The skill named by each Skill tool call, in order."""
        return [
            call["input"].get("skill") if isinstance(call["input"], dict) else None
            for call in walk_tool_calls(self.trace["items"])
            if call["name"] == SKILL_TOOL
        ]

    @property
    def invoked_subagents(self) -> list[Any]:
        """The agent type each subagent call asked for (`subagent_type`), in order."""
        return [
            call["input"]["subagent_type"]
            for call in walk_tool_calls(self.trace["items"])
            if isinstance(call["input"], dict) and "subagent_type" in call["input"]
        ]

    @property
    def user_messages(self) -> list[str]:
        """The prompt, then each answer sent."""
        answers = [item["text"] for item in self.trace["items"] if item["kind"] == "user_message"]
        return [self.prompt, *answers]


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


def parse_assertion(entry: Any) -> Assertion:
    """One `assert:` list entry, e.g. `{"tool_not_called": "Write"}`."""
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


# --- checks: each returns (passed, reason) -------------------------------------------------

_Check = Callable[[Any, RunFacts], tuple[bool, str]]


def _negated(check: _Check) -> _Check:
    """The opposite check, with the same reason."""

    def negated_check(expected: Any, facts: RunFacts) -> tuple[bool, str]:
        passed, reason = check(expected, facts)
        return not passed, reason

    return negated_check


def _check_skill_invoked(skill_name: str, facts: RunFacts) -> tuple[bool, str]:
    """Through the Skill tool, or by a `/skill` prompt or answer when Claude loaded it."""
    count = sum(1 for name in facts.invoked_skills if _skill_name_matches(name, skill_name))
    if count:
        return True, f"{skill_name} was invoked {_times(count)}"
    if any(_skill_name_matches(name, skill_name) for name in facts.loaded_skills):
        for index, message in enumerate(facts.user_messages):
            if _is_slash_command_for(message, skill_name):
                by = "the prompt" if index == 0 else "a user message"
                return True, f"{skill_name} was invoked by {by}"
    others = ", ".join(str(name) for name in facts.invoked_skills) or "none"
    return False, f"{skill_name} was never invoked (skills invoked: {others})"


def _check_subagent_invoked(agent_name: str, facts: RunFacts) -> tuple[bool, str]:
    invoked = facts.invoked_subagents
    count = sum(1 for name in invoked if _skill_name_matches(name, agent_name))
    if count:
        return True, f"the {agent_name} subagent ran {_times(count)}"
    others = ", ".join(str(name) for name in invoked) or "none"
    return False, f"the {agent_name} subagent never ran (subagents run: {others})"


def _skill_name_matches(actual: Any, expected: str) -> bool:
    """`planner` matches both `planner` and a plugin's `my-plugin:planner` (skills and agents
    alike)."""
    if not isinstance(actual, str):
        return False
    return actual == expected or (":" not in expected and actual.rsplit(":", 1)[-1] == expected)


def _is_slash_command_for(message: str, skill_name: str) -> bool:
    command = message.split(maxsplit=1)[0] if message.strip() else ""
    return command.startswith("/") and _skill_name_matches(command[1:], skill_name)


def _check_tool_called(tool_name: str, facts: RunFacts) -> tuple[bool, str]:
    count = facts.tool_names.count(tool_name)
    if count == 0:
        return False, f"{tool_name} was never called"
    return True, f"{tool_name} was called {_times(count)}"


def _check_tool_order(expected: list[str], facts: RunFacts) -> tuple[bool, str]:
    """The tools were called in this order; other calls may come in between."""
    called = facts.tool_names
    matched = 0
    for name in called:
        if matched < len(expected) and name == expected[matched]:
            matched += 1
    if matched == len(expected):
        return True, f"called in order: {' -> '.join(expected)}"
    shown = ", ".join(called[:_TOOL_ORDER_SHOWN_CALLS]) or "none"
    if len(called) > _TOOL_ORDER_SHOWN_CALLS:
        shown += f", ... ({len(called)} calls)"
    after = " -> ".join(expected[:matched]) or "nothing"
    return False, f"{expected[matched]} was not called after {after} (calls in order: {shown})"


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


def _check_command_succeeds(command: str, facts: RunFacts) -> tuple[bool, str]:
    result = run_shell(command, facts.workspace, _COMMAND_TIMEOUT_S, _COMMAND_OUTPUT_TAIL_CHARS)
    reason = f"{command!r} exited with code {result.exit_code}"
    if result.succeeded:
        return True, reason
    return False, f"{reason}; output: {result.output!r}"


def _times(count: int) -> str:
    return "1 time" if count == 1 else f"{count} times"


# --- validators for the expected value -----------------------------------------------------


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _is_non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(map(_is_non_empty_string, value))


def _is_workspace_path(value: Any) -> bool:
    """A relative path that stays inside the workspace (checked lexically, before any run)."""
    if not _is_non_empty_string(value):
        return False
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).anchor:
        return False
    depth = 0
    for part in PureWindowsPath(value).parts:
        depth += -1 if part == ".." else 0 if part == "." else 1
        if depth < 0:
            return False
    return True


def _is_file_contains_spec(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == {"path", "text"}
        and _is_workspace_path(value["path"])
        and _is_non_empty_string(value["text"])
    )


# assertion type -> (validator of the expected value, its description, check)
_ASSERTION_TYPES: dict[str, tuple[Callable[[Any], bool], str, _Check]] = {
    "skill_invoked": (_is_non_empty_string, "a skill name", _check_skill_invoked),
    "skill_not_invoked": (_is_non_empty_string, "a skill name", _negated(_check_skill_invoked)),
    "subagent_invoked": (_is_non_empty_string, "an agent name", _check_subagent_invoked),
    "tool_not_called": (_is_non_empty_string, "a tool name", _negated(_check_tool_called)),
    "tool_order": (_is_non_empty_string_list, "a non-empty list of tool names", _check_tool_order),
    "final_text_contains": (_is_non_empty_string, "a string", _check_final_text_contains),
    "file_contains": (
        _is_file_contains_spec,
        "a mapping with 'path' (a relative path inside the workspace) and 'text'",
        _check_file_contains,
    ),
    "command_succeeds": (_is_non_empty_string, "a command string", _check_command_succeeds),
}
