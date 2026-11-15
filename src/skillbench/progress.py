"""What the terminal shows while tests run: each test's steps as a checklist, what Claude is
doing right now in plain words, and each test's result with any failed checks. When output
isn't a terminal, plain lines instead."""

import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Protocol

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

_RUN_RECORD_FILE_NAME = "run.json"
_REFRESHES_PER_SECOND = 4
_SHOWN_ACTIONS = 3  # the running step's latest actions
_ACTION_TEXT_CHARS = 70
_PASSED_CHECKS_SHOWN = 3  # a failed test lists this many passed checks, then counts the rest
_STEP_MARKS = {"done": "✓", "failed": "✗", "running": "●", "pending": "○"}
_STEP_STYLES = {"done": "green", "failed": "red", "running": "yellow", "pending": "dim"}
_PLAIN_MARKS = {"done": "[ok]  ", "failed": "[FAIL]"}
_STATUS_WORDS = {"pass": "passed", "fail": "failed", "error": "error"}


@dataclass(frozen=True)
class TestHeading:
    """What is about to run."""

    index: int  # 1-based position among all runs of this invocation
    total: int
    test_name: str
    model: str | None
    steps: list[str]  # the planned steps: the prompt, then each scripted answer
    repeat_index: int = 1
    repeat_count: int = 1

    @property
    def title(self) -> str:
        if self.repeat_count == 1:
            return self.test_name
        return f"{self.test_name} (repeat {self.repeat_index}/{self.repeat_count})"


class Progress(Protocol):
    def test_started(self, heading: TestHeading, eta: "Eta") -> None: ...

    def step_started(self, text: str) -> None: ...

    def action(self, description: str) -> None: ...

    def step_finished(self, detail: str, is_ok: bool = True) -> None: ...

    def test_finished(self, record: dict[str, Any]) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


def make_progress() -> Progress:
    """Live in a terminal, plain lines otherwise."""
    return LiveProgress() if sys.stdout.isatty() else PlainProgress()


# --- describing what Claude does -----------------------------------------------------------


def describe_tool_call(call: dict[str, Any], workspace: Path) -> str:
    """A tool call in plain words, e.g. `Editing script.js` or `Running node --test`."""
    name = call["name"]
    tool_input = call["input"] if isinstance(call["input"], dict) else {}
    path = _workspace_relative(tool_input.get("file_path") or tool_input.get("path"), workspace)
    agent = tool_input.get("subagent_type") or tool_input.get("description", "")
    descriptions = {
        "Read": f"Reading {path}",
        "Write": f"Writing {path}",
        "Edit": f"Editing {path}",
        "MultiEdit": f"Editing {path}",
        "NotebookEdit": f"Editing {path}",
        "Bash": f"Running {_one_line(tool_input.get('command'))}",
        "PowerShell": f"Running {_one_line(tool_input.get('command'))}",
        "Grep": f"Searching for {_one_line(tool_input.get('pattern'))}",
        "Glob": f"Finding files {_one_line(tool_input.get('pattern'))}",
        "Skill": f"Using skill {tool_input.get('skill', '')}",
        "Agent": f"Subagent {agent}",
        "Task": f"Subagent {agent}",
        "TodoWrite": "Updating its to-do list",
        "WebFetch": f"Fetching {tool_input.get('url', '')}",
        "WebSearch": f"Searching the web for {_one_line(tool_input.get('query'))}",
    }
    return _shorten(descriptions.get(name, f"Using {name}").strip(), _ACTION_TEXT_CHARS)


def _workspace_relative(raw_path: Any, workspace: Path) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        return ""
    path = PurePath(raw_path)
    for root in (workspace, workspace.resolve()):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name if path.is_absolute() else path.as_posix()


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- a finished test -----------------------------------------------------------------------


def result_lines(record: dict[str, Any]) -> list[tuple[str, str]]:
    """A finished test as (kind, text) lines, kind being `done`, `failed` or `info`: a heading,
    any error, then its checks, listing every failed one with the reason."""
    status = record["status"]
    title = record["test_name"]
    if record.get("repeat_count", 1) > 1:
        title += f" (repeat {record['repeat_index']}/{record['repeat_count']})"
    facts = [_STATUS_WORDS.get(status, status), format_seconds(record["duration_s"])]
    if record["trace"].get("cost_usd") is not None:
        facts.append(f"${record['trace']['cost_usd']:.2f}")
    facts.append(_plural(_step_count(record), "step"))
    lines = [("done" if status == "pass" else "failed", f"{title}   {'  ·  '.join(facts)}")]
    if record.get("error"):
        error = record["error"].removeprefix(f"{record['test_file']}: ")
        lines.append(("failed", f"    {error}"))
    return lines + _check_lines(record["assertions"])


