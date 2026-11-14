"""The runs/index.html dashboard: lists every run and drills down into each run's timeline."""

import json
from html.parser import HTMLParser
from pathlib import Path

from skillbench.cli import main

from conftest import (
    assert_loads_nothing_from_the_network,
    load_run_record,
    run_skillbench,
    write_test_yaml,
)

DASHBOARD_COLUMNS = [
    "date",
    "test",
    "backend",
    "model",
    "status",
    "duration",
    "turns",
    "cost / usage",
]


class _RunsTableParser(HTMLParser):
    """Collects the dashboard's runs table: header names, and per row its cells and link."""

    def __init__(self) -> None:
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[dict] = []
        self._cell: list[str] | None = None
        self._row: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "th":
            self._cell = []
        elif tag == "tr" and self.headers:
            self._row = {"cells": [], "href": None}
        elif tag == "td" and self._row is not None:
            self._cell = []
        elif tag == "a" and self._row is not None and self._row["href"] is None:
            self._row["href"] = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "th" and self._cell is not None:
            self.headers.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "td" and self._row is not None and self._cell is not None:
            self._row["cells"].append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


class _ElementTextParser(HTMLParser):
    """Text inside the element with a given id, split by whether a closed <details> hides it."""

    def __init__(self, element_id: str) -> None:
        super().__init__()
        self._element_id = element_id
        self._depth = 0  # nesting depth inside the target element; 0 = outside
        self._details_open_stack: list[bool] = []
        self.visible: list[str] = []
        self.collapsed: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self._depth == 0:
            if attributes.get("id") == self._element_id:
                self._depth = 1
            return
        self._depth += 1
        if tag == "details":
            self._details_open_stack.append("open" in attributes)

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        self._depth -= 1
        if tag == "details" and self._details_open_stack:
            self._details_open_stack.pop()

    def handle_data(self, data):
        if self._depth == 0:
            return
        is_hidden = not all(self._details_open_stack)
        (self.collapsed if is_hidden else self.visible).append(data)

    @property
    def text(self) -> str:
        return "".join(self.visible) + "".join(self.collapsed)


def _dashboard_html() -> str:
    return (Path.cwd() / "runs" / "index.html").read_text(encoding="utf-8")


def _runs_table(html: str) -> list[dict]:
    """Each run row as {column name: cell text, "href": its link}."""
    parser = _RunsTableParser()
    parser.feed(html)
    assert parser.headers == DASHBOARD_COLUMNS
    return [
        {**dict(zip(parser.headers, row["cells"], strict=True)), "href": row["href"]}
        for row in parser.rows
    ]


def _run_detail(html: str, row: dict) -> _ElementTextParser:
    """The detail view the row's link selects."""
    assert row["href"] and row["href"].startswith("#")
    parser = _ElementTextParser(row["href"][1:])
    parser.feed(html)
    assert parser.text, f"no element with id {row['href'][1:]!r}"
    return parser


def _run(fake_claude, monkeypatch, name: str, *streams: str, **spec) -> dict:
    fake_claude.use_streams(monkeypatch, *(streams or ("basic.jsonl",)))
    test_dir = Path.cwd() / "tests-yaml"
    test_dir.mkdir(exist_ok=True)
    test_path = write_test_yaml(test_dir, {"name": name, "prompt": "/x", **spec}, f"{name}.yaml")
    run_skillbench(str(test_path))
    return _record_for(name)


def _record_for(test_name: str) -> dict:
    for run_json in (Path.cwd() / "runs").glob("*/run.json"):
        record = json.loads(run_json.read_text(encoding="utf-8"))
        if record["test_name"] == test_name:
            return record
    raise AssertionError(f"no run recorded for {test_name}")


def _set_start_time(record: dict, start_time: str) -> None:
    run_json = Path.cwd() / "runs" / record["run_id"] / "run.json"
    run_json.write_text(json.dumps({**record, "start_time": start_time}), encoding="utf-8")


def _row_for(rows: list[dict], test_name: str) -> dict:
    [row] = [row for row in rows if row["test"] == test_name]
    return row


