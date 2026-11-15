"""Running one test on one backend and recording everything under runs/<run-id>/."""

import json
import os
import re
import signal
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from skillbench import skills
from skillbench.answers import AnswerSource, AskUser, LiveAnswerUnavailableError
from skillbench.assertions import Assertion, AssertionResult, RunFacts, evaluate_assertions
from skillbench.backends import Backend, InstalledBackend
from skillbench.config import TestSpec
from skillbench.progress import Progress
from skillbench.trace import (
    TurnInfo,
    TurnTrace,
    UserMessage,
    empty_trace,
    final_text,
    merge_turn_traces,
)
from skillbench.workspace import FixtureError, create_workspace

_RUN_ID_SUFFIX_LENGTH = 6
_DURATION_DECIMALS = 3
_IS_WINDOWS = os.name == "nt"
# How often a running turn's output is checked for progress (and for a trigger decision).
_POLL_INTERVAL_S = 0.5
_SKILL_TOOL = "Skill"

# Told the turn number and the most recent tool call (None before the first) as a turn runs.
OnActivity = Callable[[int, str | None], None]


@dataclass(frozen=True)
class RunOptions:
    """Choices made per invocation rather than in the test file."""

    installed: InstalledBackend
    eval_id: str  # shared by every run of one invocation
    cache_dir: Path  # where git fixtures are cached
    model_override: str | None = None
    ask_user: AskUser | None = None  # None when there is no terminal to ask live answers in
    progress: Progress | None = None  # told about each turn's activity as it runs


@dataclass(frozen=True)
class _ProcessOutcome:
    exit_code: int | None  # None when the process was killed
    timed_out: bool
    start_time: datetime
    end_time: datetime
    duration_s: float
    stopped_early: bool = False  # killed on purpose once the run had what it needed

    @property
    def run_status(self) -> str:
        if self.timed_out:
            return "timeout"
        if self.stopped_early:
            return "completed"
        return "completed" if self.exit_code == 0 else "error"


@dataclass(frozen=True)
class _Turn:
    user_message: UserMessage | None  # None for the first turn, which sends the test prompt
    outcome: _ProcessOutcome
    info: TurnInfo


class _StopReason(StrEnum):
    """Why a conversation ended; recorded in run.json as `stop_reason`."""

    NO_MORE_ANSWERS = "no_more_answers"
    TURN_FAILED = "turn_failed"
    MAX_TURNS = "max_turns"
    NEEDS_LIVE_ANSWER = "needs_live_answer"
    NO_SESSION_ID = "no_session_id"
    TRIGGER_DECIDED = "trigger_decided"  # a trigger run stopped at the first skill call
    SETUP_FAILED = "setup_failed"  # the workspace or skill could not be prepared


@dataclass(frozen=True)
class _Stop:
    reason: _StopReason
    error: str | None = None  # set when the stop makes the run an error


@dataclass
class _Conversation:
    assigned_session_id: str | None
    turns: list[_Turn] = field(default_factory=list)
    stop: _Stop = _Stop(_StopReason.NO_MORE_ANSWERS)

    @property
    def session_id(self) -> str | None:
        reported = [turn.info.session_id for turn in self.turns if turn.info.session_id]
        return reported[-1] if reported else self.assigned_session_id


class _TurnWatcher:
    """Re-reads a running turn's output whenever it grows: reports the latest tool call, and
    says whether the turn can be stopped now."""

    def __init__(
        self,
        backend: Backend,
        turn_number: int,
        on_activity: OnActivity | None,
        stop_at_first_skill_call: bool,
    ) -> None:
        self._backend = backend
        self._turn_number = turn_number
        self._on_activity = on_activity
        self._stop_at_first_skill_call = stop_at_first_skill_call
        self._seen_size = -1
        if on_activity is not None:
            on_activity(turn_number, None)

    @property
    def is_needed(self) -> bool:
        return self._on_activity is not None or self._stop_at_first_skill_call

    def should_stop(self, stdout_path: Path) -> bool:
        try:
            size = stdout_path.stat().st_size
        except OSError:
            return False
        if size == self._seen_size:
            return False
        self._seen_size = size
        info = self._backend.parse_stream(stdout_path.read_bytes())
        calls = list(_walk_tool_calls(info.trace["items"]))
        if self._on_activity is not None:
            self._on_activity(self._turn_number, calls[-1]["name"] if calls else None)
        return self._stop_at_first_skill_call and any(call["name"] == _SKILL_TOOL for call in calls)


