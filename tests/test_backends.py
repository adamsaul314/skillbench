"""Choosing which agent CLIs a run uses: flags, defaults, detection, per-test limits."""

import json
from pathlib import Path

from conftest import run_skillbench, runs_dir, type_into_terminal, write_test_yaml

PROMPT = "/overpowered:grilling an idea"


def _records() -> list[dict]:
    records = [
        json.loads(run_json.read_text(encoding="utf-8"))
        for run_json in runs_dir().glob("*/run.json")
    ]
    return sorted(records, key=lambda record: record["start_time"])


def _backends_run() -> list[str]:
    return [record["backend"] for record in _records()]


def _write_test(**spec) -> Path:
    return write_test_yaml(Path.cwd(), {"name": "pick", "prompt": PROMPT, **spec})


def test_a_backend_that_fails_version_counts_as_not_installed(fake_copilot):
    # fake_copilot leaves a broken `claude` on PATH, so copilot is the only real choice.
    assert run_skillbench(str(_write_test())) == 0

    assert _backends_run() == ["copilot"]


def test_the_only_installed_backend_is_used(fake_claude):
    assert run_skillbench(str(_write_test())) == 0

    assert _backends_run() == ["claude"]


def test_no_backend_installed_exits_non_zero_with_an_install_hint(fake_agents, monkeypatch, capsys):
    fake_agents.claude.break_install(monkeypatch)
    fake_agents.copilot.break_install(monkeypatch)

    exit_code = run_skillbench(str(_write_test()))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "install" in error_output.lower()
    assert "claude" in error_output
    assert "copilot" in error_output
    assert not runs_dir().exists()


def test_a_flagged_backend_that_is_not_installed_is_an_error(fake_claude, capsys):
    exit_code = run_skillbench(str(_write_test()), "--copilot")

    assert exit_code == 2
    assert "copilot" in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_with_both_installed_and_no_flag_claude_runs_and_says_so(fake_agents, capsys):
    assert run_skillbench(str(_write_test())) == 0

    assert _backends_run() == ["claude"]
    assert not fake_agents.copilot.was_launched
    error_output = capsys.readouterr().err
    assert "using claude" in error_output
    assert "--copilot" in error_output


def test_never_asks_which_backend_even_in_a_terminal(fake_agents, monkeypatch, capsys):
    type_into_terminal(monkeypatch, "y")

    run_skillbench(str(_write_test()))

    assert "[y/N]" not in capsys.readouterr().out
    assert _backends_run() == ["claude"]


def test_copilot_flag_runs_on_copilot_only(fake_agents):
    assert run_skillbench(str(_write_test()), "--copilot") == 0

    assert _backends_run() == ["copilot"]
    assert not fake_agents.claude.was_launched


def test_both_flags_run_each_test_on_both_backends(fake_agents):
    suite_dir = Path.cwd() / "suite"
    suite_dir.mkdir()
    write_test_yaml(suite_dir, {"name": "one", "prompt": PROMPT}, "one.yaml")
    write_test_yaml(suite_dir, {"name": "two", "prompt": PROMPT}, "two.yaml")

    assert run_skillbench(str(suite_dir), "--claude", "--copilot") == 0

    assert sorted(_backends_run()) == ["claude", "claude", "copilot", "copilot"]


def test_model_with_both_backends_is_a_usage_error(fake_agents, capsys):
    exit_code = run_skillbench(str(_write_test()), "--claude", "--copilot", "--model", "m")

    assert exit_code == 2
    assert "--model needs a single backend" in capsys.readouterr().err
    assert not fake_agents.claude.was_launched
    assert not fake_agents.copilot.was_launched


def test_model_with_one_backend_is_passed_to_it(fake_agents):
    run_skillbench(str(_write_test()), "--copilot", "--model", "gpt-5")

    argv = fake_agents.copilot.argv
    assert argv[argv.index("--model") + 1] == "gpt-5"


def test_runs_of_one_multi_backend_invocation_share_an_eval_id(fake_agents):
    run_skillbench(str(_write_test()), "--claude", "--copilot")
    first_eval = {record["eval_id"] for record in _records()}

    run_skillbench(str(_write_test()), "--claude")

    second_eval = {record["eval_id"] for record in _records()} - first_eval
    assert len(first_eval) == 1
    assert len(second_eval) == 1


def test_a_test_limited_to_claude_is_skipped_on_copilot(fake_agents, capsys):
    exit_code = run_skillbench(str(_write_test(backends=["claude"])), "--claude", "--copilot")

    assert exit_code == 0
    assert _backends_run() == ["claude"]
    assert not fake_agents.copilot.was_launched
    summary_rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert ["pick", "copilot", "skipped", "-", "-"] in summary_rows
