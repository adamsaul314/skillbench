"""Rendering a run record as self-contained HTML (no network, no scripts).

`render_run_detail` is the part shared by the per-run report.html and the runs dashboard.
"""

import json
from html import escape
from typing import Any

# Longer texts show this many characters, with the full text behind a collapsed <details>,
# so a run with megabytes of tool output still renders quickly.
_PREVIEW_CHARS = 2000
_UNKNOWN_VALUE = "-"

PAGE_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 70rem; color: #1a1a1a;
       background: #fafafa; }
h1 { font-size: 1.4rem; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; }
dt { font-weight: 600; }
.item { margin: .5rem 0; }
.text { white-space: pre-wrap; background: #fff; border-left: 3px solid #4a7; padding: .5rem; }
.unknown { color: #888; font-style: italic; }
.user { white-space: pre-wrap; background: #eef3fb; border-left: 3px solid #47a; padding: .5rem; }
details { background: #fff; border: 1px solid #ddd; border-radius: 4px; padding: .3rem .6rem; }
summary { cursor: pointer; font-family: monospace; }
.error { color: #b00; }
.children { margin-left: 1.5rem; border-left: 2px dashed #bbb; padding-left: .8rem; }
pre { white-space: pre-wrap; word-break: break-word; background: #f3f3f3; padding: .4rem; }
.assertions .pass { color: #262; }
.assertions .fail { color: #b00; }
"""

_SUMMARY_FIELDS = (
    "test_name",
    "eval_id",
    "backend",
    "model",
    "status",
    "run_status",
    "exit_code",
    "session_id",
    "start_time",
    "end_time",
    "duration_s",
    "workspace",
)


def render_report(record: dict[str, Any]) -> str:
    title = escape(f"skillbench: {record['test_name']}")
    return (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{PAGE_STYLE}</style></head><body>"
        f"<h1>{title}</h1>"
        f"{render_run_detail(record)}"
        "</body></html>\n"
    )


def render_run_detail(record: dict[str, Any]) -> str:
    """Summary, assertion results and the full timeline of one run."""
    return (
        f"{_render_summary(record)}"
        f"{_render_assertions(record['assertions'])}"
        "<h3>Timeline</h3>"
        f"{_render_items(record['trace']['items'])}"
    )


def format_duration(duration_s: float) -> str:
    return f"{duration_s:.1f}s"


def format_optional(value: Any) -> str:
    return _UNKNOWN_VALUE if value is None else str(value)


def _render_summary(record: dict[str, Any]) -> str:
    trace = record["trace"]
    rows = [(key, record.get(key)) for key in _SUMMARY_FIELDS]
    rows += [
        ("cost_usd", trace.get("cost_usd")),
        ("premium_requests", trace.get("premium_requests")),
        ("num_turns", trace.get("num_turns")),
        ("usage", json.dumps(trace.get("usage"))),
        ("plugins", json.dumps(record.get("plugins"))),
        ("skills", json.dumps(record.get("skills"))),
    ]
    cells = "".join(f"<dt>{escape(key)}</dt><dd>{escape(str(value))}</dd>" for key, value in rows)
    return f"<dl>{cells}</dl>"


def _render_assertions(results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    entries = "".join(_render_assertion(result) for result in results)
    return f'<h3>Assertions</h3><ul class="assertions">{entries}</ul>'


def _render_assertion(result: dict[str, Any]) -> str:
    outcome = "pass" if result["passed"] else "fail"
    expected = json.dumps(result["expected"])
    return (
        f'<li class="{outcome}"><strong>{outcome}</strong> '
        f"{escape(result['type'])}: {escape(expected)} &mdash; {escape(result['reason'])}</li>"
    )


def _render_items(items: list[dict[str, Any]]) -> str:
    return "".join(f'<div class="item">{_render_item(item)}</div>' for item in items)


def _render_item(item: dict[str, Any]) -> str:
    if item["kind"] == "assistant_text":
        return f'<div class="text">{escape(item["text"])}</div>'
    if item["kind"] == "tool_call":
        return _render_tool_call(item)
    if item["kind"] == "user_message":
        label = f"turn {item['turn']}, {item['source']} answer"
        return f'<div class="user"><strong>{escape(label)}:</strong> {escape(item["text"])}</div>'

    return f'<div class="unknown">unknown event: {escape(item["raw_type"])}</div>'


def _render_tool_call(tool_call: dict[str, Any]) -> str:
    outcome = {True: "ok", False: "error", None: "no result"}[tool_call["ok"]]
    outcome_class = "error" if tool_call["ok"] is False else ""
    children = tool_call["children"]
    children_html = f'<div class="children">{_render_items(children)}</div>' if children else ""
    output = tool_call["output"] if tool_call["output"] is not None else ""
    return (
        "<details open>"
        f'<summary>{escape(tool_call["name"])} <span class="{outcome_class}">[{outcome}]</span>'
        "</summary>"
        f"<h4>input</h4>{_render_long_text(json.dumps(tool_call['input'], indent=2))}"
        f"<h4>output</h4>{_render_long_text(output)}"
        f"{children_html}"
        "</details>"
    )


def _render_long_text(text: str) -> str:
    if len(text) <= _PREVIEW_CHARS:
        return f"<pre>{escape(text)}</pre>"
    return (
        f"<pre>{escape(text[:_PREVIEW_CHARS])}&hellip;</pre>"
        f"<details><summary>show all {len(text):,} characters</summary>"
        f"<pre>{escape(text)}</pre></details>"
    )