def _walk_tool_calls(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for item in items:
        if item["kind"] == "tool_call":
            yield item
            yield from _walk_tool_calls(item["children"])


@dataclass(frozen=True)
class _TurnLauncher:
    """Launches the turns of one run: same CLI, flags and workspace each time."""

    installed: InstalledBackend
    session_args: list[str]
    workspace: Path
    raw_dir: Path
    timeout_s: float
    assigned_session_id: str | None  # set when skillbench, not the CLI, names the session
    on_activity: OnActivity | None = None
    stop_at_first_skill_call: bool = False  # trigger runs: the decision is all that's needed

    def run_turn(
        self, turn_number: int, message: str, resume_session_id: str | None
    ) -> tuple[_ProcessOutcome, TurnInfo]:
        backend = self.installed.backend
        stdout_path = self.raw_dir / f"{turn_number}.jsonl"
        turn_args = backend.turn_args(message, resume_session_id)
        command = [self.installed.executable, *turn_args, *self.session_args]
        watcher = _TurnWatcher(
            backend, turn_number, self.on_activity, self.stop_at_first_skill_call
        )
        outcome = _run_process(command, self.workspace, stdout_path, self.timeout_s, watcher)
        return outcome, backend.parse_stream(stdout_path.read_bytes())


def run_test(
    spec: TestSpec, runs_root: Path, options: RunOptions, repeat_index: int = 1
) -> dict[str, Any]:
    """Run `spec` on the chosen backend as a conversation (the prompt, then each answer as a
    follow-up turn in the same session), check its assertions, write run.json, raw output and
    report.html; return the record. `repeat_index` is which of the test's repeats this is."""
    backend = options.installed.backend
    model = options.model_override or spec.model
    run_dir = runs_root / _new_run_id(spec.name)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    header = {
        "run_id": run_dir.name,
        "eval_id": options.eval_id,
        "backend": backend.name,
        **_run_context(spec, repeat_index),
    }
    setup_started = datetime.now().astimezone()
    workspace: Path | None = None
    try:
        workspace, header["fixture_commit"] = create_workspace(
            spec.fixture, options.cache_dir, spec.fixture_exclude
        )
        spec = _prepare_for_workspace(spec, workspace, backend.name)
    except (FixtureError, skills.SkillNotFoundError) as error:
        record = {**header, **_setup_failed_record(spec, model, workspace, setup_started, error)}
        _write_run_files(run_dir, record)
        return record
    launcher = _TurnLauncher(
        installed=options.installed,
        session_args=backend.session_args(spec, model),
        workspace=workspace,
        raw_dir=raw_dir,
        timeout_s=spec.timeout_s,
        assigned_session_id=str(uuid.uuid4()) if backend.assigns_session_id else None,
        on_activity=options.progress.activity if options.progress is not None else None,
        stop_at_first_skill_call=spec.trigger is not None,
    )
    answers = AnswerSource(spec.answers, spec.is_interactive, options.ask_user)

    conversation = _converse(launcher, spec, answers)

    record = {**header, **_build_record(spec, model, launcher.workspace, conversation)}
    _write_run_files(run_dir, record)
    return record


def _run_context(spec: TestSpec, repeat_index: int) -> dict[str, Any]:
    """What the run was asked to do, recorded alongside what happened."""
    trigger = spec.trigger
    return {
        "kind": spec.kind,
        "skill": spec.skill,
        "trigger": (
            {"prompt": trigger.prompt, "should_invoke": trigger.should_invoke}
            if trigger is not None
            else None
        ),
        "fixture": str(spec.fixture) if spec.fixture is not None else None,
        "fixture_commit": None,
        "repeat_index": repeat_index,
        "repeat_count": spec.repeat,
        "min_pass_rate": spec.min_pass_rate,
    }


def _prepare_for_workspace(spec: TestSpec, workspace: Path, backend_name: str) -> TestSpec:
    """Load every plugin and skill the repo has, and turn `skill:` into the prompt that runs
    it (or, for a trigger run, the trigger prompt and the check that the skill did or didn't
    fire). Raises SkillNotFoundError."""
    found = skills.discover(workspace)
    skills.make_visible(workspace, found, backend_name)
    spec = replace(spec, plugin_dirs=found.plugin_dirs)
    if spec.skill is None:
        return spec
    target = skills.resolve(workspace, found, spec.skill)
    if spec.trigger is not None:
        check = "skill_invoked" if spec.trigger.should_invoke else "skill_not_invoked"
        return replace(
            spec,
            prompt=spec.trigger.prompt,
            answers=[],
            is_interactive=False,
            assertions=[Assertion(type=check, expected=target.name)],
        )
    invocation = target.invocation(backend_name)
    return replace(spec, prompt=f"{invocation} {spec.skill_input}".strip())


def _setup_failed_record(
    spec: TestSpec,
    model: str | None,
    workspace: Path | None,
    started: datetime,
    error: Exception,
) -> dict[str, Any]:
    """A run that never reached the agent: the fixture or skill couldn't be prepared."""
    return {
        "test_name": spec.name,
        "test_file": str(spec.source_path),
        "model": model,
        "start_time": started.isoformat(),
        "end_time": datetime.now().astimezone().isoformat(),
        "duration_s": 0.0,
        "session_id": None,
        "status": "error",
        "run_status": "not_started",
        "exit_code": None,
        "conversation_turns": 0,
        "stop_reason": _StopReason.SETUP_FAILED,
        "error": f"{spec.source_path}: {error}",
        "workspace": str(workspace) if workspace is not None else None,
        "plugins": [],
        "skills": [],
        "assertions": [],
        "trace": empty_trace(),
    }


def _converse(launcher: _TurnLauncher, spec: TestSpec, answers: AnswerSource) -> _Conversation:
    """Send turns until the answers run out, a turn fails, or max_turns is reached."""
    conversation = _Conversation(launcher.assigned_session_id)
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
    if conversation.turns[-1].outcome.stopped_early:
        return _Stop(_StopReason.TRIGGER_DECIDED)
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
        "prompt": spec.prompt,
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


def _new_run_id(test_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", test_name).strip("-")[:60] or "test"
    suffix = uuid.uuid4().hex[:_RUN_ID_SUFFIX_LENGTH]
    return f"{datetime.now():%Y%m%d-%H%M%S}-{safe_name}-{suffix}"


def _write_run_files(run_dir: Path, record: dict[str, Any]) -> None:
    (run_dir / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def _run_process(
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    timeout_s: float,
    watcher: _TurnWatcher | None = None,
) -> _ProcessOutcome:
    """Run to completion, timeout, or until the watcher says stop. Stdout streams straight to
    disk so partial output survives; the watcher reads it from there while the turn runs."""
    stderr_path = stdout_path.with_suffix(".stderr.txt")
    start_time = datetime.now().astimezone()
    started = time.monotonic()
    deadline = started + timeout_s
    exit_code: int | None = None
    timed_out = stopped_early = False
    poll_s = _POLL_INTERVAL_S if watcher is not None and watcher.is_needed else timeout_s
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=not _IS_WINDOWS,
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process_tree(process)
                timed_out = True
                break
            try:
                exit_code = process.wait(timeout=min(poll_s, remaining))
                break
            except subprocess.TimeoutExpired:
                if watcher is not None and watcher.should_stop(stdout_path):
                    _kill_process_tree(process)
                    stopped_early = True
                    break
    return _ProcessOutcome(
        exit_code=exit_code,
        timed_out=timed_out,
        start_time=start_time,
        end_time=datetime.now().astimezone(),
        duration_s=round(time.monotonic() - started, _DURATION_DECIMALS),
        stopped_early=stopped_early,
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
