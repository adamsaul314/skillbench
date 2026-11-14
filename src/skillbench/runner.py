"""Running one test on the Claude backend and recording everything under runs/<run-id>/."""

import json
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from skillbench.answers import AnswerSource, AskUser, LiveAnswerUnavailableError
from skillbench.assertions import AssertionResult, RunFacts, evaluate_assertions
from skillbench.claude_command import build_session_args, build_turn_args
from skillbench.claude_trace import ClaudeRunInfo, parse_claude_stream
from skillbench.config import TestSpec
from skillbench.report import render_report
from skillbench.trace import TurnTrace, UserMessage, final_text, merge_turn_traces
from skillbench.workspace import create_workspace

_CLAUDE_EXECUTABLE = "claude"
_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
_RUN_ID_SUFFIX_LENGTH = 6
_DURATION_DECIMALS = 3
_IS_WINDOWS = os.name == "nt"


class BackendNotFoundError(Exception):
    """The agent CLI is not on PATH."""


@dataclass(frozen=True)
class RunOptions:
    """Choices made per invocation rather than in the test file."""

    model_override: str | None = None
    ask_user: AskUser | None = None  # None when there is no terminal to ask live answers in


@dataclass(frozen=True)
class _ProcessOutcome:
    exit_code: int | None  # None when the process was killed
    timed_out: bool
    start_time: datetime
    end_time: datetime
    duration_s: float

    @property
    def run_status(self) -> str:
        if self.timed_out:
            return "timeout"
        return "completed" if self.exit_code == 0 else "error"


@dataclass(frozen=True)
class _Turn:
    user_message: UserMessage | None  # None for the first turn, which sends the test prompt
    outcome: _ProcessOutcome
    info: ClaudeRunInfo


class _StopReason(StrEnum):
    """Why a conversation ended; recorded in run.json as `stop_reason`."""

    NO_MORE_ANSWERS = "no_more_answers"
    TURN_FAILED = "turn_failed"
    MAX_TURNS = "max_turns"
    NEEDS_LIVE_ANSWER = "needs_live_answer"
    NO_SESSION_ID = "no_session_id"


@dataclass(frozen=True)
class _Stop:
    reason: _StopReason
    error: str | None = None  # set when the stop makes the run an error


@dataclass
class _Conversation:
    turns: list[_Turn] = field(default_factory=list)
    stop: _Stop = _Stop(_StopReason.NO_MORE_ANSWERS)

    @property
    def session_id(self) -> str | None:
        reported = [turn.info.session_id for turn in self.turns if turn.info.session_id]
        return reported[-1] if reported else None


@dataclass(frozen=True)
class _TurnLauncher:
    """Launches the turns of one run: same executable, flags and workspace each time."""

    executable: str
    session_args: list[str]
    workspace: Path
    raw_dir: Path
    timeout_s: float

    def run_turn(
        self, turn_number: int, message: str, resume_session_id: str | None
    ) -> tuple[_ProcessOutcome, ClaudeRunInfo]:
        stdout_path = self.raw_dir / f"{turn_number}.jsonl"
        turn_args = build_turn_args(message, resume_session_id)
        command = [self.executable, *turn_args, *self.session_args]
        outcome = _run_process(command, self.workspace, stdout_path, self.timeout_s)
        return outcome, parse_claude_stream(stdout_path.read_bytes())


def run_test(spec: TestSpec, runs_root: Path, options: RunOptions) -> dict[str, Any]:
    """Run `spec` on Claude as a conversation (the prompt, then each answer as a resumed turn),
    check its assertions, write run.json, raw output and report.html; return the record."""
    model = options.model_override or spec.model
    has_api_key = bool(os.environ.get(_API_KEY_ENV_VAR))
    run_dir = runs_root / _new_run_id(spec.name)
    launcher = _TurnLauncher(
        executable=_find_claude(),
        session_args=build_session_args(spec, model, has_api_key),
        workspace=create_workspace(spec.fixture),
        raw_dir=run_dir / "raw",
        timeout_s=spec.timeout_s,
    )
    launcher.raw_dir.mkdir(parents=True)
    answers = AnswerSource(spec.answers, spec.is_interactive, options.ask_user)

    conversation = _converse(launcher, spec, answers)

    record = {
        "run_id": run_dir.name,
        **_build_record(spec, model, launcher.workspace, conversation),
    }
    _write_run_files(run_dir, record)
    return record


def _converse(launcher: _TurnLauncher, spec: TestSpec, answers: AnswerSource) -> _Conversation:
    """Send turns until the answers run out, a turn fails, or max_turns is reached."""
    conversation = _Conversation()
    user_message: UserMessage | None = None
    while True:
        message = spec.prompt if user_message is None else user_message.text
        turn_number = len(conversation.turns) + 1
        outcome, info = launcher.run_turn(turn_number, message, conversation.session_id)
        conversation.turns.append(_Turn(user_message, outcome, info))
        stop = _stop_before_next_turn(conversation, spec, answers)
        if stop is not None:
            conversation.stop = stop
            return conversation
        try:
            user_message = answers.next_answer(final_text(info.trace["items"]))
        except LiveAnswerUnavailableError as error:
            conversation.stop = _Stop(_StopReason.NEEDS_LIVE_ANSWER, f"{spec.source_path}: {error}")
            return conversation
        if user_message is None:
            return conversation


