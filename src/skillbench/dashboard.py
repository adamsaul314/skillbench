"""The skillbench/runs/index.html dashboard: every run, newest first, each selectable
for its full timeline.

One static file with every run's data embedded and no scripts, so it opens from disk. Selecting
a run is a link to its detail section, which CSS `:target` reveals.
"""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from skillbench.claude import final_text
from skillbench.progress import (
    format_seconds,
    plural,
    run_error,
    shorten,
    steps_taken,
    why_not_passed,
)
from skillbench.workspace import is_git_url

INDEX_FILE_NAME = "index.html"
_RUN_RECORD_FILE_NAME = "run.json"
_EMPTY_STATE = (
    '<section class="empty-state"><h2>No runs yet</h2>'
    "<p>Run <code>skillbench skill</code> or <code>skillbench agent</code>. Each run is added "
    "here with its full timeline.</p></section>"
)
_COLUMNS = ("what's tested", "status", "test", "when")
_SHOWN_STEP_CHARS = 160
_LISTED_SKILLS = 5  # a plugin's skills named in its description before "and N more"
_ROW_DATE_FORMAT = "%d %b %H:%M"

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
.runs tr { position: relative; display: grid; grid-template-columns: 1fr auto;
           grid-template-areas: "subject status" "test test" "when when";
           gap: .1rem .75rem; padding: .75rem 1.25rem .75rem 1rem;
           border-left: 4px solid transparent; border-bottom: 1px solid var(--rule); }
.runs tr:hover { background: var(--code); }
.runs tr:focus-within { outline: 2px solid var(--focus); outline-offset: -2px; }
.runs td { padding: 0; min-width: 0; }
.runs .row-subject { grid-area: subject; font: 600 1rem/1.25 var(--display); color: var(--ink);
                     overflow-wrap: anywhere; }
.runs .row-subject a { text-decoration: none; outline: none; }
.runs .row-subject a::after { content: ""; position: absolute; inset: 0; }
.runs .row-repeat { font-weight: 400; color: var(--muted); }
.runs .row-status { grid-area: status; font: 600 .85rem var(--display); }
.runs .row-test { grid-area: test; font-size: .82rem; color: var(--ink); overflow: hidden;
                  text-overflow: ellipsis; white-space: nowrap; }
.runs .row-test .kind { color: var(--muted); margin-left: .35rem; }
.runs .row-when { grid-area: when; font-size: .75rem; color: var(--muted);
                  font-variant-numeric: tabular-nums; }
.runs .row-when span + span { margin-left: .6rem; }
.runs .empty { padding: 1.25rem; color: var(--muted); font-size: .9rem; }
.details { padding: 2.5rem clamp(1rem, 4vw, 3.5rem) 4rem; min-width: 0; max-width: 72rem;
            overflow-wrap: anywhere; }
.run-detail { display: none; scroll-margin-top: 1rem; }
.run-detail:target,
body:not(:has(.run-detail:target)) .run-detail:first-child { display: block; }
body:not(:has(.run-detail:target)) .runs tbody tr:first-child { border-left-color: var(--ink);
                                                                  background: var(--bench); }
.subject h2 { font-size: clamp(2rem, 4.5vw, 3.1rem); font-stretch: condensed;
              letter-spacing: -.015em; line-height: 1.05; }
.stamp { display: inline-block; margin-left: .45rem; padding: .05rem .5rem; position: relative;
         top: -.35em; border: 2px solid currentColor; border-radius: 3px; color: var(--muted);
         font: 600 .95rem var(--display); letter-spacing: 0; font-stretch: normal; }
.subject-what { margin: .45rem 0 0; color: var(--muted); max-width: 70ch; }
.test-line { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .8rem;
             margin: 1.6rem 0 0; padding-top: 1.1rem; border-top: 1px solid var(--rule); }
.test-name { font: 600 1.25rem var(--display); overflow-wrap: anywhere; }
.description { margin: .45rem 0 0; max-width: 70ch; }
.setup { margin: .3rem 0 0; color: var(--muted); font-size: .9rem; overflow-wrap: anywhere; }
.steps { list-style: none; counter-reset: step; margin: 1.1rem 0 0; padding: 0; max-width: 76ch; }
.steps li { counter-increment: step; display: grid; gap: .65rem; align-items: center;
            grid-template-columns: 1.5rem minmax(0, 1fr) auto; padding: .18rem 0; }
