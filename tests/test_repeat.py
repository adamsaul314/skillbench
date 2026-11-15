"""Repeat runs: --repeat / repeat:, the multiplier line, pass rates and min_pass_rate."""

from pathlib import Path

import pytest

from conftest import init_project, project_records, skillbench, write_test_yaml

HITS_HELLO = "hello_skill.jsonl"
MISSES_HELLO = "basic.jsonl"


def _loop_test(project_dir: Path, **fields) -> None:
    spec = {"name": "loop", "prompt": "greet", "assert": [{"skill_invoked": "hello"}], **fields}
    write_test_yaml(project_dir / "agent", spec)


def test_repeat_flag_runs_each_test_n_times_and_says_so_up_front(fake_claude, monkeypatch, capsys):
    fake_claude.use_stream(monkeypatch, HITS_HELLO)
    _loop_test(init_project())

    assert skillbench("agent", "--repeat", "3") == 0

    records = project_records()
    assert [record["repeat_index"] for record in records] == [1, 2, 3]
    assert {record["repeat_count"] for record in records} == {3}
    assert len({record["eval_id"] for record in records}) == 1
    output = capsys.readouterr().out
    assert "skillbench: 1 test × 3 repeats = 3 runs" in output
    assert "pass 3/3" in output


def test_the_multiplier_line_counts_backends(fake_agents, monkeypatch, capsys):
    project_dir = init_project()
    write_test_yaml(project_dir / "agent", {"name": "a", "prompt": "hi"}, "a.yaml")
    write_test_yaml(project_dir / "agent", {"name": "b", "prompt": "hi"}, "b.yaml")

    skillbench("agent", "--repeat", "2", "--claude", "--copilot")

    assert "2 tests × 2 repeats × 2 backends = 8 runs" in capsys.readouterr().out


def test_default_min_pass_rate_needs_every_run_to_pass(fake_claude, monkeypatch, capsys):
    fake_claude.use_streams(monkeypatch, HITS_HELLO, MISSES_HELLO, HITS_HELLO)
    _loop_test(init_project(), repeat=3)

    assert skillbench("agent") == 1

    output = capsys.readouterr().out
    assert "fail 2/3" in output
    assert "loop (claude): 2/3 runs passed, needs 100% -> fail" in output
    assert "skill_invoked: hello  2/3" in output


def test_min_pass_rate_tolerates_flaky_runs(fake_claude, monkeypatch, capsys):
    fake_claude.use_streams(monkeypatch, HITS_HELLO, MISSES_HELLO, HITS_HELLO)
    _loop_test(init_project(), repeat=3, min_pass_rate=0.6)

    assert skillbench("agent") == 0

    assert "pass 2/3" in capsys.readouterr().out


def test_project_repeat_default_and_cli_override(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, HITS_HELLO)
    _loop_test(init_project("repeat: 2\n"))

    skillbench("agent")
    assert len(project_records()) == 2

    skillbench("agent", "--repeat", "1")
    assert len(project_records()) == 3


def test_repeats_show_their_number_in_the_dashboard(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, HITS_HELLO)
    project_dir = init_project()
    _loop_test(project_dir)

    skillbench("agent", "--repeat", "2")

    dashboard = (project_dir / "runs" / "index.html").read_text(encoding="utf-8")
    assert "loop #1/2" in dashboard
    assert "loop #2/2" in dashboard


def test_repeat_must_be_positive(fake_claude, capsys):
    init_project()

    with pytest.raises(SystemExit) as exit_:
        skillbench("agent", "--repeat", "0")

    assert exit_.value.code == 2
    assert "must be 1 or more" in capsys.readouterr().err
    assert not fake_claude.was_launched
