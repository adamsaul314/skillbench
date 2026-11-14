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

from skillbench.report import PAGE_STYLE, format_duration, format_optional, render_run_detail

INDEX_FILE_NAME = "index.html"
_RUN_RECORD_FILE_NAME = "run.json"
_BROKEN_STATUS = "broken"
_COLUMNS = ("date", "test", "backend", "model", "status", "duration", "turns", "cost / usage")
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_COST_DECIMALS = 4

_DASHBOARD_STYLE = """
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { text-align: left; padding: .3rem .6rem; border-bottom: 1px solid #ddd; }
.run-detail { display: none; margin-top: 2rem; }
.run-detail:target { display: block; }
.status-pass { color: #262; }
.status-fail, .status-error, .status-timeout, .status-broken { color: #b00; }
"""


class _BrokenRunError(Exception):
    """A run directory whose run.json is missing or can't be shown."""


@dataclass(frozen=True)
class _RunView:
    """One run as the dashboard shows it: a table row and a detail section."""

    sort_time: datetime
    row_html: str
    detail_html: str


def write_dashboard(runs_root: Path) -> None:
    """(Re)write runs_root/index.html from every run directory found under runs_root."""
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dirs = [entry for entry in runs_root.iterdir() if entry.is_dir()]
    views = sorted(map(_view_run, run_dirs), key=lambda view: view.sort_time, reverse=True)
    (runs_root / INDEX_FILE_NAME).write_text(_render_page(views), encoding="utf-8")


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
            start_time.strftime(_DATE_FORMAT),
            _detail_link(run_id, record["test_name"]),
            escape(record["backend"]),
            escape(format_optional(record["model"])),
            _status_cell(record["status"]),
            format_duration(record["duration_s"]),
            escape(format_optional(record["trace"]["num_turns"])),
            escape(_cost_and_usage(record["trace"])),
        )
        detail = _detail_section(run_id, record["test_name"], render_run_detail(record))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise _BrokenRunError(
            f"{run_id}/{_RUN_RECORD_FILE_NAME} is malformed: {error!r}"
        ) from error
    return _RunView(sort_time=start_time, row_html=_row(cells), detail_html=detail)


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
    detail = _detail_section(run_dir.name, run_dir.name, f'<p class="error">{escape(error)}</p>')
    return _RunView(sort_time=modified, row_html=_row(cells), detail_html=detail)


def _cost_and_usage(trace: dict[str, Any]) -> str:
    usage = trace["usage"]
    parts = []
    if trace["cost_usd"] is not None:
        parts.append(f"${trace['cost_usd']:.{_COST_DECIMALS}f}")
    if "input_tokens" in usage or "output_tokens" in usage:
        input_tokens = format_optional(usage.get("input_tokens"))
        output_tokens = format_optional(usage.get("output_tokens"))
        parts.append(f"{input_tokens} in / {output_tokens} out tokens")
    return ", ".join(parts) if parts else format_optional(None)


def _detail_id(run_id: str) -> str:
    return f"run-{run_id}"


def _detail_link(run_id: str, label: str) -> str:
    return f'<a href="#{escape(_detail_id(run_id))}">{escape(label)}</a>'


def _status_cell(status: str) -> str:
    return f'<span class="status-{escape(status)}">{escape(status)}</span>'


def _row(cells: tuple[str, ...]) -> str:
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _detail_section(run_id: str, title: str, body_html: str) -> str:
    return (
        f'<section class="run-detail" id="{escape(_detail_id(run_id))}">'
        f"<h2>{escape(title)} <small>{escape(run_id)}</small></h2>{body_html}</section>"
    )


def _render_page(views: list[_RunView]) -> str:
    header = "".join(f"<th>{escape(column)}</th>" for column in _COLUMNS)
    rows = "".join(view.row_html for view in views)
    details = "".join(view.detail_html for view in views)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8"><title>skillbench runs</title>'
        f"<style>{PAGE_STYLE}{_DASHBOARD_STYLE}</style></head><body>"
        f"<h1>skillbench runs ({len(views)})</h1>"
        "<p>Select a run to see its timeline.</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
        f"{details}"
        "</body></html>\n"
    )