.steps li::before { content: counter(step); display: grid; place-items: center;
                    width: 1.35rem; height: 1.35rem; border-radius: 50%;
                    background: var(--ink); color: var(--bench);
                    font: 600 .78rem var(--display); }
.steps .step-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.steps .command { font-family: var(--mono); font-size: .82rem; }
.steps .note { font-size: .78rem; color: var(--muted); }
.steps .stopped::before { background: var(--fail); }
.steps .stopped .note { color: var(--fail); }
.steps .not-run { color: var(--muted); }
.steps .not-run::before { background: var(--rule); color: var(--muted); }
.empty-state { max-width: 34rem; }
.empty-state h2 { margin-bottom: .5rem; }
@media (max-width: 760px) {
  .dashboard { display: block; }
  .runs { position: static; height: auto; max-height: 45vh; border-right: 0;
          border-bottom: 1px solid var(--rule); }
}
"""
# Longer texts show this many characters, with the full text behind a collapsed <details>,
# so a run with megabytes of tool output still renders quickly.
_PREVIEW_CHARS = 2000
_UNKNOWN_VALUE = "-"
_COST_DECIMALS = 4
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
# A tool call's summary line quotes this much of its most telling input.
_INPUT_HINT_CHARS = 90
_INPUT_HINT_KEYS = (
    "command",
    "skill",
    "pattern",
    "file_path",
    "path",
    "url",
    "query",
    "description",
    "prompt",
)

# System fonts only: the page must open from disk.
PAGE_STYLE = """
:root {
  --bench: #eef2f4; --panel: #ffffff; --ink: #1e2a33; --muted: #5b6b76; --rule: #d3dbe0;
  --code: #f5f8f9; --pass: #0f7b6c; --fail: #c8431f; --warn: #a15c00; --user: #4b5ba6;
  --user-tint: #eceffa; --focus: #2f6fd0;
  --display: Bahnschrift, "DIN Alternate", "DIN 2014", "Barlow Semi Condensed",
             "Arial Narrow", sans-serif;
  --body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: "Cascadia Code", ui-monospace, "SF Mono", Consolas, monospace;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bench: #141b20; --panel: #1c252b; --ink: #e3eaee; --muted: #93a3ad; --rule: #2e3a42;
    --code: #172026; --pass: #3cc2aa; --fail: #f07a55; --warn: #e0a04a; --user: #9aa8f0;
    --user-tint: #232a45; --focus: #7fb0ff;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bench); color: var(--ink); font: 15px/1.5 var(--body); }
a { color: inherit; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
h1, h2, h3 { font-family: var(--display); font-weight: 600; line-height: 1.2; margin: 0; }
h2 { font-size: 1.75rem; overflow-wrap: anywhere; }
h2 small { display: block; margin-top: .25rem; font: 400 .8rem var(--mono); color: var(--muted); }
h3 { font-size: 1.1rem; margin: 2rem 0 .75rem; }
h4 { margin: .75rem 0 .25rem; font-size: .8rem; font-weight: 600; color: var(--muted); }
code, pre, summary { font-family: var(--mono); font-size: .82rem; }
pre { margin: 0; padding: .5rem .65rem; background: var(--code); border-radius: 3px;
      white-space: pre-wrap; word-break: break-word; max-height: 28rem; overflow: auto; }

.status-pass { color: var(--pass); }
.status-fail { color: var(--fail); }
.status-error, .status-timeout { color: var(--warn); }
.status { font-family: var(--display); font-weight: 600; font-size: 1rem; padding: .1rem .55rem;
          border: 2px solid currentColor; border-radius: 3px; }

.meter { display: flex; flex-wrap: wrap; gap: .75rem 2rem; margin: 1.25rem 0 0; padding: 0; }
.meter div { min-width: 0; }
.meter dt { font-size: .78rem; color: var(--muted); }
.meter dd { margin: 0; font-family: var(--display); font-size: 1.15rem;
            font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.workspace { margin: .9rem 0 0; color: var(--muted); font-size: .85rem; overflow-wrap: anywhere; }
.workspace code { color: var(--ink); }
.run-error, .error { color: var(--fail); overflow-wrap: anywhere; }
.run-error { margin: 1rem 0 0; padding: .5rem .75rem; border-left: 3px solid var(--fail);
             background: var(--panel); white-space: pre-wrap; }

.assertions { list-style: none; margin: 0; padding: 0; background: var(--panel);
              border: 1px solid var(--rule); border-radius: 4px; }
.assertions li { display: grid; grid-template-columns: 1.4rem 1fr; gap: 0 .5rem;
                 padding: .5rem .75rem; border-top: 1px solid var(--rule); }
.assertions li:first-child { border-top: 0; }
.assertions .verdict { grid-row: span 2; font-family: var(--display); font-weight: 700;
                       font-size: 0; }
.assertions .verdict::before { font-size: 1.1rem; }
.assertions .pass .verdict { color: var(--pass); }
.assertions .pass .verdict::before { content: "\\2713"; }
.assertions .fail .verdict { color: var(--fail); }
.assertions .fail .verdict::before { content: "\\2717"; }
.assertions li > * { min-width: 0; }
.assertions code { overflow-wrap: anywhere; }
.assertions .reason { color: var(--muted); font-size: .88rem; overflow-wrap: anywhere; }
.assertions .fail .reason { color: var(--ink); }
.tally { font-family: var(--body); font-weight: 400; font-size: .85rem; color: var(--muted);
         margin-left: .5rem; }

/* The timeline: one spine, a numbered stop per conversation turn. */
.turns { list-style: none; margin: 0; padding: 0 0 0 2.6rem; position: relative; }
.turns::before { content: ""; position: absolute; left: .95rem; top: .4rem; bottom: .4rem;
                 width: 2px; background: var(--rule); }
.turn { position: relative; padding-bottom: 1.5rem; }
.turn-head { display: flex; align-items: baseline; gap: .5rem; margin: 0 0 .6rem;
             font-family: var(--display); font-size: 1rem; font-weight: 600;
             color: var(--ink); }
.turn-no { position: absolute; left: -2.6rem; top: -.15rem; width: 2rem; height: 2rem;
           display: grid; place-items: center; border-radius: 50%; background: var(--ink);
           color: var(--bench); font: 600 1rem var(--display); }
.turn-answer .turn-no { background: var(--user); }
.item { margin: .4rem 0; }
.text { white-space: pre-wrap; background: var(--panel); padding: .6rem .8rem;
        border-radius: 4px; border: 1px solid var(--rule); max-width: 75ch; }
.user { white-space: pre-wrap; background: var(--user-tint); color: var(--ink);
        padding: .6rem .8rem; border-radius: 4px; border-left: 3px solid var(--user);
        max-width: 75ch; }
.unknown { color: var(--muted); font-size: .8rem; }
details { background: var(--panel); border: 1px solid var(--rule); border-radius: 4px;
          padding: .35rem .7rem; }
details[open] { padding-bottom: .6rem; }
summary { cursor: pointer; display: flex; gap: .6rem; align-items: baseline; min-width: 0; }
summary::marker { color: var(--muted); }
.tool { font-weight: 700; }
.hint { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        min-width: 0; flex: 1; }
.outcome { margin-left: auto; font-size: .75rem; color: var(--pass); }
.outcome.error, .outcome.none { color: var(--fail); }
.outcome.none { color: var(--muted); }
details details { border-style: dashed; margin-top: .4rem; }
.children { margin: .7rem 0 0 .3rem; padding-left: .9rem; border-left: 2px solid var(--user); }

@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
"""


@dataclass(frozen=True)
class _RunView:
    """One run as the dashboard shows it: a table row and a detail section."""

    run_id: str
    status: str
    sort_time: datetime
    # Runs of one test in one invocation (its repeats) share a group.
    group: str
    row_html: str
    detail_html: str


def write_dashboard(runs_root: Path) -> None:
    """(Re)write runs_root/index.html from every run directory found under runs_root."""
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dirs = [entry for entry in runs_root.iterdir() if entry.is_dir()]
    views = [view for run_dir in run_dirs if (view := _view_run(run_dir)) is not None]
    views = _newest_first_by_group(views)
    (runs_root / INDEX_FILE_NAME).write_text(_render_page(views), encoding="utf-8")


def _newest_first_by_group(views: list[_RunView]) -> list[_RunView]:
    """Groups ordered by their newest run, newest first; within a group, newest first too."""
    group_times: dict[str, datetime] = {}
    for view in views:
        group_times[view.group] = max(view.sort_time, group_times.get(view.group, view.sort_time))
    return sorted(
        views,
        key=lambda view: (group_times[view.group], view.group, view.sort_time),
        reverse=True,
    )


def _view_run(run_dir: Path) -> _RunView | None:
    """The run's view, or None if its run.json is missing or can't be shown (it may still be
    running, or was interrupted)."""
    try:
        record = json.loads((run_dir / _RUN_RECORD_FILE_NAME).read_text(encoding="utf-8"))
        return _render_run(run_dir.name, record)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _render_run(run_id: str, record: dict[str, Any]) -> _RunView:
    """Raises KeyError, TypeError or ValueError if the record is malformed."""
    status = record["status"]
    when = (
        f"<span>{escape(format_seconds(record['duration_s']))}</span>"
        f"<span>{escape(_row_date(record['start_time']))}</span>"
    )
    row = (
        "<tr>"
        f'<td class="row-subject">{_detail_link(run_id, _subject_title(record))}'
        f"{_repeat_note(record)}</td>"
        f'<td class="row-status">{_status_cell(status)}</td>'
        f'<td class="row-test">{_row_test(record)}</td>'
        f'<td class="row-when">{when}</td>'
        "</tr>"
    )
    eval_id = record.get("eval_id")
    return _RunView(
        run_id=run_id,
        status=status,
        sort_time=datetime.fromisoformat(record["start_time"]).astimezone(),
        group=f"{eval_id}/{record['test_name']}" if eval_id else run_id,
        row_html=row,
        detail_html=_render_detail(run_id, record),
    )


def _repeat_note(record: dict[str, Any]) -> str:
    repeat_count = record.get("repeat_count") or 1
    if repeat_count == 1:
        return ""
    return f' <span class="row-repeat">#{record.get("repeat_index", 1)}/{repeat_count}</span>'


def _row_test(record: dict[str, Any]) -> str:
    """Which test: a trigger test by its prompt, any other by its name and kind."""
    trigger = record.get("trigger")
    if trigger:
        verdict = "should pick it" if trigger["should_invoke"] else "shouldn't pick it"
        return f'{escape(verdict)}: <span class="kind">"{escape(trigger["prompt"])}"</span>'
    name = escape(record["test_name"])
    kind = record.get("kind")
    return f'{name}<span class="kind">{escape(kind)} test</span>' if kind else name


def _row_date(start_time: str) -> str:
    return datetime.fromisoformat(start_time).astimezone().strftime(_ROW_DATE_FORMAT)


def _detail_id(run_id: str) -> str:
    return f"run-{run_id}"


def _detail_link(run_id: str, label: str) -> str:
    return f'<a href="#{escape(_detail_id(run_id))}">{escape(label)}</a>'


def _status_cell(status: str) -> str:
    return f'<span class="status-{escape(status)}">{escape(status)}</span>'


def _render_detail(run_id: str, record: dict[str, Any]) -> str:
    """A run's page: what it tests and how, first; then its results."""
    return (
        f'<section class="run-detail" id="{escape(_detail_id(run_id))}">'
        f"{_render_subject(record)}{_render_test(record)}{_render_run_detail(record)}</section>"
    )


# --- what a run tests ---------------------------------------------------------------------


def _subject_title(record: dict[str, Any]) -> str:
    """What's under test, short: `grilling skill`, `overpowered 0.2.0`, `This repo's setup`."""
    name, stamp, _ = _subject(record)
    return f"{name} {stamp}" if stamp else name


def _render_subject(record: dict[str, Any]) -> str:
    """The thing being tested, as the page's headline, with a sentence saying what it is."""
    name, stamp, sentence = _subject(record)
    stamp_html = f'<span class="stamp">{escape(stamp)}</span>' if stamp else ""
    sentence_html = f'<p class="subject-what">{escape(sentence)}</p>' if sentence else ""
    return f'<header class="subject"><h2>{escape(name)}{stamp_html}</h2>{sentence_html}</header>'


def _subject(record: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """The subject's name, its stamp (`skill`, or a plugin's version) and a sentence about it.
    Runs recorded before subjects existed show only their test name."""
    subject = record.get("subject")
    if not subject:
        return record["test_name"], None, None
    if subject.get("skill"):
        return subject["skill"], "skill", _skill_sentence(subject, bool(record.get("trigger")))
    if subject["kind"] == "plugins" and len(subject["plugins"]) == 1:
        plugin = subject["plugins"][0]
        return plugin["name"], plugin.get("version"), _plugin_sentence(plugin)
    if subject["kind"] == "plugins":
        titles = ", ".join(_plugin_title(plugin) for plugin in subject["plugins"])
        return titles, None, "Plugins under test, loaded together."
    return "This repo's setup", None, _repo_sentence(subject)


def _plugin_title(plugin: dict[str, Any]) -> str:
    return f"{plugin['name']} {plugin['version']}" if plugin.get("version") else plugin["name"]


def _skill_sentence(subject: dict[str, Any], is_trigger: bool) -> str:
    what = "Tests whether Claude picks this skill on its own" if is_trigger else "Skill under test"
    plugin = subject.get("plugin")
    return f"{what}, from the {_plugin_title(plugin)} plugin." if plugin else f"{what}."


def _plugin_sentence(plugin: dict[str, Any]) -> str:
    """e.g. `Plugin under test, with 5 skills (understand, build, …) and the reviewer agent.`"""
    skills = plugin.get("skills") or []
    agents = plugin.get("agents") or []
    parts = []
    if skills:
        listed = ", ".join(skills[:_LISTED_SKILLS])
        more = f" and {len(skills) - _LISTED_SKILLS} more" if len(skills) > _LISTED_SKILLS else ""
        parts.append(f"{plural(len(skills), 'skill')} ({listed}{more})")
    if agents:
        parts.append(
            f"the {agents[0]} agent" if len(agents) == 1 else f"agents {', '.join(agents)}"
        )
    return f"Plugin under test, with {' and '.join(parts)}." if parts else "Plugin under test."


def _repo_sentence(subject: dict[str, Any]) -> str:
    skills, agents = subject.get("skills") or [], subject.get("agents") or []
    if not skills and not agents:
        return "No skills or agents were found in the repo, so Claude worked with its defaults."
    found = [plural(len(skills), "skill"), plural(len(agents), "agent")]
    return f"Everything the repo itself has: {' and '.join(found)}."


def _render_test(record: dict[str, Any]) -> str:
    """The test: its name and status, its description, where it ran, and its steps."""
    status = record["status"]
    description = record.get("description")
    html = (
        '<div class="test-line">'
        f'<span class="test-name">{escape(record["test_name"])}{_repeat_note(record)}</span>'
        f'<span class="status status-{escape(status)}">{escape(status)}</span></div>'
    )
    if description:
        html += f'<p class="description">{escape(description)}</p>'
    if record.get("kind"):
        html += f'<p class="setup">{escape(_setup_sentence(record))}</p>'
    return html + _render_steps(record)


def _setup_sentence(record: dict[str, Any]) -> str:
    """e.g. `Agent test on github.com/you/app at cd5def0`."""
    kind = "Trigger" if record.get("trigger") else str(record["kind"]).capitalize()
    fixture = record.get("fixture")
    if fixture is None:
        where = "in an empty repo"
    elif is_git_url(fixture):
        where = f"on {_repo_address(fixture)}"
    else:
        where = f"on a copy of {Path(fixture).name}"
    commit = record.get("fixture_commit")
    return f"{kind} test {where}" + (f" at {commit[:7]}" if commit else "")


def _repo_address(fixture: str) -> str:
    """`https://github.com/you/app.git@v1` -> `github.com/you/app` (the commit is shown apart)."""
    address = fixture.split("://", 1)[-1]
    host_and_path, _, ref = address.rpartition("@")
    if ref and "/" in host_and_path:
        address = host_and_path
    return address.removesuffix(".git")


def _render_steps(record: dict[str, Any]) -> str:
    """The prompt and every answer, numbered. The step a failed run stopped at is marked, and
    steps after it show as not run."""
    if "planned_answers" not in record or not record.get("prompt"):
        return ""
    steps = [record["prompt"], *record["planned_answers"]]
    taken = steps_taken(record)
    stopped_at = taken if record["status"] == "error" and taken else None
    items = []
    for number, text in enumerate(steps, start=1):
        state, note = "", ""
        if number == stopped_at:
            state, note = "stopped", "stopped here"
        elif number > taken:
            state, note = "not-run", "not run"
        text_class = "step-text command" if text.startswith(("/", "!")) else "step-text"
        shown = escape(shorten(text, _SHOWN_STEP_CHARS))
        items.append(
            f'<li class="{state}"><span class="{text_class}" title="{escape(text)}">'
            f'{shown}</span><span class="note">{note}</span></li>'
        )
    return f'<ol class="steps" aria-label="Steps">{"".join(items)}</ol>'


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


# --- one run's detail: readout, assertions and timeline --------------------------------


def _render_run_detail(record: dict[str, Any]) -> str:
    """Why it didn't pass, the checks, the numbers, then the full timeline of one run."""
    return (
        f"{_render_why_not_passed(record)}"
        f"{_render_assertions(record['assertions'])}"
        f"{_render_readout(record)}"
        "<h3>Timeline</h3>"
        f"{_render_timeline(record['trace']['items'])}"
    )


def format_optional(value: Any) -> str:
    return _UNKNOWN_VALUE if value is None else str(value)


def format_start_time(start_time: str) -> str:
    return datetime.fromisoformat(start_time).astimezone().strftime(_DATE_FORMAT)


def format_cost_and_usage(trace: dict[str, Any]) -> str:
    usage = trace["usage"]
    parts = []
    if trace["cost_usd"] is not None:
        parts.append(f"${trace['cost_usd']:.{_COST_DECIMALS}f}")
    if "input_tokens" in usage or "output_tokens" in usage:
        input_tokens = format_optional(usage.get("input_tokens"))
        output_tokens = format_optional(usage.get("output_tokens"))
        parts.append(f"{input_tokens} in / {output_tokens} out tokens")
    return ", ".join(parts) if parts else format_optional(None)


def _render_readout(record: dict[str, Any]) -> str:
    trace = record["trace"]
    readings = (
        ("Model", format_optional(record["model"])),
        ("Duration", format_seconds(record["duration_s"])),
        ("Turns", format_optional(trace["num_turns"])),
        ("Cost and usage", format_cost_and_usage(trace)),
        ("Started", format_start_time(record["start_time"])),
    )
    cells = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>" for label, value in readings
    )
    return f'<dl class="meter">{cells}</dl>{_render_workspace(record)}'


def _render_why_not_passed(record: dict[str, Any]) -> str:
    """For a run that didn't pass: where it stopped and the full error, or which checks failed;
    then what Claude said last."""
    if record["status"] == "pass":
        return ""
    if error := run_error(record):
        steps = steps_taken(record)
        headline = f"Stopped at step {steps}" if steps else "Couldn't start"
        html = f'<p class="run-error"><strong>{escape(headline)}</strong><br>{escape(error)}</p>'
    else:
        html = f'<p class="run-error"><strong>{escape(why_not_passed(record) or "")}</strong></p>'
    last_message = final_text(record["trace"]["items"])
    if last_message:
        html += (
            "<h4>Claude's last message</h4>"
            f'<div class="item"><div class="text">{escape(last_message)}</div></div>'
        )
    return html


def _render_workspace(record: dict[str, Any]) -> str:
    """The temp folder the run worked in, kept so its files can be opened."""
    if not record.get("workspace"):
        return ""
    return f'<p class="workspace">Workspace <code>{escape(record["workspace"])}</code></p>'


def _render_assertions(results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    passed = sum(1 for result in results if result["passed"])
    entries = "".join(_render_assertion(result) for result in results)
    return (
        f'<h3>Assertions<span class="tally">{passed} of {len(results)} passed</span></h3>'
        f'<ul class="assertions">{entries}</ul>'
    )


def _render_assertion(result: dict[str, Any]) -> str:
    outcome = "pass" if result["passed"] else "fail"
    expected = json.dumps(result["expected"])
    return (
        f'<li class="{outcome}"><span class="verdict">{outcome}</span>'
        f"<code>{escape(result['type'])}: {escape(expected)}</code>"
        f'<span class="reason">{escape(result["reason"])}</span></li>'
    )


def _render_timeline(items: list[dict[str, Any]]) -> str:
    """Items split into turns: the first starts with the test prompt, each later one with an
    answer."""
    turns: list[tuple[dict[str, Any] | None, list[dict[str, Any]]]] = [(None, [])]
    for item in items:
        if item["kind"] == "user_message":
            turns.append((item, []))
        else:
            turns[-1][1].append(item)
    return '<ol class="turns">' + "".join(_render_turn(*turn) for turn in turns) + "</ol>"


def _render_turn(answer: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    body = _render_items(items) or '<p class="unknown">No events recorded.</p>'
    if answer is None:
        return (
            '<li class="turn"><span class="turn-no">1</span>'
            f'<p class="turn-head">Test prompt</p>{body}</li>'
        )
    label = f"{answer['source'].capitalize()} answer"
    return (
        f'<li class="turn turn-answer"><span class="turn-no">{escape(str(answer["turn"]))}</span>'
        f'<p class="turn-head">{escape(label)}</p>'
        f'<div class="item"><div class="user">{escape(answer["text"])}</div></div>{body}</li>'
    )


def _render_items(items: list[dict[str, Any]]) -> str:
    """Each item in order; runs of events skillbench doesn't display collapse to one line."""
    parts: list[str] = []
    skipped: list[str] = []
    for item in items:
        if item["kind"] == "unknown":
            skipped.append(item["raw_type"])
            continue
        if skipped:
            parts.append(_render_skipped(skipped))
            skipped = []
        parts.append(f'<div class="item">{_render_item(item)}</div>')
    if skipped:
        parts.append(_render_skipped(skipped))
    return "".join(parts)


def _render_skipped(raw_types: list[str]) -> str:
    counts = Counter(raw_types)
    listed = ", ".join(
        name if count == 1 else f"{name} ({count})" for name, count in counts.items()
    )
    return f'<div class="item unknown">Other events: {escape(listed)}</div>'


def _render_item(item: dict[str, Any]) -> str:
    if item["kind"] == "assistant_text":
        return f'<div class="text">{escape(item["text"])}</div>'
    if item["kind"] == "tool_call":
        return _render_tool_call(item)
    if item["kind"] == "user_message":
        label = f"{item['source'].capitalize()} answer"
        return f'<div class="user"><strong>{escape(label)}:</strong> {escape(item["text"])}</div>'
    if item["kind"] == "user_command":
        return _render_user_command(item)
    if item["kind"] == "new_session":
        return '<div class="user"><strong>/clear</strong>: a fresh session starts here</div>'
    return f'<div class="unknown">Other event: {escape(item["raw_type"])}</div>'


def _render_user_command(item: dict[str, Any]) -> str:
    """A `!command` answer: run in the workspace by skillbench, between turns."""
    is_ok = item["exit_code"] == 0
    outcome = "ok" if is_ok else f"exit {item['exit_code']}"
    return (
        '<details class="user-command">'
        f'<summary><span class="tool">!</span><span class="hint">{escape(item["command"])}</span>'
        f'<span class="outcome {"ok" if is_ok else "error"}">{outcome}</span></summary>'
        f"<h4>Output</h4>{_render_long_text(item['output'])}</details>"
    )


def _render_tool_call(tool_call: dict[str, Any]) -> str:
    outcome, outcome_class = {
        True: ("ok", "ok"),
        False: ("error", "error"),
        None: ("no result", "none"),
    }[tool_call["ok"]]
    children = tool_call["children"]
    children_html = f'<div class="children">{_render_items(children)}</div>' if children else ""
    output = tool_call["output"] if tool_call["output"] is not None else ""
    return (
        "<details open>"
        f'<summary><span class="tool">{escape(tool_call["name"])}</span>'
        f'<span class="hint">{escape(_input_hint(tool_call["input"]))}</span>'
        f'<span class="outcome {outcome_class}">{outcome}</span></summary>'
        f"<h4>Input</h4>{_render_long_text(json.dumps(tool_call['input'], indent=2))}"
        f"<h4>Output</h4>{_render_long_text(output)}"
        f"{children_html}"
        "</details>"
    )


def _input_hint(tool_input: Any) -> str:
    """The input value that says most about a call (its command, pattern, path...), one line."""
    if not isinstance(tool_input, dict):
        return ""
    keys = [key for key in _INPUT_HINT_KEYS if key in tool_input] + list(tool_input)
    for key in keys:
        value = tool_input[key]
        if isinstance(value, str) and value.strip():
            line = " ".join(value.split())
            if len(line) > _INPUT_HINT_CHARS:
                return line[:_INPUT_HINT_CHARS] + "…"
            return line
    return ""


def _render_long_text(text: str) -> str:
    if len(text) <= _PREVIEW_CHARS:
        return f"<pre>{escape(text)}</pre>"
    return (
        f"<pre>{escape(text[:_PREVIEW_CHARS])}&hellip;</pre>"
        f"<details><summary>Show all {len(text):,} characters</summary>"
        f"<pre>{escape(text)}</pre></details>"
    )
