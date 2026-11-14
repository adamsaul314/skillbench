"""Loading and validating test YAML files into a `TestSpec`."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_TIMEOUT_S = 600
_ISOLATION_MODES = ("clean", "none")


class ConfigError(Exception):
    """A test file is unreadable or invalid; the message names the file and field."""


@dataclass(frozen=True)
class TestSpec:
    """A validated test. Keys later slices act on (answers, assert, ...) are validated only."""

    source_path: Path
    name: str
    prompt: str
    skill_dir: Path | None = None
    fixture: Path | None = None
    model: str | None = None
    timeout_s: float = _DEFAULT_TIMEOUT_S
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    isolation: str = "clean"


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _is_isolation_mode(value: Any) -> bool:
    return value in _ISOLATION_MODES


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


# YAML key -> (validator, description of the expected value)
_FIELD_RULES: dict[str, tuple[Callable[[Any], bool], str]] = {
    "name": (_is_string, "a string"),
    "prompt": (_is_string, "a string"),
    "skill_dir": (_is_string, "a path string"),
    "fixture": (_is_string, "a path string"),
    "model": (_is_string, "a string"),
    "backends": (_is_string_list, "a list of strings"),
    "answers": (_is_string_list, "a list of strings"),
    "interactive": (_is_bool, "true or false"),
    "max_turns": (_is_positive_int, "a positive integer"),
    "timeout_s": (_is_positive_number, "a positive number"),
    "allowed_tools": (_is_string_list, "a list of strings"),
    "denied_tools": (_is_string_list, "a list of strings"),
    "isolation": (_is_isolation_mode, f"one of {', '.join(_ISOLATION_MODES)}"),
    "assert": (_is_list, "a list"),
}
_REQUIRED_FIELDS = ("prompt",)
_DIRECTORY_FIELDS = ("skill_dir", "fixture")


def load_test(test_path: Path) -> TestSpec:
    """Read and validate one test file. Raises ConfigError naming the file and field."""
    raw = _read_yaml_mapping(test_path)
    _validate_fields(test_path, raw)
    directories = _resolve_directories(test_path, raw)
    return TestSpec(
        source_path=test_path,
        name=raw.get("name", test_path.stem),
        prompt=raw["prompt"],
        skill_dir=directories.get("skill_dir"),
        fixture=directories.get("fixture"),
        model=raw.get("model"),
        timeout_s=raw.get("timeout_s", _DEFAULT_TIMEOUT_S),
        allowed_tools=raw.get("allowed_tools", []),
        denied_tools=raw.get("denied_tools", []),
        isolation=raw.get("isolation", "clean"),
    )


def _read_yaml_mapping(test_path: Path) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(test_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"{test_path}: cannot read file ({error.strerror})") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{test_path}: not valid YAML ({error})") from error
    if not isinstance(parsed, dict):
        raise ConfigError(f"{test_path}: top level must be a mapping of test fields")
    return parsed


def _validate_fields(test_path: Path, raw: dict[str, Any]) -> None:
    for key in raw:
        if key not in _FIELD_RULES:
            known = ", ".join(_FIELD_RULES)
            raise ConfigError(f"{test_path}: unknown field '{key}' (known fields: {known})")
    for key in _REQUIRED_FIELDS:
        if key not in raw:
            raise ConfigError(f"{test_path}: missing required field '{key}'")
    for key, value in raw.items():
        is_valid, expected = _FIELD_RULES[key]
        if not is_valid(value):
            raise ConfigError(f"{test_path}: field '{key}' must be {expected}, got {value!r}")


def _resolve_directories(test_path: Path, raw: dict[str, Any]) -> dict[str, Path]:
    """Directory fields are relative to the test file and must exist."""
    resolved: dict[str, Path] = {}
    for key in _DIRECTORY_FIELDS:
        if key not in raw:
            continue
        directory = (test_path.parent / raw[key]).resolve()
        if not directory.is_dir():
            raise ConfigError(f"{test_path}: field '{key}' directory not found: {directory}")
        resolved[key] = directory
    return resolved
