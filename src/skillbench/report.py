"""Rendering one run record as HTML for the dashboard (no network, no scripts)."""

import json
from collections import Counter
from datetime import datetime
from html import escape
from typing import Any

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
.status-error, .status-timeout, .status-broken { color: var(--warn); }
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
.assertions .reason { color: var(--muted); font-size: .88rem; }
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


def render_run_detail(record: dict[str, Any]) -> str:
    """Readout, assertion results and the full timeline of one run."""
    return (
        f"{_render_readout(record)}"
        f"{_render_assertions(record['assertions'])}"
        "<h3>Timeline</h3>"
        f"{_render_timeline(record['trace']['items'])}"
    )


def format_duration(duration_s: float) -> str:
    return f"{duration_s:.1f}s"


def format_optional(value: Any) -> str:
    return _UNKNOWN_VALUE if value is None else str(value)


def format_start_time(start_time: str) -> str:
    return datetime.fromisoformat(start_time).astimezone().strftime(_DATE_FORMAT)


def format_cost_and_usage(trace: dict[str, Any]) -> str:
    usage = trace["usage"]
    parts = []
    if trace["cost_usd"] is not None:
        parts.append(f"${trace['cost_usd']:.{_COST_DECIMALS}f}")
    if trace.get("premium_requests") is not None:
        parts.append(f"{trace['premium_requests']} premium requests")
    if "input_tokens" in usage or "output_tokens" in usage:
        input_tokens = format_optional(usage.get("input_tokens"))
        output_tokens = format_optional(usage.get("output_tokens"))
        parts.append(f"{input_tokens} in / {output_tokens} out tokens")
    return ", ".join(parts) if parts else format_optional(None)


def _render_readout(record: dict[str, Any]) -> str:
    trace = record["trace"]
    readings = (
        ("Backend", record["backend"]),
        ("Model", format_optional(record["model"])),
        ("Duration", format_duration(record["duration_s"])),
        ("Turns", format_optional(trace["num_turns"])),
        ("Cost and usage", format_cost_and_usage(trace)),
        ("Started", format_start_time(record["start_time"])),
    )
    status = record["status"]
    cells = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>" for label, value in readings
    )
    error = record.get("error")
    error_html = f'<p class="run-error">{escape(str(error))}</p>' if error else ""
    workspace = record.get("workspace")
    workspace_html = (
        f'<p class="workspace">Workspace <code>{escape(str(workspace))}</code></p>'
        if workspace
        else ""
    )
    fixture = record.get("fixture")
    if fixture:
        commit = record.get("fixture_commit")
        at_commit = f" at <code>{escape(commit)}</code>" if commit else ""
        workspace_html += (
            f'<p class="workspace">Fixture <code>{escape(str(fixture))}</code>{at_commit}</p>'
        )
    return (
        f'<dl class="meter"><div><dt>Status</dt><dd><span class="status status-{escape(status)}">'
        f"{escape(status)}</span></dd></div>{cells}</dl>{error_html}{workspace_html}"
    )


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
    return f'<div class="unknown">Other event: {escape(item["raw_type"])}</div>'


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
