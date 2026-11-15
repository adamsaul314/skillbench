"""The runs/index.html dashboard: every run under runs/, newest first, each selectable for its
full timeline.

One static file with every run's data embedded and no scripts, so it opens from disk. Selecting
a run is a link to its detail section, which CSS `:target` reveals.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from skillbench.report import (
    PAGE_STYLE,
    format_cost_and_usage,
    format_duration,
    format_optional,
    format_start_time,
    render_run_detail,
)

INDEX_FILE_NAME = "index.html"
_RUN_RECORD_FILE_NAME = "run.json"
_BROKEN_STATUS = "broken"
_COLUMNS = ("date", "test", "backend", "model", "status", "duration", "turns", "cost / usage")
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Master-detail: the runs table, restyled as a pinned list, beside the selected run. A run is
# selected by its link (CSS :target); with none selected the newest run shows.
_DASHBOARD_STYLE = """
.dashboard { display: grid; grid-template-columns: minmax(16rem, 21rem) 1fr; min-height: 100vh; }
.runs { position: sticky; top: 0; height: 100vh; overflow-y: auto; background: var(--panel);
        border-right: 1px solid var(--rule); }
.runs header { padding: 1.5rem 1.25rem 1rem; border-bottom: 1px solid var(--rule); }
.runs h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -.01em; }
.runs header p { margin: .2rem 0 0; color: var(--muted); font-size: .85rem; }
.runs table, .runs tbody { display: block; border-collapse: collapse; }
.runs thead { position: absolute; width: 1px; height: 1px; overflow: hidden;
              clip-path: inset(50%); }
.runs tr { position: relative; display: grid; grid-template-columns: auto auto 1fr auto;
           grid-template-areas: "test test test status" "backend duration . ."
                                "date date date date";
           gap: 0 .75rem; padding: .7rem 1.25rem .7rem 1rem; border-left: 4px solid transparent;
           border-bottom: 1px solid var(--rule); }
.runs tr:hover { background: var(--code); }
.runs tr:focus-within { outline: 2px solid var(--focus); outline-offset: -2px; }
.runs td { padding: 0; font-size: .8rem; color: var(--muted); }
.runs td:nth-child(1) { grid-area: date; font-variant-numeric: tabular-nums; }
.runs td:nth-child(2) { grid-area: test; font-size: .95rem; font-weight: 600; color: var(--ink);
                        overflow-wrap: anywhere; }
.runs td:nth-child(2) a { text-decoration: none; outline: none; }
.runs td:nth-child(2) a::after { content: ""; position: absolute; inset: 0; }
.runs td:nth-child(3) { grid-area: backend; }
.runs td:nth-child(5) { grid-area: status; font-family: var(--display); font-weight: 600;
                        font-size: .9rem; }
.runs td:nth-child(6) { grid-area: duration; font-variant-numeric: tabular-nums; }
.runs td:nth-child(4), .runs td:nth-child(7), .runs td:nth-child(8) { display: none; }
.runs .empty { padding: 1.25rem; color: var(--muted); font-size: .9rem; }
.details { padding: 2.5rem clamp(1rem, 4vw, 3.5rem) 4rem; min-width: 0; max-width: 72rem; }
.run-detail { display: none; scroll-margin-top: 1rem; }
.run-detail:target,
body:not(:has(.run-detail:target)) .run-detail:first-child { display: block; }
body:not(:has(.run-detail:target)) .runs tbody tr:first-child { border-left-color: var(--ink);
                                                                  background: var(--bench); }
.empty-state { max-width: 34rem; }
.empty-state h2 { margin-bottom: .5rem; }
@media (max-width: 760px) {
  .dashboard { display: block; }
  .runs { position: static; height: auto; max-height: 45vh; border-right: 0;
          border-bottom: 1px solid var(--rule); }
}
"""


class _BrokenRunError(Exception):
    """A run directory whose run.json is missing or can't be shown."""


@dataclass(frozen=True)
class _RunView:
    """One run as the dashboard shows it: a table row and a detail section."""

    run_id: str
    status: str
    sort_time: datetime
    # Runs of one test in one eval (the same test on several backends) share a group.
    group: str
    backend: str
    row_html: str
    detail_html: str


def write_dashboard(runs_root: Path) -> None:
    """(Re)write runs_root/index.html from every run directory found under runs_root."""
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dirs = [entry for entry in runs_root.iterdir() if entry.is_dir()]
    views = _newest_first_by_group([_view_run(run_dir) for run_dir in run_dirs])
    (runs_root / INDEX_FILE_NAME).write_text(_render_page(views), encoding="utf-8")


def _newest_first_by_group(views: list[_RunView]) -> list[_RunView]:
    """Groups ordered by their newest run, newest first; within a group, by backend name."""
    group_times: dict[str, datetime] = {}
    for view in views:
        group_times[view.group] = max(view.sort_time, group_times.get(view.group, view.sort_time))
    by_backend = sorted(views, key=lambda view: view.backend)
    return sorted(by_backend, key=lambda view: (group_times[view.group], view.group), reverse=True)


def _view_run(run_dir: Path) -> _RunView:
    try:
        record = _read_record(run_dir)
        return _render_run(run_dir.name, record)
    except _BrokenRunError as error:
        return _render_broken_run(run_dir, str(error))


def _read_record(run_dir: Path) -> dict[str, Any]:
    record_path = run_dir / _RUN_RECORD_FILE_NAME
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise _BrokenRunError(f"{record_path} not found") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _BrokenRunError(f"{record_path} could not be read: {error}") from error
    if not isinstance(record, dict):
        raise _BrokenRunError(f"{record_path} is not a JSON object")
    return record


