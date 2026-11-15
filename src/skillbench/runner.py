"""Running one test as a conversation with Claude Code and recording it under runs/<run-id>/."""

import json
import os
import re
import signal
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from skillbench import claude, skills
from skillbench.assertions import Assertion, AssertionResult, RunFacts, evaluate_assertions
from skillbench.claude import TurnInfo, UserMessage
from skillbench.config import TestSpec
from skillbench.progress import Progress, SilentProgress, describe_tool_call, format_seconds
from skillbench.workspace import FixtureError, copy_folder, create_workspace, run_shell

_IS_WINDOWS = os.name == "nt"
_POLL_INTERVAL_S = 0.5
# Typed in place of a live answer, these end the conversation.
_END_COMMANDS = ("", "/done")

# Shows the agent's final text to the user and returns what they typed (None at end of input).
AskUser = Callable[[str], str | None]


@dataclass(frozen=True)
class RunOptions:
    """Choices made per invocation rather than in the test file."""

    executable: str  # the claude CLI
    eval_id: str  # shared by every run of one invocation
    model_override: str | None = None
    ask_user: AskUser | None = None  # None when there is no terminal to ask live answers in
    progress: Progress = field(default_factory=SilentProgress)  # shows the run as it goes

    def model_for(self, spec: TestSpec) -> str | None:
        return self.model_override or spec.model