def _check_lines(results: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if not results:
        return []
    failed = [result for result in results if not result["passed"]]
    if not failed:
        return [("done", f"    all {_plural(len(results), 'check')} passed")]
    lines = []
    for result in failed:
        lines.append(("failed", f"    {format_check(result)}"))
        lines.append(("info", f"        {result['reason']}"))
    passed = [result for result in results if result["passed"]]
    lines += [("done", f"    {format_check(result)}") for result in passed[:_PASSED_CHECKS_SHOWN]]
    if len(passed) > _PASSED_CHECKS_SHOWN:
        more = _plural(len(passed) - _PASSED_CHECKS_SHOWN, "more check")
        lines.append(("info", f"    + {more} passed"))
    return lines


def format_check(result: dict[str, Any]) -> str:
    """A check as written in the test, e.g. `tool_order: Read -> Edit`."""
    expected = result["expected"]
    if isinstance(expected, list):
        expected = " -> ".join(map(str, expected))
    elif isinstance(expected, dict):
        expected = " ".join(
            repr(value) if key == "text" else str(value) for key, value in expected.items()
        )
    return f"{result['type']}: {expected}"


def _step_count(record: dict[str, Any]) -> int:
    actions = ("user_message", "user_command", "new_session")
    return 1 + sum(1 for item in record["trace"]["items"] if item["kind"] in actions)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --- the steps of the running test ---------------------------------------------------------


@dataclass
class _Step:
    text: str
    state: str = "pending"  # pending, running, done or failed
    detail: str = ""
    started: float = 0.0


@dataclass
class _Checklist:
    """The running test's steps, started in order. A step beyond the plan (a live answer) is
    added at the end; a started step takes the text actually sent."""

    steps: list[_Step] = field(default_factory=list)
    actions: deque[str] = field(default_factory=lambda: deque(maxlen=_SHOWN_ACTIONS))
    started_count: int = 0

    @classmethod
    def planned(cls, texts: list[str]) -> "_Checklist":
        return cls(steps=[_Step(text) for text in texts])

    @property
    def running(self) -> _Step | None:
        index = self.started_count - 1
        if 0 <= index < len(self.steps) and self.steps[index].state == "running":
            return self.steps[index]
        return None

    def start(self, text: str) -> None:
        if self.started_count >= len(self.steps):
            self.steps.append(_Step(text))
        step = self.steps[self.started_count]
        step.text, step.state, step.started = text, "running", time.monotonic()
        self.started_count += 1
        self.actions.clear()

    def finish(self, detail: str, is_ok: bool) -> _Step | None:
        step = self.running
        if step is not None:
            step.state, step.detail = ("done" if is_ok else "failed"), detail
        self.actions.clear()
        return step


# --- terminal output -----------------------------------------------------------------------


class PlainProgress:
    """For pipes and CI: a line per test and per finished step, then the result. ASCII marks."""

    def __init__(self) -> None:
        self._checklist = _Checklist()

    def test_started(self, heading: TestHeading, eta: "Eta") -> None:
        self._checklist = _Checklist.planned(heading.steps)
        model = heading.model or "default model"
        print(f"[{heading.index}/{heading.total}] {heading.title}  ({model})", flush=True)

    def step_started(self, text: str) -> None:
        self._checklist.start(text)

    def action(self, description: str) -> None:
        pass

    def step_finished(self, detail: str, is_ok: bool = True) -> None:
        step = self._checklist.finish(detail, is_ok)
        if step is not None:
            line = f"  {_PLAIN_MARKS[step.state]} {_shorten(step.text, 70)}  {detail}"
            print(line.rstrip(), flush=True)

    def test_finished(self, record: dict[str, Any]) -> None:
        for kind, text in result_lines(record):
            print(f"{_PLAIN_MARKS.get(kind, '      ')} {text}", flush=True)
        print(flush=True)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class LiveProgress:
    """The running test's checklist, redrawn in place; replaced by its result when it ends."""

    def __init__(self) -> None:
        self._console = Console(highlight=False)
        self._live: Live | None = None
        self._heading = TestHeading(0, 0, "", None, [])
        self._checklist = _Checklist()
        self._eta = Eta({}, [])
        self._started = 0.0

    def test_started(self, heading: TestHeading, eta: "Eta") -> None:
        self._heading, self._eta = heading, eta
        self._checklist = _Checklist.planned(heading.steps)
        self._started = time.monotonic()
        self._live = Live(
            get_renderable=self._render,
            console=self._console,
            refresh_per_second=_REFRESHES_PER_SECOND,
            transient=True,
        )
        self._live.start()

    def step_started(self, text: str) -> None:
        self._checklist.start(text)

    def action(self, description: str) -> None:
        self._checklist.actions.append(description)

    def step_finished(self, detail: str, is_ok: bool = True) -> None:
        self._checklist.finish(detail, is_ok)

    def test_finished(self, record: dict[str, Any]) -> None:
        self.pause()
        self._live = None
        for kind, text in result_lines(record):
            mark = _STEP_MARKS.get(kind, " ")
            self._console.print(Text(f"{mark} {text}", style=_STEP_STYLES.get(kind, "dim")))
        self._console.print()

    def pause(self) -> None:
        if self._live is not None:
            self._live.stop()

    def resume(self) -> None:
        if self._live is not None:
            self._live.start()

    def _render(self) -> Group:
        elapsed = time.monotonic() - self._started
        return Group(
            self._header(elapsed), Text(), self._steps_table(), Text(), self._footer(elapsed)
        )

    def _header(self, elapsed: float) -> Table:
        heading = self._heading
        model = heading.model or "default model"
        title = f"{heading.title}  ·  {model}  ·  test {heading.index} of {heading.total}"
        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify="right")
        header.add_row(Text(title, style="bold"), Text(format_seconds(elapsed), style="dim"))
        return header

    def _steps_table(self) -> Table:
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=3)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        table.add_column(justify="right", no_wrap=True)
        running = self._checklist.running
        for step in self._checklist.steps:
            is_running = step is running
            detail = format_seconds(time.monotonic() - step.started) if is_running else step.detail
            table.add_row(
                Text(f" {_STEP_MARKS[step.state]}", style=_STEP_STYLES[step.state]),
                Text(step.text, style="bold" if is_running else ""),
                Text(detail, style="dim"),
            )
            if is_running:
                for description in self._checklist.actions or ["thinking…"]:
                    table.add_row("", Text(f"    {description}", style="dim"), "")
        return table

    def _footer(self, elapsed: float) -> Text:
        remaining = self._eta.remaining_s(elapsed)
        estimate = (
            "first run of this test, no time estimate yet"
            if remaining is None
            else f"about {format_seconds(remaining)} left"
        )
        position = max(self._checklist.started_count, 1)
        steps = len(self._checklist.steps)
        return Text(f"  step {position} of {steps}  ·  {estimate}", style="dim")


