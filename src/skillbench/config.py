"""Loading and validating test YAML files into a `TestSpec`."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from skillbench.assertions import Assertion, InvalidAssertionError, parse_assertion
from skillbench.workspace import Fixture, GitFixture, is_git_url

if TYPE_CHECKING:
    from skillbench.project import Project

_DEFAULT_TIMEOUT_S = 600
_TRIGGER_KEYS = ("should", "should_not")
_GIT_FIXTURE_KEYS = frozenset({"git", "ref"})
# The agent CLIs skillbench can drive, primary first: the default when both are installed.
BACKEND_NAMES = ("claude", "copilot")


class ConfigError(Exception):
    """A test file or skillbench.yaml is unreadable or invalid; the message names the field."""


@dataclass(frozen=True)
class TriggerCase:
    """One prompt of a trigger test, and whether the skill should fire for it."""

    prompt: str
    should_invoke: bool


@dataclass(frozen=True)
class TestSpec:
    """A validated test."""

    source_path: Path
    name: str
    prompt: str
    fixture: Fixture | None = None
    model: str | None = None
    timeout_s: float = _DEFAULT_TIMEOUT_S
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    is_interactive: bool = False
    max_turns: int | None = None  # limit on conversation turns (the prompt plus each answer)
    assertions: list[Assertion] = field(default_factory=list)
    backends: list[str] = field(default_factory=lambda: list(BACKEND_NAMES))
    skill: str | None = None  # `skill:`, found in the workspace at run time
    skill_input: str = ""  # `input:`, the text after the skill's /command
    triggers: list[TriggerCase] = field(default_factory=list)
    trigger: TriggerCase | None = None  # set on the run of one trigger prompt
    repeat: int = 1
    min_pass_rate: float = 1.0
    kind: str | None = None  # "skill" or "agent": which tests folder the file is in
    # The skillbench folder: never copied into a workspace made from the repo around it.
    fixture_exclude: Path | None = None
    # Plugins found in the workspace, set at run time so the agent loads all of them.
    plugin_dirs: list[Path] = field(default_factory=list)

    def runs_on(self, backend_name: str) -> bool:
        return backend_name in self.backends

    @property
    def has_forced_run(self) -> bool:
        """False for a trigger-only skill test: one with triggers but no `input:`."""
        return not self.triggers or bool(self.skill_input)


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _is_rate(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def _is_backend_list(value: Any) -> bool:
    return _is_string_list(value) and all(name in BACKEND_NAMES for name in value)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _is_fixture(value: Any) -> bool:
    if isinstance(value, str):
        return value != ""
    return (
        isinstance(value, dict)
        and "git" in value
        and set(value) <= _GIT_FIXTURE_KEYS
        and all(_is_non_empty_string(item) for item in value.values())
    )


def _is_triggers(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) <= set(_TRIGGER_KEYS)
        and all(
            isinstance(prompts, list) and all(map(_is_non_empty_string, prompts))
            for prompts in value.values()
        )
        and any(value.values())
    )


_Rule = tuple[Callable[[Any], bool], str]

# Fields a test and skillbench.yaml share: YAML key -> (validator, expected value).
DEFAULT_RULES: dict[str, _Rule] = {
    "fixture": (_is_fixture, "a path, a git URL, or {git: <url>, ref: <branch|tag|commit>}"),
    "model": (_is_string, "a string"),
    "timeout_s": (_is_positive_number, "a positive number"),
    "repeat": (_is_positive_int, "a positive integer"),
    "min_pass_rate": (_is_rate, "a number from 0 to 1"),
}
_FIELD_RULES: dict[str, _Rule] = {
    "name": (_is_string, "a string"),
    "prompt": (_is_string, "a string"),
    "skill": (_is_non_empty_string, "a skill name or a path to it"),
    "input": (_is_string, "a string"),
    "triggers": (_is_triggers, "a mapping with 'should' and/or 'should_not' lists of prompts"),
    **DEFAULT_RULES,
    "backends": (_is_backend_list, f"a list of backend names ({', '.join(BACKEND_NAMES)})"),
    "answers": (_is_string_list, "a list of strings"),
    "interactive": (_is_bool, "true or false"),
    "max_turns": (_is_positive_int, "a positive integer"),
    "allowed_tools": (_is_string_list, "a list of strings"),
    "denied_tools": (_is_string_list, "a list of strings"),
    "assert": (_is_list, "a list"),
}


def read_yaml_mapping(path: Path, what: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"{path}: cannot read file ({error.strerror})") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: not valid YAML ({error})") from error
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(f"{path}: top level must be a mapping of {what}")
    return parsed


def validate_fields(path: Path, raw: dict[str, Any], rules: dict[str, _Rule]) -> None:
    for key, value in raw.items():
        if key not in rules:
            raise ConfigError(f"{path}: unknown field '{key}' (known fields: {', '.join(rules)})")
        is_valid, expected = rules[key]
        if not is_valid(value):
            raise ConfigError(f"{path}: field '{key}' must be {expected}, got {value!r}")


def load_test(test_path: Path, project: "Project") -> TestSpec:
    """Read and validate one test file. Raises ConfigError naming the file and field. Fields
    the test leaves out come from skillbench.yaml; `fixture:` paths start at the repo root."""
    raw = read_yaml_mapping(test_path, "test fields")
    validate_fields(test_path, raw, _FIELD_RULES)
    _validate_combination(test_path, raw)
    merged = {**project.defaults, **raw}
    return TestSpec(
        source_path=test_path,
        name=raw.get("name", test_path.stem),
        prompt=raw.get("prompt", ""),
        fixture=_resolve_fixture(test_path, merged.get("fixture"), project.repo_root),
        model=merged.get("model"),
        timeout_s=merged.get("timeout_s", _DEFAULT_TIMEOUT_S),
        allowed_tools=raw.get("allowed_tools", []),
        denied_tools=raw.get("denied_tools", []),
        answers=raw.get("answers", []),
        is_interactive=raw.get("interactive", False),
        max_turns=raw.get("max_turns"),
        assertions=_parse_assertions(test_path, raw.get("assert", [])),
        backends=raw.get("backends", list(BACKEND_NAMES)),
        skill=raw.get("skill"),
        skill_input=raw.get("input", ""),
        triggers=_trigger_cases(raw.get("triggers", {})),
        repeat=merged.get("repeat", 1),
        min_pass_rate=merged.get("min_pass_rate", 1.0),
        kind=project.kind_of(test_path),
        fixture_exclude=project.directory,
    )


def _validate_combination(test_path: Path, raw: dict[str, Any]) -> None:
    """A test either invokes a skill or sends a prompt."""
    if "skill" in raw:
        if "prompt" in raw:
            raise ConfigError(
                f"{test_path}: use 'input' with 'skill' (skillbench builds the /command); "
                "'prompt' is for tests that don't force a skill"
            )
        return
    for key in ("input", "triggers"):
        if key in raw:
            raise ConfigError(f"{test_path}: field '{key}' needs 'skill' (the skill to test)")
    if "prompt" not in raw:
        raise ConfigError(f"{test_path}: missing required field 'prompt' (or 'skill')")


def _resolve_fixture(test_path: Path, raw_fixture: Any, base: Path) -> Fixture | None:
    if raw_fixture is None:
        return None
    if isinstance(raw_fixture, dict):
        return GitFixture(url=raw_fixture["git"], ref=raw_fixture.get("ref"))
    local = (base / raw_fixture).resolve()
    is_bare_repo = (local / "HEAD").is_file() and (local / "objects").is_dir()
    if is_bare_repo:
        return GitFixture(url=str(local))
    if local.is_dir():
        return local
    if is_git_url(raw_fixture):
        return GitFixture(url=raw_fixture)
    raise ConfigError(f"{test_path}: field 'fixture' directory not found: {local}")


def _trigger_cases(raw_triggers: dict[str, list[str]]) -> list[TriggerCase]:
    return [
        TriggerCase(prompt=prompt, should_invoke=key == "should")
        for key in _TRIGGER_KEYS
        for prompt in raw_triggers.get(key, [])
    ]


def _parse_assertions(test_path: Path, entries: list[Any]) -> list[Assertion]:
    assertions = []
    for index, entry in enumerate(entries):
        try:
            assertions.append(parse_assertion(entry))
        except InvalidAssertionError as error:
            raise ConfigError(f"{test_path}: field 'assert[{index}]' {error}") from error
    return assertions