def _stop_before_next_turn(
    conversation: _Conversation, spec: TestSpec, answers: AnswerSource
) -> _Stop | None:
    """Why no further turn may follow the last one, or None if one may."""
    turn_count = len(conversation.turns)
    if conversation.turns[-1].outcome.run_status != "completed":
        return _Stop(_StopReason.TURN_FAILED)
    if not answers.has_more:
        return _Stop(_StopReason.NO_MORE_ANSWERS)
    if spec.max_turns is not None and turn_count >= spec.max_turns:
        return _Stop(_StopReason.MAX_TURNS)
    if conversation.session_id is None:
        error = f"turn {turn_count} reported no session id, so the next answer cannot resume it"
        return _Stop(_StopReason.NO_SESSION_ID, error)
    return None


def _build_record(
    spec: TestSpec, model: str | None, workspace: Path, conversation: _Conversation
) -> dict[str, Any]:
    first_turn, last_turn = conversation.turns[0], conversation.turns[-1]
    trace = merge_turn_traces(
        [TurnTrace(turn.user_message, turn.info.trace) for turn in conversation.turns]
    )
    facts = RunFacts(
        trace=trace,
        workspace=workspace,
        exit_code=last_turn.outcome.exit_code,
        timed_out=last_turn.outcome.timed_out,
        prompt=spec.prompt,
        loaded_skills=first_turn.info.skills,
        exceeded_turn_limit=(
            spec.max_turns if conversation.stop.reason == _StopReason.MAX_TURNS else None
        ),
    )
    assertion_results = evaluate_assertions(spec.assertions, facts)
    run_status = last_turn.outcome.run_status
    process_seconds = sum(turn.outcome.duration_s for turn in conversation.turns)
    return {
        "test_name": spec.name,
        "test_file": str(spec.source_path),
        "backend": "claude",
        "model": model or first_turn.info.reported_model,
        "start_time": first_turn.outcome.start_time.isoformat(),
        "end_time": last_turn.outcome.end_time.isoformat(),
        # Time the CLI spent running; excludes time spent waiting for live answers.
        "duration_s": round(process_seconds, _DURATION_DECIMALS),
        "session_id": conversation.session_id,
        "status": _test_status(run_status, conversation.stop.error, assertion_results),
        "run_status": run_status,
        "exit_code": last_turn.outcome.exit_code,
        "conversation_turns": len(conversation.turns),
        "stop_reason": conversation.stop.reason,
        "error": conversation.stop.error,
        "workspace": str(workspace),
        "plugins": first_turn.info.plugins,
        "skills": first_turn.info.skills,
        "assertions": [result.to_json() for result in assertion_results],
        "trace": trace,
    }


def _test_status(
    run_status: str, conversation_error: str | None, assertion_results: list[AssertionResult]
) -> str:
    """`error` if the CLI or the conversation did not finish cleanly, else `fail` if any
    assertion failed."""
    if run_status != "completed" or conversation_error is not None:
        return "error"
    return "pass" if all(result.passed for result in assertion_results) else "fail"


def _find_claude() -> str:
    executable = shutil.which(_CLAUDE_EXECUTABLE)
    if executable is None:
        raise BackendNotFoundError(f"'{_CLAUDE_EXECUTABLE}' was not found on PATH")
    return executable


def _new_run_id(test_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", test_name).strip("-") or "test"
    suffix = uuid.uuid4().hex[:_RUN_ID_SUFFIX_LENGTH]
    return f"{datetime.now():%Y%m%d-%H%M%S}-{safe_name}-{suffix}"


def _write_run_files(run_dir: Path, record: dict[str, Any]) -> None:
    (run_dir / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (run_dir / "report.html").write_text(render_report(record), encoding="utf-8")


def _run_process(
    command: list[str], cwd: Path, stdout_path: Path, timeout_s: float
) -> _ProcessOutcome:
    """Run to completion or timeout. Stdout streams straight to disk so partial output survives."""
    stderr_path = stdout_path.with_suffix(".stderr.txt")
    start_time = datetime.now().astimezone()
    started = time.monotonic()
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=not _IS_WINDOWS,
        )
        try:
            exit_code, timed_out = process.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            exit_code, timed_out = None, True
    return _ProcessOutcome(
        exit_code=exit_code,
        timed_out=timed_out,
        start_time=start_time,
        end_time=datetime.now().astimezone(),
        duration_s=round(time.monotonic() - started, _DURATION_DECIMALS),
    )


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """The agent CLI spawns its own children (tools, subagents); kill all of them."""
    if _IS_WINDOWS:
        # taskkill fails if the process already exited; the poll below handles that case.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if process.poll() is None:
            process.kill()
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
