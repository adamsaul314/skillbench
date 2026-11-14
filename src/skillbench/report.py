"""Rendering a run record as a self-contained HTML report (no network, no scripts)."""

import json
from html import escape
from typing import Any

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 70rem; color: #1a1a1a;
       background: #fafafa; }
h1 { font-size: 1.4rem; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; }
dt { font-weight: 600; }
.item { margin: .5rem 0; }
.text { white-space: pre-wrap; background: #fff; border-left: 3px solid #4a7; padding: .5rem; }
.unknown { color: #888; font-style: italic; }
details { background: #fff; border: 1px solid #ddd; border-radius: 4px; padding: .3rem .6rem; }
summary { cursor: pointer; font-family: monospace; }
.error { color: #b00; }
.children { margin-left: 1.5rem; border-left: 2px dashed #bbb; padding-left: .8rem; }
pre { white-space: pre-wrap; word-break: break-word; background: #f3f3f3; padding: .4rem; }
"""

_SUMMARY_FIELDS = (
    "test_name",
    "backend",
    "model",
    "status",
    "exit_code",
    "session_id",
    "start_time",
    "end_time",
    "duration_s",
    "workspace",
)


def render_report(record: dict[str, Any]) -> str:
    trace = record["trace"]
    title = escape(f"skillbench: {record['test_name']}")
    return (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{_STYLE}</style></head><body>"
        f"<h1>{title}</h1>"
        f"{_render_summary(record)}"
        "<h2>Timeline</h2>"
        f"{_render_items(trace['items'])}"
        "</body></html>\n"
    )


def _render_summary(record: dict[str, Any]) -> str:
    trace = record["trace"]
    rows = [(key, record.get(key)) for key in _SUMMARY_FIELDS]
    rows += [
        ("cost_usd", trace.get("cost_usd")),
        ("num_turns", trace.get("num_turns")),
        ("usage", json.dumps(trace.get("usage"))),
        ("plugins", json.dumps(record.get("plugins"))),
        ("skills", json.dumps(record.get("skills"))),
    ]
    cells = "".join(f"<dt>{escape(key)}</dt><dd>{escape(str(value))}</dd>" for key, value in rows)
    return f"<dl>{cells}</dl>"


def _render_items(items: list[dict[str, Any]]) -> str:
    return "".join(f'<div class="item">{_render_item(item)}</div>' for item in items)


def _render_item(item: dict[str, Any]) -> str:
    if item["kind"] == "assistant_text":
        return f'<div class="text">{escape(item["text"])}</div>'
    if item["kind"] == "tool_call":
        return _render_tool_call(item)
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
        f"<h4>input</h4><pre>{escape(json.dumps(tool_call['input'], indent=2))}</pre>"
        f"<h4>output</h4><pre>{escape(output)}</pre>"
        f"{children_html}"
        "</details>"
    )