def _render_run(run_id: str, record: dict[str, Any]) -> _RunView:
    try:
        start_time = datetime.fromisoformat(record["start_time"]).astimezone()
        cells = (
            format_start_time(record["start_time"]),
            _detail_link(run_id, _run_label(record)),
            escape(record["backend"]),
            escape(format_optional(record["model"])),
            _status_cell(record["status"]),
            format_duration(record["duration_s"]),
            escape(format_optional(record["trace"]["num_turns"])),
            escape(format_cost_and_usage(record["trace"])),
        )
        detail = _detail_section(run_id, record["test_name"], render_run_detail(record))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise _BrokenRunError(
            f"{run_id}/{_RUN_RECORD_FILE_NAME} is malformed: {error!r}"
        ) from error
    eval_id = record.get("eval_id")  # absent in runs recorded before evals existed
    return _RunView(
        run_id=run_id,
        status=record["status"],
        sort_time=start_time,
        group=f"{eval_id}/{record['test_name']}" if eval_id else run_id,
        backend=record["backend"],
        row_html=_row(cells),
        detail_html=detail,
    )


def _run_label(record: dict[str, Any]) -> str:
    """The test name, plus which repeat this was when the test ran more than once."""
    repeat_count = record.get("repeat_count") or 1  # absent in runs from before repeats
    if repeat_count > 1:
        return f"{record['test_name']} #{record.get('repeat_index', 1)}/{repeat_count}"
    return record["test_name"]


def _render_broken_run(run_dir: Path, error: str) -> _RunView:
    modified = datetime.fromtimestamp(run_dir.stat().st_mtime).astimezone()
    cells = (
        modified.strftime(_DATE_FORMAT),
        _detail_link(run_dir.name, run_dir.name),
        "",
        "",
        _status_cell(_BROKEN_STATUS),
        "",
        "",
        "",
    )
    detail = _detail_section(
        run_dir.name,
        run_dir.name,
        f'<p class="run-error">{escape(error)}</p>'
        '<p class="workspace">This run has no readable record, so it can\'t be shown. It may '
        "still be running or may have been interrupted. Delete the folder to remove it from "
        "this list.</p>",
    )
    return _RunView(
        run_id=run_dir.name,
        status=_BROKEN_STATUS,
        sort_time=modified,
        group=run_dir.name,
        backend="",
        row_html=_row(cells),
        detail_html=detail,
    )


def _detail_id(run_id: str) -> str:
    return f"run-{run_id}"


def _detail_link(run_id: str, label: str) -> str:
    return f'<a href="#{escape(_detail_id(run_id))}">{escape(label)}</a>'


def _status_cell(status: str) -> str:
    return f'<span class="status-{escape(status)}">{escape(status)}</span>'


def _row(cells: tuple[str, ...]) -> str:
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _detail_section(run_id: str, title: str, body_html: str) -> str:
    run_id_html = "" if title == run_id else f" <small>{escape(run_id)}</small>"
    return (
        f'<section class="run-detail" id="{escape(_detail_id(run_id))}">'
        f"<h2>{escape(title)}{run_id_html}</h2>{body_html}</section>"
    )


def _render_page(views: list[_RunView]) -> str:
    header = "".join(f"<th>{escape(column)}</th>" for column in _COLUMNS)
    rows = "".join(view.row_html for view in views)
    details = "".join(view.detail_html for view in views) or _EMPTY_STATE
    selected_rows = "".join(_selected_row_style(view.run_id) for view in views)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>skillbench runs</title>"
        f"<style>{PAGE_STYLE}{_DASHBOARD_STYLE}</style></head>"
        '<body class="dashboard">'
        '<nav class="runs" aria-label="Runs">'
        f"<header><h1>skillbench</h1><p>{escape(_tally(views))}</p></header>"
        f'<table aria-label="Runs, newest first"><thead><tr>{header}</tr></thead>'
        f"<tbody>{rows}</tbody></table></nav>"
        f'<main class="details">{details}</main>'
        # After the list, not in <head>: these rules name every run.
        f"<style>{selected_rows}</style>"
        "</body></html>\n"
    )


_EMPTY_STATE = (
    '<section class="empty-state"><h2>No runs yet</h2>'
    "<p>Run <code>skillbench run evals/</code> from this folder. Each run is added here with "
    "its full timeline.</p></section>"
)


def _tally(views: list[_RunView]) -> str:
    if not views:
        return "No runs"
    runs = f"{len(views)} run" + ("" if len(views) == 1 else "s")
    counts: dict[str, int] = {}
    for view in views:
        counts[view.status] = counts.get(view.status, 0) + 1
    labels = {"pass": "passed", "fail": "failed", "error": "errored", "timeout": "timed out"}
    parts = [f"{count} {labels.get(status, status)}" for status, count in counts.items()]
    return f"{runs}: " + ", ".join(parts)


def _selected_row_style(run_id: str) -> str:
    """Marks the list row of the run whose detail is showing."""
    detail_id = _css_string(_detail_id(run_id))
    return (
        f'body:has([id="{detail_id}"]:target) .runs tr:has(a[href="#{detail_id}"])'
        "{border-left-color:var(--ink);background:var(--bench)}"
    )


def _css_string(value: str) -> str:
    """value escaped for a double-quoted CSS string inside <style>."""
    return "".join(
        char if char.isascii() and (char.isalnum() or char in "-_.") else f"\\{ord(char):x} "
        for char in value
    )
