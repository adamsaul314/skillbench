"""The agent CLIs skillbench drives, and finding which of them are installed and runnable."""

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from skillbench import claude_command, copilot_command
from skillbench.claude_trace import parse_claude_stream
from skillbench.config import BACKEND_NAMES, TestSpec
from skillbench.copilot_trace import parse_copilot_stream
from skillbench.trace import TurnInfo

_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
# A healthy CLI prints its version in well under this; a hung stub must not stall the run.
_VERSION_CHECK_TIMEOUT_S = 30


class BackendNotFoundError(Exception):
    """A backend the run needs is not installed, or is installed but won't run."""


@dataclass(frozen=True)
class Backend:
    """How to drive one agent CLI headless and read what it did."""

    name: str
    install_hint: str
    # Arguments every turn shares, given the test and the effective model (None = default).
    session_args: Callable[[TestSpec, str | None], list[str]]
    # Arguments for one turn, given its message and the session to continue (None = new).
    turn_args: Callable[[str, str | None], list[str]]
    parse_stream: Callable[[bytes], TurnInfo]
    # Makes the test's skill visible to the agent before the first turn.
    prepare_workspace: Callable[[TestSpec, Path], None]
    # True if skillbench picks the session id up front (the CLI is told it on every turn);
    # False if the CLI reports one in its first turn's events.
    assigns_session_id: bool


@dataclass(frozen=True)
class InstalledBackend:
    backend: Backend
    executable: str


def _claude_session_args(spec: TestSpec, model: str | None) -> list[str]:
    has_api_key = bool(os.environ.get(_API_KEY_ENV_VAR))
    return claude_command.build_session_args(spec, model, has_api_key)


def _no_workspace_setup(_spec: TestSpec, _workspace: Path) -> None:
    """Claude is pointed at the skill by a flag instead."""


_BACKENDS = {
    "claude": Backend(
        name="claude",
        install_hint="install Claude Code: npm install -g @anthropic-ai/claude-code",
        session_args=_claude_session_args,
        turn_args=claude_command.build_turn_args,
        parse_stream=parse_claude_stream,
        prepare_workspace=_no_workspace_setup,
        assigns_session_id=False,
    ),
    "copilot": Backend(
        name="copilot",
        install_hint="install GitHub Copilot CLI: npm install -g @github/copilot",
        session_args=copilot_command.build_session_args,
        turn_args=copilot_command.build_turn_args,
        parse_stream=parse_copilot_stream,
        prepare_workspace=copilot_command.install_skill,
        assigns_session_id=True,
    ),
}
assert tuple(_BACKENDS) == BACKEND_NAMES, "every backend name needs an adapter, primary first"


def find_installed(backend_name: str) -> InstalledBackend | None:
    """The backend if its CLI is on PATH and answers `--version`; None otherwise."""
    executable = shutil.which(backend_name)
    if executable is None:
        return None
    try:
        check = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_VERSION_CHECK_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return InstalledBackend(_BACKENDS[backend_name], executable) if check.returncode == 0 else None


def not_installed_error(backend_names: list[str]) -> BackendNotFoundError:
    hints = "; ".join(_BACKENDS[name].install_hint for name in backend_names)
    names = " or ".join(backend_names)
    return BackendNotFoundError(f"no runnable {names} found on PATH ({hints})")
