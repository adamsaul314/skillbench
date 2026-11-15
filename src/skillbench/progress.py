"""What the terminal shows while runs go: a live status with the current turn, the latest tool
call, elapsed time, an overall bar and an ETA; or, when output isn't a terminal, plain lines."""

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_RUN_RECORD_FILE_NAME = "run.json"
_REFRESHES_PER_SECOND = 4
# The bar never shows a run as done before it is, however long it overruns its estimate.
_MAX_RUN_FRACTION = 0.95
_BAR_WIDTH = 30


@dataclass(frozen=True)
class RunHeading:
    """What is about to run."""

    index: int  # 1-based position among all runs of this invocation
    total: int
    test_name: str
    backend: str
    model: str | None
    planned_turns: int | None  # the prompt plus scripted answers; None if not known up front
    repeat_index: int = 1
    repeat_count: int = 1


class Progress(Protocol):
    def run_started(self, heading: RunHeading, eta: "Eta") -> None: ...

    def activity(self, turn: int, last_tool: str | None) -> None: ...

    def run_finished(self, line: str) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


def load_history(runs_root: Path) -> dict[tuple[str, str], float]:
    """The most recent duration of each (test name, backend) recorded under runs_root."""
    latest: dict[tuple[str, str], tuple[str, float]] = {}
    if not runs_root.is_dir():
        return {}
    for record_path in runs_root.glob(f"*/{_RUN_RECORD_FILE_NAME}"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            key = (record["test_name"], record["backend"])
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
    last recorded duration on that backend, else the mean of runs finished so far; if any run
    still has no estimate, there is no ETA rather than a made-up one."""

    def __init__(
        self, history: dict[tuple[str, str], float], run_keys: list[tuple[str, str]]
    ) -> None:
        self._history = history
        self._run_keys = run_keys
        self._finished: list[float] = []
        self._current = 0  # index into run_keys of the run in progress

    def start_run(self, index: int) -> None:
        self._current = index

    def finish_run(self, duration_s: float) -> None:
        self._finished.append(duration_s)

    def estimate(self, key: tuple[str, str]) -> float | None:
        if key in self._history:
            return self._history[key]
        if self._finished:
            return sum(self._finished) / len(self._finished)
        return None

    def remaining_s(self, elapsed_s: float) -> float | None:
        estimates = [self.estimate(key) for key in self._run_keys[self._current :]]
        if any(estimate is None for estimate in estimates):
            return None
        current, *later = estimates
        return max(current - elapsed_s, 0.0) + sum(later)

    def fraction_done(self, elapsed_s: float) -> float:
        total = len(self._run_keys)
        if total == 0:
            return 1.0
        current_estimate = self.estimate(self._run_keys[self._current]) if self._run_keys else None
        current_fraction = (
            min(elapsed_s / current_estimate, _MAX_RUN_FRACTION) if current_estimate else 0.0
        )
        return (self._current + current_fraction) / total


def format_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def describe_run(heading: RunHeading) -> str:
    repeat = (
        f" (repeat {heading.repeat_index}/{heading.repeat_count})"
        if heading.repeat_count > 1
        else ""
    )
    model = heading.model or "default model"
    return (
        f"[{heading.index}/{heading.total}] {heading.test_name}{repeat}  "
        f"{heading.backend} · {model}"
    )


class PlainProgress:
    """For pipes and CI: one line when a run starts and one when it ends, no escape codes."""

    def run_started(self, heading: RunHeading, eta: Eta) -> None:
        print(describe_run(heading), flush=True)

    def activity(self, turn: int, last_tool: str | None) -> None:
        pass

    def run_finished(self, line: str) -> None:
        print(line, flush=True)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class LiveProgress:
    """A status block redrawn in place while a run goes, replaced by its result line after."""

    def __init__(self) -> None:
        from rich.console import Console

        self._console = Console(highlight=False)
        self._live = None
        self._heading: RunHeading | None = None
        self._eta: Eta | None = None
        self._started = 0.0
        self._turn = 1
        self._last_tool: str | None = None

    def run_started(self, heading: RunHeading, eta: Eta) -> None:
        from rich.live import Live

        self._heading, self._eta = heading, eta
        self._started = time.monotonic()
        self._turn, self._last_tool = 1, None
        self._live = Live(
            get_renderable=self._render,
            console=self._console,
            refresh_per_second=_REFRESHES_PER_SECOND,
            transient=True,
        )
        self._live.start()

    def activity(self, turn: int, last_tool: str | None) -> None:
        if turn != self._turn:
            self._last_tool = None
        self._turn = turn
        self._last_tool = last_tool or self._last_tool

    def run_finished(self, line: str) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._console.print(line, markup=False)

    def pause(self) -> None:
        if self._live is not None:
            self._live.stop()

    def resume(self) -> None:
        if self._live is not None:
            self._live.start()

    def _render(self):
        from rich.console import Group
        from rich.progress_bar import ProgressBar
        from rich.table import Table
        from rich.text import Text

        heading, eta = self._heading, self._eta
        assert heading is not None and eta is not None
        elapsed = time.monotonic() - self._started
        turns = f"/{heading.planned_turns}" if heading.planned_turns else ""
        activity = self._last_tool or "thinking"
        status = Text(
            f"  turn {self._turn}{turns} · {activity} · {format_seconds(elapsed)} elapsed"
        )
        status.stylize("dim")
        fraction = eta.fraction_done(elapsed)
        remaining = eta.remaining_s(elapsed)
        estimate = f"est. {format_seconds(remaining)} left" if remaining is not None else ""
        bar = Table.grid(padding=(0, 1))
        bar.add_row(
            Text(" "),
            ProgressBar(total=1.0, completed=fraction, width=_BAR_WIDTH),
            Text(f"{fraction:>4.0%}"),
            Text(estimate),
        )
        return Group(Text(describe_run(heading), style="bold"), status, bar)


def make_progress() -> Progress:
    """Live in a terminal, plain lines otherwise."""
    return LiveProgress() if sys.stdout.isatty() else PlainProgress()
