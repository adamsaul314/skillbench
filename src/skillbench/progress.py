"""What the terminal shows while tests run: one line per test, a spinner while it runs and its
result when it ends, with a short reason when it didn't pass. The details live in the
dashboard."""

import sys
import time
from dataclasses import dataclass
from typing import Any, Protocol

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

_REFRESHES_PER_SECOND = 8
_REASON_CHARS = 100
_MARKS = {"pass": "✓", "fail": "✗", "error": "✗"}
_PLAIN_MARKS = {"pass": "ok  ", "fail": "FAIL", "error": "FAIL"}
_STYLES = {"pass": "green", "fail": "red", "error": "red"}
_STATUS_WORDS = {"pass": "passed", "fail": "failed", "error": "error"}


@dataclass(frozen=True)
class TestHeading:
    """What is about to run."""

    title: str  # the test's name, with its repeat number when repeated
    planned_steps: int | None  # the prompt plus scripted answers; None when answered live
    name_width: int  # the longest test name in this invocation, to line results up


class Progress(Protocol):
    def test_started(self, heading: TestHeading) -> None: ...

    def step_started(self, text: str) -> None: ...

    def test_finished(self, record: dict[str, Any]) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


def make_progress() -> Progress:
    """A spinner in a terminal, plain lines otherwise."""
    return LiveProgress() if sys.stdout.isatty() else PlainProgress()


def result_line(record: dict[str, Any], name_width: int) -> tuple[str, str]:
    """A finished test's status and its line: name, verdict, time and cost."""
    status = record["status"]
    cells = [
        _title(record).ljust(name_width),
        _STATUS_WORDS.get(status, status).ljust(6),
        format_seconds(record["duration_s"]).rjust(6),
    ]
    if record["trace"].get("cost_usd") is not None:
        cells.append(f"${record['trace']['cost_usd']:.2f}")
    return status, "   ".join(cells)


def why_not_passed(record: dict[str, Any]) -> str | None:
    """One line saying why a test didn't pass: where the run stopped, or the first failed
    check. None when it passed."""
    if record["status"] == "pass":
        return None
    if record.get("error"):
        problem = record["error"].removeprefix(f"{record['test_file']}: ").splitlines()[0]
        steps = steps_taken(record)
        reason = f"stopped at step {steps}: {problem}" if steps else problem
        return _shorten(reason, _REASON_CHARS)
    checks = record["assertions"]
    failed = [check for check in checks if not check["passed"]]
    reason = f"{len(failed)} of {len(checks)} checks failed: {format_check(failed[0])}"
    return _shorten(reason, _REASON_CHARS)


def format_check(result: dict[str, Any]) -> str:
    """A check as written in the test, e.g. `tool_order Read -> Edit`."""
    expected = result["expected"]
    if isinstance(expected, list):
        expected = " -> ".join(map(str, expected))
    elif isinstance(expected, dict):
        expected = " ".join(
            repr(value) if key == "text" else str(value) for key, value in expected.items()
        )
    return f"{result['type']} {expected}"


def format_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _title(record: dict[str, Any]) -> str:
    if record.get("repeat_count", 1) > 1:
        return f"{record['test_name']} #{record['repeat_index']}/{record['repeat_count']}"
    return record["test_name"]


def steps_taken(record: dict[str, Any]) -> int:
    """The prompt plus every answer acted on (sent, run, or /clear)."""
    if record.get("stop_reason") == "setup_failed":
        return 0
    actions = ("user_message", "user_command", "new_session")
    return 1 + sum(1 for item in record["trace"]["items"] if item["kind"] in actions)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class LiveProgress:
    """A spinner line for the running test, replaced by its result line."""

    def __init__(self) -> None:
        self._console = Console(highlight=False)
        self._live: Live | None = None
        self._spinner = Spinner("dots")
        self._heading = TestHeading("", None, 0)
        self._steps_started = 0
        self._started = 0.0

    def test_started(self, heading: TestHeading) -> None:
        self._heading, self._steps_started = heading, 0
        self._started = time.monotonic()
        self._live = Live(
            get_renderable=self._render,
            console=self._console,
            refresh_per_second=_REFRESHES_PER_SECOND,
            transient=True,
        )
        self._live.start()

    def step_started(self, text: str) -> None:
        self._steps_started += 1

    def test_finished(self, record: dict[str, Any]) -> None:
        self.pause()
        self._live = None
        status, line = result_line(record, self._heading.name_width)
        self._console.print(Text(f"{_MARKS.get(status, '?')} {line}", style=_STYLES.get(status)))
        if (reason := why_not_passed(record)) is not None:
            self._console.print(Text(f"    {reason}", style="dim"))

    def pause(self) -> None:
        if self._live is not None:
            self._live.stop()

    def resume(self) -> None:
        if self._live is not None:
            self._live.start()

    def _render(self) -> Spinner:
        heading = self._heading
        planned = f" of {heading.planned_steps}" if heading.planned_steps else ""
        step = f"step {max(self._steps_started, 1)}{planned}"
        elapsed = format_seconds(time.monotonic() - self._started)
        self._spinner.update(text=f"{heading.title.ljust(heading.name_width)}   {step}   {elapsed}")
        return self._spinner


class PlainProgress:
    """For pipes and CI: a line when each test starts and its result when it ends."""

    def __init__(self) -> None:
        self._name_width = 0

    def test_started(self, heading: TestHeading) -> None:
        self._name_width = heading.name_width
        print(f"...  {heading.title}", flush=True)

    def step_started(self, text: str) -> None:
        pass

    def test_finished(self, record: dict[str, Any]) -> None:
        status, line = result_line(record, self._name_width)
        print(f"{_PLAIN_MARKS.get(status, '?   ')} {line}", flush=True)
        if (reason := why_not_passed(record)) is not None:
            print(f"       {reason}", flush=True)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class SilentProgress:
    """Shows nothing: for runs nobody is watching."""

    def test_started(self, heading: TestHeading) -> None:
        pass

    def step_started(self, text: str) -> None:
        pass

    def test_finished(self, record: dict[str, Any]) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass
