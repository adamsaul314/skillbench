"""Choosing which agent CLIs a run uses: detection, the run-on-both prompt, per-test limits."""

import json
from pathlib import Path

from conftest import run_skillbench, type_into_terminal, write_test_yaml

PROMPT = "/overpowered:grilling an idea"
BOTH_PROMPT = "run on both claude and copilot?"


def _records() -> list[dict]:
    records = [
        json.loads(run_json.read_text(encoding="utf-8"))
        for run_json in (Path.cwd() / "runs").glob("*/run.json")
    ]
    return sorted(records, key=lambda record: record["start_time"])


def _backends_run() -> list[str]:
    return [record["backend"] for record in _records()]


def _write_test(**spec) -> Path:
    return write_test_yaml(Path.cwd(), {"name": "pick", "prompt": PROMPT, **spec})


def test_ac5_3_a_backend_that_fails_version_counts_as_not_installed(fake_copilot):
    # fake_copilot leaves a broken `claude` on PATH, so copilot is the only real choice.
    assert run_skillbench(str(_write_test())) == 0

    assert _backends_run() == ["copilot"]


def test_ac5_4_the_only_installed_backend_is_used_and_the_other_reported_skipped(
    fake_claude, capsys
):
    assert run_skillbench(str(_write_test())) == 0

    assert _backends_run() == ["claude"]
    output = capsys.readouterr()
    assert "copilot" in output.err
    assert "skip" in output.err


def test_ac5_4_no_backend_installed_exits_non_zero_with_an_install_hint(
    fake_agents, monkeypatch, capsys
):
    fake_agents.claude.break_install(monkeypatch)
    fake_agents.copilot.break_install(monkeypatch)

    exit_code = run_skillbench(str(_write_test()))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "install" in error_output.lower()
    assert "claude" in error_output
    assert "copilot" in error_output
    assert not (Path.cwd() / "runs").exists()


def test_ac5_4_an_explicit_backend_that_is_not_installed_is_an_error(fake_claude, capsys):
    exit_code = run_skillbench(str(_write_test()), "--backend", "copilot")

    assert exit_code != 0
    assert "copilot" in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_ac5_5_answering_yes_runs_each_test_on_both_backends(fake_agents, monkeypatch, capsys):
    type_into_terminal(monkeypatch, "y")

    assert run_skillbench(str(_write_test())) == 0

    assert BOTH_PROMPT in capsys.readouterr().out
    assert sorted(_backends_run()) == ["claude", "copilot"]


def test_ac5_5_answering_no_runs_on_the_primary_only(fake_agents, monkeypatch):
    type_into_terminal(monkeypatch, "n")

    run_skillbench(str(_write_test()))

    assert _backends_run() == ["claude"]
    assert not fake_agents.copilot.was_launched


def test_ac5_5_asks_once_for_a_whole_directory(fake_agents, monkeypatch, capsys):
    suite_dir = Path.cwd() / "suite"
    suite_dir.mkdir()
    write_test_yaml(suite_dir, {"name": "one", "prompt": PROMPT}, "one.yaml")
    write_test_yaml(suite_dir, {"name": "two", "prompt": PROMPT}, "two.yaml")
    type_into_terminal(monkeypatch, "y")

    run_skillbench(str(suite_dir))

    assert capsys.readouterr().out.count(BOTH_PROMPT) == 1
    assert len(_records()) == 4


def test_ac5_5_without_a_terminal_runs_the_primary_without_asking(fake_agents, capsys):
    run_skillbench(str(_write_test()))

    assert BOTH_PROMPT not in capsys.readouterr().out
    assert _backends_run() == ["claude"]


def test_ac5_5_an_explicit_backend_skips_the_question(fake_agents, monkeypatch, capsys):
    type_into_terminal(monkeypatch, "y")

    run_skillbench(str(_write_test()), "--backend", "copilot")

    assert BOTH_PROMPT not in capsys.readouterr().out
    assert _backends_run() == ["copilot"]


def test_ac5_6_runs_of_one_multi_backend_invocation_share_an_eval_id(fake_agents, monkeypatch):
    type_into_terminal(monkeypatch, "y")
    run_skillbench(str(_write_test()))
    first_eval = {record["eval_id"] for record in _records()}

    type_into_terminal(monkeypatch, "n")
    run_skillbench(str(_write_test()))

    second_eval = {record["eval_id"] for record in _records()} - first_eval
    assert len(first_eval) == 1
    assert len(second_eval) == 1


def test_ac5_7_a_test_limited_to_claude_is_skipped_on_copilot(fake_agents, monkeypatch, capsys):
    type_into_terminal(monkeypatch, "y")

    exit_code = run_skillbench(str(_write_test(backends=["claude"])))

    assert exit_code == 0
    assert _backends_run() == ["claude"]
    assert not fake_agents.copilot.was_launched
    summary_rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert ["pick", "copilot", "skipped", "-", "-"] in summary_rows