# --- estimating time left ------------------------------------------------------------------


def load_history(runs_root: Path) -> dict[str, float]:
    """The most recent duration of each test recorded under runs_root."""
    latest: dict[str, tuple[str, float]] = {}
    if not runs_root.is_dir():
        return {}
    for record_path in runs_root.glob(f"*/{_RUN_RECORD_FILE_NAME}"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            key = record["test_name"]
            started, duration = record["start_time"], float(record["duration_s"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if duration <= 0 or record.get("stop_reason") == "setup_failed":
            continue
        if key not in latest or started > latest[key][0]:
            latest[key] = (started, duration)
    return {key: duration for key, (_, duration) in latest.items()}


class Eta:
    """Estimates how long the rest of the invocation takes. Each run's estimate is its test's
    last recorded duration, else the mean of runs finished so far; if any run still has no
    estimate, there is no ETA rather than a made-up one."""

    def __init__(self, history: dict[str, float], run_keys: list[str]) -> None:
        self._history = history
        self._run_keys = run_keys
        self._finished: list[float] = []
        self._current = 0  # index into run_keys of the run in progress

    def start_run(self, index: int) -> None:
        self._current = index

    def finish_run(self, duration_s: float) -> None:
        self._finished.append(duration_s)

    def estimate(self, key: str) -> float | None:
        if key in self._history:
            return self._history[key]
        if self._finished:
            return sum(self._finished) / len(self._finished)
        return None

    def remaining_s(self, elapsed_s: float) -> float | None:
        estimates = [self.estimate(key) for key in self._run_keys[self._current :]]
        if not estimates or any(estimate is None for estimate in estimates):
            return None
        current, *later = estimates
        return max(current - elapsed_s, 0.0) + sum(later)


def format_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class SilentProgress:
    """Shows nothing: for runs nobody is watching."""

    def test_started(self, heading: TestHeading, eta: Eta) -> None:
        pass

    def step_started(self, text: str) -> None:
        pass

    def action(self, description: str) -> None:
        pass

    def step_finished(self, detail: str, is_ok: bool = True) -> None:
        pass

    def test_finished(self, record: dict[str, Any]) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass
