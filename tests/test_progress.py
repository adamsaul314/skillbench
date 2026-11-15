"""Live progress: the ETA's three tiers, history from past runs, and plain output when piped."""

import json

from skillbench.progress import Eta, PlainProgress, RunHeading, format_seconds, load_history

from conftest import init_project, skillbench, write_test_yaml

A = ("a", "claude")
B = ("b", "claude")


def test_eta_uses_each_tests_last_recorded_duration():
    eta = Eta({A: 100.0, B: 50.0}, [A, B])

    eta.start_run(0)

    assert eta.remaining_s(elapsed_s=30) == 70 + 50


def test_eta_falls_back_to_the_mean_of_runs_finished_so_far():
    eta = Eta({}, [A, B, B])
    eta.start_run(0)
    eta.finish_run(20.0)
    eta.start_run(1)

    assert eta.remaining_s(elapsed_s=5) == 15 + 20


def test_eta_is_unknown_rather_than_invented_with_no_history_and_nothing_finished():
    eta = Eta({A: 100.0}, [A, B])

    eta.start_run(0)

    assert eta.remaining_s(elapsed_s=1) is None


def test_eta_never_goes_negative_when_a_run_overruns():
    eta = Eta({A: 10.0}, [A])
    eta.start_run(0)

    assert eta.remaining_s(elapsed_s=99) == 0


def test_fraction_done_counts_finished_runs_and_caps_the_current_one():
    eta = Eta({A: 10.0, B: 10.0}, [A, B])
    eta.start_run(1)

    assert eta.fraction_done(elapsed_s=5) == 0.75
    assert eta.fraction_done(elapsed_s=500) < 1.0


def test_history_keeps_the_latest_duration_per_test_and_backend(tmp_path):
    for run_id, start, duration in (("r1", "2026-01-01T10:00", 30), ("r2", "2026-01-02T10:00", 40)):
        (tmp_path / run_id).mkdir()
        record = {
            "test_name": "a",
            "backend": "claude",
            "start_time": start,
            "duration_s": duration,
        }
        (tmp_path / run_id / "run.json").write_text(json.dumps(record))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "run.json").write_text("{not json")

    assert load_history(tmp_path) == {A: 40}


def test_format_seconds():
    assert format_seconds(42) == "42s"
    assert format_seconds(372) == "6m 12s"
    assert format_seconds(3900) == "1h 05m"


def test_plain_progress_prints_one_line_per_start_and_finish_without_escape_codes(capsys):
    progress = PlainProgress()
    heading = RunHeading(2, 6, "grill", "claude", None, planned_turns=1)

    progress.run_started(heading, Eta({}, [A]))
    progress.activity(1, "Read")
    progress.run_finished("grill: pass in 1.0s")

    output = capsys.readouterr().out
    assert output == "[2/6] grill  claude · default model\ngrill: pass in 1.0s\n"
    assert "\x1b" not in output


def test_a_piped_run_shows_progress_lines_and_no_ansi(fake_claude, capsys):
    project_dir = init_project()
    write_test_yaml(project_dir / "agent", {"name": "one", "prompt": "hi"}, "one.yaml")
    write_test_yaml(project_dir / "agent", {"name": "two", "prompt": "hi"}, "two.yaml")

    skillbench("agent")

    output = capsys.readouterr().out
    assert "[1/2] one  claude" in output
    assert "[2/2] two  claude" in output
    assert "\x1b" not in output


def test_activity_reports_turns_and_the_latest_tool_while_a_turn_runs(fake_claude, monkeypatch):
    """The runner tells progress about each turn and the newest tool call it can see."""
    from skillbench import cli

    seen: list[tuple[int, str | None]] = []

    class Recording(PlainProgress):
        def activity(self, turn, last_tool):
            seen.append((turn, last_tool))

    monkeypatch.setattr(cli, "make_progress", Recording)
    fake_claude.use_stream(monkeypatch, "hello_skill.jsonl")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP_S", "1.2")
    project_dir = init_project()
    write_test_yaml(project_dir / "agent", {"name": "t", "prompt": "hi"})

    skillbench("agent")

    assert seen[0] == (1, None)
    assert (1, "Bash") in seen