def test_ac4_1_every_run_regenerates_the_dashboard_with_all_runs(fake_claude, monkeypatch):
    _run(fake_claude, monkeypatch, "first")
    assert [row["test"] for row in _runs_table(_dashboard_html())] == ["first"]

    _run(fake_claude, monkeypatch, "second")

    assert sorted(row["test"] for row in _runs_table(_dashboard_html())) == ["first", "second"]


def test_ac4_1_row_shows_the_runs_summary_fields(fake_claude, monkeypatch):
    record = _run(fake_claude, monkeypatch, "summary")

    row = _row_for(_runs_table(_dashboard_html()), "summary")

    assert row["date"].startswith(record["start_time"][:10])
    assert row["backend"] == "claude"
    assert row["model"] == "claude-sonnet-5-5"
    assert row["status"] == "pass"
    assert row["duration"] == f"{record['duration_s']:.1f}s"
    assert row["turns"] == "3"
    assert "$0.0123" in row["cost / usage"]
    assert "42 in" in row["cost / usage"]
    assert "17 out" in row["cost / usage"]


def test_ac4_1_runs_are_listed_newest_first_by_start_time(fake_claude, monkeypatch):
    oldest = _run(fake_claude, monkeypatch, "a")
    newest = _run(fake_claude, monkeypatch, "b")
    middle = _run(fake_claude, monkeypatch, "c")
    _set_start_time(oldest, "2026-01-01T12:00:00")
    _set_start_time(newest, "2026-01-03T12:00:00")
    _set_start_time(middle, "2026-01-02T12:00:00")

    main(["dashboard"])

    rows = _runs_table(_dashboard_html())
    assert [row["test"] for row in rows] == ["b", "c", "a"]
    assert rows[0]["date"].startswith("2026-01-03")


def test_ac4_2_selecting_a_run_shows_its_full_timeline(fake_claude, monkeypatch):
    _run(
        fake_claude,
        monkeypatch,
        "timeline",
        "subagent.jsonl",
        "turn2.jsonl",
        answers=["go <on>"],
        **{"assert": [{"tool_not_called": "Grep"}, {"tool_called": "Task"}]},
    )
    html = _dashboard_html()

    detail = _run_detail(html, _row_for(_runs_table(html), "timeline")).text

    assert "Searching for TODOs." in detail
    assert "Review passed." in detail
    assert "go <on>" in detail
    assert "Q2: Who uses it?" in detail
    assert detail.index("Review passed.") < detail.index("go <on>") < detail.index("Q2: Who")
    assert '"pattern": "TODO"' in detail
    assert "no matches" in detail
    assert "Grep was called 1 time" in detail
    assert "Task was called 1 time" in detail


def test_ac4_2_tool_calls_are_expandable_and_subagent_calls_nest_inside_their_parent(
    fake_claude, monkeypatch
):
    _run(fake_claude, monkeypatch, "nesting", "subagent.jsonl")

    html = _dashboard_html()
    section_start = html.index(f'id="{_runs_table(html)[0]["href"][1:]}"')
    section = html[section_start:]
    task_at = section.index("Task")
    grep_at = section.index("Grep")
    review_passed_at = section.index("Review passed.")

    # Both calls are <details>, and Grep's opens inside Task's: Task's is not closed before Grep.
    assert section.rfind("<details", 0, task_at) != -1
    assert section.count("<details", task_at, grep_at) == 1
    assert task_at < grep_at < review_passed_at
    assert "</details>" not in section[task_at:grep_at]


def test_ac4_2_each_run_selects_its_own_detail(fake_claude, monkeypatch):
    _run(fake_claude, monkeypatch, "one", "basic.jsonl")
    _run(fake_claude, monkeypatch, "two", "subagent.jsonl")
    html = _dashboard_html()
    rows = _runs_table(html)

    one = _run_detail(html, _row_for(rows, "one")).text
    two = _run_detail(html, _row_for(rows, "two")).text

    assert "Q1: What is the <app> for?" in one and "Review passed." not in one
    assert "Review passed." in two and "Q1: What is the <app> for?" not in two


