"""The `skillbench/` project folder: finding it from any subdirectory, and its defaults file."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillbench.config import BACKEND_NAMES, DEFAULT_RULES, read_yaml_mapping, validate_fields

PROJECT_DIR_NAME = "skillbench"
CONFIG_FILE_NAME = "skillbench.yaml"
SKILL_TESTS_DIR_NAME = "skills"
AGENT_TESTS_DIR_NAME = "agent"
# The kind of test a file is, decided by which tests folder it lives in.
_TEST_KINDS = {SKILL_TESTS_DIR_NAME: "skill", AGENT_TESTS_DIR_NAME: "agent"}


def _is_backends(value: Any) -> bool:
    names = [value] if isinstance(value, str) else value
    return isinstance(names, list) and bool(names) and all(name in BACKEND_NAMES for name in names)


_CONFIG_RULES = {
    "backend": (_is_backends, "claude, copilot, or a list of them"),
    **DEFAULT_RULES,
}


@dataclass(frozen=True)
class Project:
    directory: Path  # the skillbench/ folder
    defaults: dict[str, Any]  # skillbench.yaml's test defaults, as written
    backends: list[str] | None  # skillbench.yaml's `backend:`

    @property
    def repo_root(self) -> Path:
        """The repo the project folder lives in; `skill:` and `fixture:` paths start here."""
        return self.directory.parent

    @property
    def runs_dir(self) -> Path:
        return self.directory / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.directory / ".cache"

    def kind_of(self, test_path: Path) -> str | None:
        """`skill` or `agent` if the file is under that tests folder, else None."""
        try:
            relative = test_path.resolve().relative_to(self.directory)
        except ValueError:
            return None
        return _TEST_KINDS.get(relative.parts[0]) if relative.parts else None


def find_project(start: Path) -> Project | None:
    """The nearest project folder at or above `start`, like git finds `.git`. Raises
    ConfigError if its skillbench.yaml is invalid."""
    for directory in [start, *start.parents]:
        for candidate in (directory / PROJECT_DIR_NAME, directory):
            if candidate.name == PROJECT_DIR_NAME and (candidate / CONFIG_FILE_NAME).is_file():
                return _load_project(candidate.resolve())
    return None


def _load_project(directory: Path) -> Project:
    config_path = directory / CONFIG_FILE_NAME
    defaults = read_yaml_mapping(config_path, "defaults")
    validate_fields(config_path, defaults, _CONFIG_RULES)
    backend = defaults.pop("backend", None)
    return Project(
        directory=directory,
        defaults=defaults,
        backends=[backend] if isinstance(backend, str) else backend,
    )
