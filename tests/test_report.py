"""The per-run report.html: self-contained and showing the whole trace."""

from pathlib import Path

from conftest import (
    assert_loads_nothing_from_the_network,
    only_run_dir,
    run_skillbench,
    write_test_yaml,
)


def _render_report(fake_claude, monkeypatch, stream: str) -> str:
    fake_claude.use_stream(monkeypatch, stream)
    test_path = write_test_yaml(Path.cwd(), {"name": "report", "prompt": "/x"})
    run_skillbench(str(test_path))
    return (only_run_dir() / "report.html").read_text(encoding="utf-8")


def test_ac1_7_report_shows_assistant_texts_escaped(fake_claude, monkeypatch):
    html = _render_report(fake_claude, monkeypatch, "basic.jsonl")

    assert "Let me look at the repo." in html
    assert "Q1: What is the &lt;app&gt; for?" in html
    assert "<app>" not in html


def test_ac1_7_report_shows_each_tool_call_with_input_and_output(fake_claude, monkeypatch):
    html = _render_report(fake_claude, monkeypatch, "basic.jsonl")

    assert "Bash" in html
    assert "&quot;command&quot;: &quot;ls&quot;" in html
    assert "README.md" in html
    assert "missing.txt" in html
    assert "File does not exist." in html


def test_ac1_7_report_shows_subagent_tool_calls(fake_claude, monkeypatch):
    html = _render_report(fake_claude, monkeypatch, "subagent.jsonl")

    assert "Grep" in html
    assert "no matches" in html
    assert "Review: PASS" in html


def test_ac1_7_report_loads_nothing_from_the_network(fake_claude, monkeypatch):
    html = _render_report(fake_claude, monkeypatch, "basic.jsonl")

    assert_loads_nothing_from_the_network(html)


def test_ac3_1_report_shows_each_user_answer_between_turns(fake_claude, monkeypatch):
    fake_claude.use_streams(monkeypatch, "basic.jsonl", "turn2.jsonl")
    test_path = write_test_yaml(
        Path.cwd(), {"name": "report", "prompt": "/x", "answers": ["it is for <todos>"]}
    )
    run_skillbench(str(test_path))

    html = (only_run_dir() / "report.html").read_text(encoding="utf-8")
    answer_at = html.index("it is for &lt;todos&gt;")
    assert html.index("Q1: What is the &lt;app&gt; for?") < answer_at < html.index("Q2: Who uses")