def run_test(
    spec: TestSpec, runs_root: Path, options: RunOptions, repeat_index: int = 1
) -> dict[str, Any]:
    """Run `spec` once, check its assertions, and write run.json and the raw output under
    runs_root; return the record. A fixture or skill that can't be set up gives an `error`
    record without launching the agent."""
    run_dir = runs_root / _new_run_id(spec.name)
    (run_dir / "raw").mkdir(parents=True)
    record = {
        "run_id": run_dir.name,
        "eval_id": options.eval_id,
        "backend": "claude",
        **_run_context(spec, repeat_index),
    }
    setup_started = datetime.now().astimezone()
    workspace = None
    try:
        workspace, record["fixture_commit"] = create_workspace(spec.fixture, spec.fixture_exclude)
        spec = _prepare_for_workspace(spec, workspace, run_dir / "plugins")
    except (FixtureError, skills.SkillNotFoundError) as error:
        record |= _setup_failed_record(spec, options, workspace, setup_started, error)
    else:
        conversation = _converse(spec, options, workspace, run_dir / "raw")
        record |= _conversation_record(spec, options, workspace, conversation)
    (run_dir / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _run_context(spec: TestSpec, repeat_index: int) -> dict[str, Any]:
    """What the run was asked to do, recorded alongside what happened."""
    return {
        "kind": spec.kind,
        "skill": spec.skill,
        "trigger": None if spec.trigger is None else vars(spec.trigger),
        "fixture": None if spec.fixture is None else str(spec.fixture),
        "fixture_commit": None,
        "repeat_index": repeat_index,
        "repeat_count": spec.repeat,
        "min_pass_rate": spec.min_pass_rate,
    }


def _prepare_for_workspace(spec: TestSpec, workspace: Path, snapshot_dir: Path) -> TestSpec:
    """Load every plugin and skill the repo has, plus the `plugin:` folders, and turn `skill:`
    into the prompt that runs it (or, for a trigger run, the trigger prompt and the check that
    the skill did or didn't fire). Raises SkillNotFoundError."""
    found = skills.discover(workspace, _snapshot_plugins(spec, snapshot_dir))
    skills.make_visible(workspace, found)
    spec = replace(spec, plugin_dirs=found.plugin_dirs)
    if spec.skill is None:
        return spec
    target = skills.resolve(workspace, found, spec.skill)
    if spec.trigger is None:
        return replace(spec, prompt=f"{target.invocation} {spec.skill_input}".strip())
    check = "skill_invoked" if spec.trigger.should_invoke else "skill_not_invoked"
    return replace(
        spec,
        prompt=spec.trigger.prompt,
        answers=[],
        is_interactive=False,
        assertions=[Assertion(type=check, expected=target.name)],
    )


def _snapshot_plugins(spec: TestSpec, snapshot_dir: Path) -> list[Path]:
    """Copy each `plugin:` folder into the run, so the agent can't change the real one and the
    run keeps the exact version it tested."""
    snapshots = []
    for plugin in spec.plugins:
        snapshot = snapshot_dir / plugin.name
        snapshot.mkdir(parents=True)
        copy_folder(plugin, snapshot, spec.fixture_exclude)
        snapshots.append(snapshot)
    return snapshots


# --- the conversation ----------------------------------------------------------------------

# An answer that is exactly this starts a fresh session instead of being sent.
_CLEAR = "/clear"
# An answer starting with this runs the rest as a shell command in the workspace.
_COMMAND_PREFIX = "!"


class _StopReason(StrEnum):
    """Why a conversation ended; recorded in run.json as `stop_reason`."""

    NO_MORE_ANSWERS = "no_more_answers"
    TURN_FAILED = "turn_failed"
    COMMAND_FAILED = "command_failed"  # a `!command` answer exited non-zero
    NEEDS_LIVE_ANSWER = "needs_live_answer"
    NO_SESSION_ID = "no_session_id"
    TRIGGER_DECIDED = "trigger_decided"  # a trigger run stopped at the first skill call
    SETUP_FAILED = "setup_failed"  # the workspace or skill could not be prepared


@dataclass(frozen=True)
class _Stop:
    reason: _StopReason
    error: str | None = None  # set when the stop makes the run an error


@dataclass(frozen=True)
class _Turn:
    preface: list[dict[str, Any]]  # the user's actions since the last turn: commands, /clear
    user_message: UserMessage | None  # None for the first turn, which sends the test prompt
    outcome: "_ProcessOutcome"
    info: TurnInfo


@dataclass
class _Conversation:
    turns: list[_Turn] = field(default_factory=list)
    stop: _Stop = _Stop(_StopReason.NO_MORE_ANSWERS)
    pending_actions: list[dict[str, Any]] = field(default_factory=list)  # since the last turn
    starts_fresh: bool = False  # a /clear came since the last turn

    @property
    def session_id(self) -> str | None:
        reported = [turn.info.session_id for turn in self.turns if turn.info.session_id]
        return reported[-1] if reported else None

    @property
    def session_to_resume(self) -> str | None:
        return None if self.starts_fresh else self.session_id

    def add_turn(
        self, user_message: UserMessage | None, outcome: "_ProcessOutcome", info: TurnInfo
    ) -> None:
        self.turns.append(_Turn(self.pending_actions, user_message, outcome, info))
        self.pending_actions = []
        self.starts_fresh = False


class _LiveAnswerUnavailableError(Exception):
    """A live answer is needed but there is no terminal to ask in."""


class _CommandFailedError(Exception):
    """A `!command` answer exited non-zero; the steps after it can't be trusted to work."""


def _converse(spec: TestSpec, options: RunOptions, workspace: Path, raw_dir: Path) -> _Conversation:
    """Send the prompt, then each answer as a follow-up turn, until the answers run out or a
    turn fails. `/clear` and `!command` answers are carried out between turns."""
    launcher = _TurnLauncher(spec, options, workspace, raw_dir)
    answers = _AnswerSource(spec.answers, spec.is_interactive, options.ask_user)
    conversation = _Conversation()
    between_turns = _BetweenTurns(
        answers, conversation, workspace, spec.timeout_s, options.progress
    )
    user_message: UserMessage | None = None
    while True:
        message = spec.prompt if user_message is None else user_message.text
        turn_number = len(conversation.turns) + 1
        options.progress.step_started(message)
        outcome, info = launcher.run_turn(turn_number, message, conversation.session_to_resume)
        options.progress.step_finished(
            _turn_detail(outcome, info), outcome.run_status == "completed"
        )
        conversation.add_turn(user_message, outcome, info)
        stop = _stop_before_next_turn(conversation, answers)
        if stop is not None:
            conversation.stop = stop
            return conversation
        try:
            user_message = between_turns.next_message(claude.final_text(info.trace["items"]) or "")
        except _LiveAnswerUnavailableError as error:
            conversation.stop = _Stop(_StopReason.NEEDS_LIVE_ANSWER, f"{spec.source_path}: {error}")
            return conversation
        except _CommandFailedError as error:
            conversation.stop = _Stop(_StopReason.COMMAND_FAILED, f"{spec.source_path}: {error}")
            return conversation
        if user_message is None:
            return conversation


def _turn_detail(outcome: "_ProcessOutcome", info: TurnInfo) -> str:
    """How long a turn took and what it cost, e.g. `38s · $0.04`."""
    if outcome.timed_out:
        return f"timed out after {format_seconds(outcome.duration_s)}"
    detail = format_seconds(outcome.duration_s)
    cost = info.trace["cost_usd"]
    return detail if cost is None else f"{detail} · ${cost:.2f}"


@dataclass(frozen=True)
class _BetweenTurns:
    """Carries out the user's actions between turns: `/clear` and `!command` answers."""

    answers: "_AnswerSource"
    conversation: _Conversation
    workspace: Path
    timeout_s: float
    progress: Progress

    def next_message(self, agent_text: str) -> UserMessage | None:
        """The next message for Claude, after carrying out any `/clear` and `!command` answers
        that come before it; None when the conversation is over. Raises _CommandFailedError."""
        shown_to_user = agent_text
        while (answer := self.answers.next_answer(shown_to_user)) is not None:
            if answer.text == _CLEAR:
                shown_to_user = self._clear()
            elif answer.text.startswith(_COMMAND_PREFIX):
                shown_to_user = self._run(answer.text.removeprefix(_COMMAND_PREFIX).strip())
            else:
                return answer
        return None

    def _clear(self) -> str:
        self.progress.step_started(_CLEAR)
        self.conversation.starts_fresh = True
        self.conversation.pending_actions.append({"kind": "new_session"})
        self.progress.step_finished("fresh session")
        return "(the next message starts a fresh session)"

    def _run(self, command: str) -> str:
        self.progress.step_started(f"{_COMMAND_PREFIX}{command}")
        result = run_shell(command, self.workspace, self.timeout_s)
        self.conversation.pending_actions.append(
            {"kind": "user_command", "command": command, **vars(result)}
        )
        self.progress.step_finished(f"exit {result.exit_code}", result.succeeded)
        if not result.succeeded:
            raise _CommandFailedError(
                f"'!{command}' exited with code {result.exit_code}: {result.output}"
            )
        return f"$ {command}\n{result.output}"


def _stop_before_next_turn(conversation: _Conversation, answers: "_AnswerSource") -> _Stop | None:
    """Why no further turn may follow the last one, or None if one may."""
    last_outcome = conversation.turns[-1].outcome
    if last_outcome.stopped_early:
        return _Stop(_StopReason.TRIGGER_DECIDED)
    if last_outcome.run_status != "completed":
        return _Stop(_StopReason.TURN_FAILED)
    if not answers.has_more:
        return _Stop(_StopReason.NO_MORE_ANSWERS)
    if conversation.session_id is None:
        turn_count = len(conversation.turns)
        error = f"turn {turn_count} reported no session id, so the next answer cannot resume it"
        return _Stop(_StopReason.NO_SESSION_ID, error)
    return None


class _AnswerSource:
    """Hands out scripted answers in order; once they run out, asks the user if the test is
    interactive."""

    def __init__(self, scripted: list[str], is_interactive: bool, ask_user: AskUser | None):
        self._scripted = list(scripted)
        self._is_interactive = is_interactive
        self._ask_user = ask_user

    @property
    def has_more(self) -> bool:
        """Whether the conversation would continue after this turn."""
        return bool(self._scripted) or self._is_interactive

    def next_answer(self, shown_to_user: str) -> UserMessage | None:
        """The next answer, or None when the conversation is over. `shown_to_user` is what a
        live answerer sees first. Raises _LiveAnswerUnavailableError if the user must be asked
        but cannot be."""
        if self._scripted:
            return UserMessage(text=self._scripted.pop(0).strip(), source="scripted")
        if not self._is_interactive:
            return None
        if self._ask_user is None:
            raise _LiveAnswerUnavailableError(
                "the agent is waiting for an answer but stdin is not a terminal, "
                "so scripted answers are required (add them under 'answers:')"
            )
        typed = self._ask_user(shown_to_user)
        if typed is None or typed.strip() in _END_COMMANDS:
            self._is_interactive = False
            return None
        return UserMessage(text=typed.strip(), source="live")


class _TurnLauncher:
    """Launches each turn of one run: the same CLI, flags and workspace every time."""

    def __init__(self, spec: TestSpec, options: RunOptions, workspace: Path, raw_dir: Path) -> None:
        self._spec = spec
        self._options = options
        self._workspace = workspace
        self._raw_dir = raw_dir

    def run_turn(
        self, turn_number: int, message: str, session_id: str | None
    ) -> tuple["_ProcessOutcome", TurnInfo]:
        command = claude.turn_command(
            self._options.executable,
            message,
            session_id,
            self._options.model_for(self._spec),
            self._spec.plugin_dirs,
        )
        stdout_path = self._raw_dir / f"{turn_number}.jsonl"
        watcher = _TurnWatcher(stdout_path, self._workspace, self._options.progress)
        outcome = _run_process(
            command, self._workspace, stdout_path, self._spec.timeout_s, self._stop_check(watcher)
        )
        return outcome, claude.parse_stream(stdout_path.read_bytes())

    def _stop_check(self, watcher: "_TurnWatcher") -> Callable[[], bool]:
        """What to check while the turn runs: a trigger run stops at its first skill call;
        otherwise the watcher only reports progress."""
        return watcher.has_invoked_a_skill if self._spec.trigger is not None else watcher.report


class _TurnWatcher:
    """Re-reads a running turn's output whenever it grows, to report each new tool call and to
    see whether a skill has been invoked yet."""

    def __init__(self, stdout_path: Path, workspace: Path, progress: Progress) -> None:
        self._stdout_path = stdout_path
        self._workspace = workspace
        self._progress = progress
        self._seen_size = -1
        self._tool_calls: list[dict[str, Any]] = []

    def report(self) -> bool:
        """Tell progress about new tool calls; never asks for the turn to stop."""
        self._refresh()
        return False

    def has_invoked_a_skill(self) -> bool:
        self._refresh()
        return any(call["name"] == claude.SKILL_TOOL for call in self._tool_calls)

    def _refresh(self) -> None:
        try:
            size = self._stdout_path.stat().st_size
        except OSError:
            return
        if size == self._seen_size:
            return
        self._seen_size = size
        trace = claude.parse_stream(self._stdout_path.read_bytes()).trace
        calls = list(claude.walk_tool_calls(trace["items"]))
        for call in calls[len(self._tool_calls) :]:
            self._progress.action(describe_tool_call(call, self._workspace))
        self._tool_calls = calls


# --- the run record ------------------------------------------------------------------------


def _conversation_record(
    spec: TestSpec, options: RunOptions, workspace: Path, conversation: _Conversation
) -> dict[str, Any]:
    first_turn, last_turn = conversation.turns[0], conversation.turns[-1]
    trace = claude.merge_turns(
        [(turn.preface, turn.user_message, turn.info.trace) for turn in conversation.turns]
    )
    trace["items"] += conversation.pending_actions  # actions after the last turn
    facts = RunFacts(
        trace=trace, workspace=workspace, prompt=spec.prompt, loaded_skills=first_turn.info.skills
    )
    assertion_results = evaluate_assertions(spec.assertions, facts)
    run_status = last_turn.outcome.run_status
    return {
        "test_name": spec.name,
        "test_file": str(spec.source_path),
        "prompt": spec.prompt,
        "model": options.model_for(spec) or first_turn.info.reported_model,
        "start_time": first_turn.outcome.start_time.isoformat(),
        "end_time": last_turn.outcome.end_time.isoformat(),
        # Time the CLI spent running; excludes time spent waiting for live answers.
        "duration_s": round(sum(turn.outcome.duration_s for turn in conversation.turns), 3),
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


def _setup_failed_record(
    spec: TestSpec,
    options: RunOptions,
    workspace: Path | None,
    started: datetime,
    error: Exception,
) -> dict[str, Any]:
    """A run that never reached the agent: the fixture or skill couldn't be prepared."""
    return {
        "test_name": spec.name,
        "test_file": str(spec.source_path),
        "prompt": spec.prompt,
        "model": options.model_for(spec),
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
        "workspace": None if workspace is None else str(workspace),
        "plugins": [],
        "skills": [],
        "assertions": [],
        "trace": claude.empty_trace(),
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
    return f"{datetime.now():%Y%m%d-%H%M%S}-{safe_name}-{uuid.uuid4().hex[:6]}"


# --- the subprocess ------------------------------------------------------------------------


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
        return "completed" if self.stopped_early or self.exit_code == 0 else "error"


@dataclass(frozen=True)
class _Ending:
    exit_code: int | None = None
    timed_out: bool = False
    stopped_early: bool = False


def _run_process(
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    timeout_s: float,
    should_stop: Callable[[], bool] | None,
) -> _ProcessOutcome:
    """Run to completion or timeout, or until `should_stop` says so. Stdout streams straight to
    disk so partial output survives and can be read while the turn runs."""
    start_time = datetime.now().astimezone()
    started = time.monotonic()
    stderr_path = stdout_path.with_suffix(".stderr.txt")
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=not _IS_WINDOWS,
        )
        ending = _wait(process, started + timeout_s, should_stop)
        if ending.exit_code is None:
            _kill_process_tree(process)
    return _ProcessOutcome(
        exit_code=ending.exit_code,
        timed_out=ending.timed_out,
        start_time=start_time,
        end_time=datetime.now().astimezone(),
        duration_s=round(time.monotonic() - started, 3),
        stopped_early=ending.stopped_early,
    )


def _wait(
    process: subprocess.Popen[bytes], deadline: float, should_stop: Callable[[], bool] | None
) -> _Ending:
    """Wait for the process to exit, the deadline to pass, or `should_stop` to say so."""
    while (remaining := deadline - time.monotonic()) > 0:
        wait_s = remaining if should_stop is None else min(_POLL_INTERVAL_S, remaining)
        try:
            return _Ending(exit_code=process.wait(timeout=wait_s))
        except subprocess.TimeoutExpired:
            if should_stop is not None and should_stop():
                return _Ending(stopped_early=True)
    return _Ending(timed_out=True)


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
