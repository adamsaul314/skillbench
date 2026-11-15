"""The skillbench/ project folder and its defaults, and loading test YAML files into `TestSpec`s."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from skillbench.assertions import Assertion, InvalidAssertionError, parse_assertion
from skillbench.workspace import Fixture, GitFixture, is_git_url

PROJECT_DIR_NAME = "skillbench"
CONFIG_FILE_NAME = "skillbench.yaml"
TEST_FOLDERS = {"skill": "skills", "agent": "agent"}  # test kind -> its folder in the project
_DEFAULT_TIMEOUT_S = 600
_TRIGGER_KEYS = ("should", "should_not")


class ConfigError(Exception):
    """A test file or skillbench.yaml is unreadable or invalid; the message names the field."""


@dataclass(frozen=True)
class Project:
    directory: Path  # the skillbench/ folder
    defaults: dict[str, Any]  # skillbench.yaml, as written

    @property
    def repo_root(self) -> Path:
        """The repo the project folder lives in; `skill:` and `fixture:` paths start here."""
        return self.directory.parent

    @property
    def runs_dir(self) -> Path:
        return self.directory / "runs"

    def tests_dir(self, kind: str) -> Path:
        return self.directory / TEST_FOLDERS[kind]


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
    kind: str  # "skill" or "agent": which tests folder the file is in
    fixture: Fixture | None = None
    model: str | None = None  # from skillbench.yaml; --model overrides it
    timeout_s: float = _DEFAULT_TIMEOUT_S
    answers: list[str] = field(default_factory=list)
    is_interactive: bool = False
    assertions: list[Assertion] = field(default_factory=list)
    skill: str | None = None  # `skill:`, found in the workspace at run time
    skill_input: str = ""  # `input:`, the text after the skill's /command
    triggers: list[TriggerCase] = field(default_factory=list)
    trigger: TriggerCase | None = None  # set on the run of one trigger prompt
    repeat: int = 1
    min_pass_rate: float = 1.0
    # The skillbench folder: never copied into a workspace made from the repo around it.
    fixture_exclude: Path | None = None
    # Plugins found in the workspace, set at run time so the agent loads all of them.
    plugin_dirs: list[Path] = field(default_factory=list)

    @property
    def has_forced_run(self) -> bool:
        """False for a trigger-only skill test: one with triggers but no `input:`."""
        return not self.triggers or bool(self.skill_input)


def find_project(start: Path) -> Project | None:
    """The nearest project folder at or above `start`, like git finds `.git`. Raises
    ConfigError if its skillbench.yaml is invalid."""
    for directory in [start, *start.parents]:
        for candidate in (directory / PROJECT_DIR_NAME, directory):
            config_path = candidate / CONFIG_FILE_NAME
            if candidate.name == PROJECT_DIR_NAME and config_path.is_file():
                defaults = _read_yaml_mapping(config_path, "defaults")
                _validate_fields(config_path, defaults, _DEFAULT_RULES)
                return Project(directory=candidate.resolve(), defaults=defaults)
    return None


def load_test(test_path: Path, project: Project, kind: str) -> TestSpec:
    """Read and validate one test file. Raises ConfigError naming the file and field. Fields
    the test leaves out come from skillbench.yaml; `fixture:` paths start at the repo root."""
    raw = _read_yaml_mapping(test_path, "test fields")
    _validate_fields(test_path, raw, _TEST_RULES)
    _validate_skill_or_prompt(test_path, raw)
    merged = {**project.defaults, **raw}
    return TestSpec(
        source_path=test_path,
        name=raw.get("name", test_path.stem),
        prompt=raw.get("prompt", ""),
        kind=kind,
        fixture=_resolve_fixture(test_path, merged.get("fixture"), project.repo_root),
        model=project.defaults.get("model"),
        timeout_s=merged.get("timeout_s", _DEFAULT_TIMEOUT_S),
        answers=raw.get("answers", []),
        is_interactive=raw.get("interactive", False),
        assertions=_parse_assertions(test_path, raw.get("assert", [])),
        skill=raw.get("skill"),
        skill_input=raw.get("input", ""),
        triggers=_trigger_cases(raw.get("triggers", {})),
        repeat=merged.get("repeat", 1),
        min_pass_rate=merged.get("min_pass_rate", 1.0),
        fixture_exclude=project.directory,
    )


def _read_yaml_mapping(path: Path, what: str) -> dict[str, Any]:
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


def _validate_fields(path: Path, raw: dict[str, Any], rules: dict[str, "_Rule"]) -> None:
    for key, value in raw.items():
        if key not in rules:
            raise ConfigError(f"{path}: unknown field '{key}' (known fields: {', '.join(rules)})")
        is_valid, expected = rules[key]
        if not is_valid(value):
            raise ConfigError(f"{path}: field '{key}' must be {expected}, got {value!r}")


def _validate_skill_or_prompt(test_path: Path, raw: dict[str, Any]) -> None:
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


def _resolve_fixture(test_path: Path, raw_fixture: Any, repo_root: Path) -> Fixture | None:
    if raw_fixture is None:
        return None
    if isinstance(raw_fixture, dict):
        return GitFixture(url=raw_fixture["git"], ref=raw_fixture.get("ref"))
    if is_git_url(raw_fixture):
        return GitFixture(url=raw_fixture)
    local = (repo_root / raw_fixture).resolve()
    if not local.is_dir():
        raise ConfigError(f"{test_path}: field 'fixture' directory not found: {local}")
    return local


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


# --- field validators ----------------------------------------------------------------------


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


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _is_fixture(value: Any) -> bool:
    if isinstance(value, str):
        return value != ""
    return (
        isinstance(value, dict)
        and "git" in value
        and set(value) <= {"git", "ref"}
        and all(map(_is_non_empty_string, value.values()))
    )


def _is_triggers(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) <= set(_TRIGGER_KEYS)
        and all(
            _is_string_list(prompts) and all(map(_is_non_empty_string, prompts))
            for prompts in value.values()
        )
        and any(value.values())
    )


_Rule = tuple[Callable[[Any], bool], str]

# Fields a test and skillbench.yaml share: YAML key -> (validator, expected value).
_SHARED_RULES: dict[str, _Rule] = {
    "fixture": (_is_fixture, "a path, a git URL, or {git: <url>, ref: <branch|tag|commit>}"),
    "timeout_s": (_is_positive_number, "a positive number"),
    "repeat": (_is_positive_int, "a positive integer"),
    "min_pass_rate": (_is_rate, "a number from 0 to 1"),
}
_DEFAULT_RULES: dict[str, _Rule] = {"model": (_is_string, "a string"), **_SHARED_RULES}
_TEST_RULES: dict[str, _Rule] = {
    "name": (_is_string, "a string"),
    "skill": (_is_non_empty_string, "a skill name or a path to it"),
    "input": (_is_string, "a string"),
    "triggers": (_is_triggers, "a mapping with 'should' and/or 'should_not' lists of prompts"),
    "prompt": (_is_string, "a string"),
    **_SHARED_RULES,
    "answers": (_is_string_list, "a list of strings"),
    "interactive": (_is_bool, "true or false"),
    "assert": (_is_list, "a list"),
}