def test_ac4_3_dashboard_embeds_all_data_and_loads_nothing_from_the_network(
    fake_claude, monkeypatch
):
    _run(fake_claude, monkeypatch, "offline")

    html = _dashboard_html()

    assert_loads_nothing_from_the_network(html)
    assert "fetch(" not in html
    assert "Q1: What is the &lt;app&gt; for?" in html
    assert "File does not exist." in html


def test_ac4_4_missing_or_corrupt_run_json_is_a_broken_item_and_the_rest_still_render(
    fake_claude, monkeypatch
):
    _run(fake_claude, monkeypatch, "healthy")
    runs_root = Path.cwd() / "runs"
    (runs_root / "no-record").mkdir()
    (runs_root / "bad-json").mkdir()
    (runs_root / "bad-json" / "run.json").write_text("{not json", encoding="utf-8")
    (runs_root / "bad-shape").mkdir()
    (runs_root / "bad-shape" / "run.json").write_text('{"test_name": "x"}', encoding="utf-8")

    assert main(["dashboard"]) == 0

    html = _dashboard_html()
    rows = _runs_table(html)
    assert _row_for(rows, "healthy")["status"] == "pass"
    for broken_name in ("no-record", "bad-json", "bad-shape"):
        row = _row_for(rows, broken_name)
        assert row["status"] == "broken"
        assert "run.json" in _run_detail(html, row).text


def test_ac4_4_broken_item_shows_why_it_is_broken(fake_claude, monkeypatch):
    runs_root = Path.cwd() / "runs"
    (runs_root / "missing").mkdir(parents=True)
    (runs_root / "corrupt").mkdir()
    (runs_root / "corrupt" / "run.json").write_text("{not json", encoding="utf-8")

    main(["dashboard"])

    html = _dashboard_html()
    rows = _runs_table(html)
    missing = _run_detail(html, _row_for(rows, "missing")).text
    corrupt = _run_detail(html, _row_for(rows, "corrupt")).text
    assert "not found" in missing
    assert "line 1" in corrupt  # the JSON parser's own location of the error


def test_ac4_5_dashboard_command_regenerates_without_running_tests(fake_claude, monkeypatch):
    _run(fake_claude, monkeypatch, "earlier")
    index_path = Path.cwd() / "runs" / "index.html"
    index_path.unlink()
    launches_before = len(fake_claude.invocations)

    exit_code = main(["dashboard"])

    assert exit_code == 0
    assert [row["test"] for row in _runs_table(_dashboard_html())] == ["earlier"]
    assert len(fake_claude.invocations) == launches_before


def test_ac4_5_dashboard_command_with_no_runs_writes_an_empty_dashboard(fake_claude):
    assert main(["dashboard"]) == 0

    assert _runs_table(_dashboard_html()) == []
    assert not fake_claude.was_launched


def _write_big_output_stream(directory: Path, output: str) -> Path:
    events = [
        {"type": "system", "subtype": "init", "session_id": "sess-big", "model": "m"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
            "parent_tool_use_id": None,
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": output}]
            },
            "parent_tool_use_id": None,
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "after the big output"}]},
            "parent_tool_use_id": None,
        },
        {"type": "result", "subtype": "success", "num_turns": 1, "session_id": "sess-big"},
    ]
    stream_path = directory / "big.jsonl"
    stream_path.write_text("".join(json.dumps(event) + "\n" for event in events), "utf-8")
    return stream_path


def test_ac4_6_large_tool_output_is_truncated_with_the_full_text_on_expand(
    fake_claude, monkeypatch, tmp_path
):
    big_output = "BEGIN-" + "x" * 1_100_000 + "-END"
    stream_path = _write_big_output_stream(tmp_path, big_output)
    monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(stream_path))
    run_skillbench(str(write_test_yaml(Path.cwd(), {"name": "big", "prompt": "/x"})))
    assert load_run_record()["trace"]["items"][0]["output"] == big_output

    html = _dashboard_html()
    detail = _run_detail(html, _row_for(_runs_table(html), "big"))

    visible = "".join(detail.visible)
    assert "BEGIN-xxx" in visible
    assert "-END" not in visible
    assert len(visible) < 100_000
    assert big_output in "".join(detail.collapsed)
    assert "after the big output" in visible
