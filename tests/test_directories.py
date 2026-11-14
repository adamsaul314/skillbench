"""Running a directory of tests: order, summary table and overall exit code."""

import json
from pathlib import Path

from conftest import run_skillbench, write_test_yaml

PROMPT = "/overpowered:grilling an idea"


def _run_records() -> list[dict]:
    records = [
        json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        for run_dir in (Path.cwd() / "runs").iterdir()
    ]
    return sorted(records, key=lambda record: record["start_time"])


def _make_suite(root: Path, tests: dict[str, dict]) -> Path:
    suite_dir = root / "suite"
    for relative_path, spec in tests.items():
        test_path = suite_dir / relative_path
        test_path.parent.mkdir(parents=True, exist_ok=True)
        write_test_yaml(test_path.parent, spec, test_path.name)
    return suite_dir


def test_ac2_4_runs_every_yaml_under_the_directory_in_sorted_order(fake_claude):
    suite_dir = _make_suite(
        Path.cwd(),
        {
            "b.yaml": {"name": "bravo", "prompt": PROMPT},
            "a.yaml": {"name": "alpha", "prompt": PROMPT},
            "nested/c.yaml": {"name": "charlie", "prompt": PROMPT},
        },
    )
    (suite_dir / "README.md").write_text("not a test", encoding="utf-8")

    exit_code = run_skillbench(str(suite_dir))

    assert exit_code == 0
    assert [record["test_name"] for record in _run_records()] == ["alpha", "bravo", "charlie"]


def test_ac2_4_prints_a_summary_table_with_status_duration_and_turns(fake_claude, capsys):
    suite_dir = _make_suite(
        Path.cwd(),
        {
            "a.yaml": {"name": "alpha", "prompt": PROMPT},
            "b.yaml": {"name": "bravo", "prompt": PROMPT, "assert": [{"tool_called": "Edit"}]},
        },
    )

    run_skillbench(str(suite_dir))

    lines = capsys.readouterr().out.splitlines()
    header_index = next(i for i, line in enumerate(lines) if line.split()[:1] == ["test"])
    assert lines[header_index].split() == ["test", "status", "duration", "turns"]
    alpha_row, bravo_row = (line.split() for line in lines[header_index + 1 : header_index + 3])
    durations = {record["test_name"]: record["duration_s"] for record in _run_records()}
    # basic.jsonl reports 3 turns
    assert alpha_row == ["alpha", "pass", f"{durations['alpha']:.1f}s", "3"]
    assert bravo_row == ["bravo", "fail", f"{durations['bravo']:.1f}s", "3"]


def test_ac2_5_exit_code_is_0_when_every_test_passes(fake_claude):
    suite_dir = _make_suite(
        Path.cwd(),
        {
            "a.yaml": {"name": "alpha", "prompt": PROMPT, "assert": [{"tool_called": "Bash"}]},
            "b.yaml": {"name": "bravo", "prompt": PROMPT},
        },
    )

    assert run_skillbench(str(suite_dir)) == 0


def test_ac2_5_exit_code_is_1_when_any_test_does_not_pass(fake_claude):
    suite_dir = _make_suite(
        Path.cwd(),
        {
            "a.yaml": {"name": "alpha", "prompt": PROMPT, "assert": [{"tool_called": "Edit"}]},
            "b.yaml": {"name": "bravo", "prompt": PROMPT},
        },
    )

    assert run_skillbench(str(suite_dir)) == 1
    assert [record["status"] for record in _run_records()] == ["fail", "pass"]


def test_ac2_6_an_invalid_test_anywhere_in_the_directory_stops_all_runs(fake_claude, capsys):
    suite_dir = _make_suite(
        Path.cwd(),
        {
            "a.yaml": {"name": "alpha", "prompt": PROMPT},
            "b.yaml": {"name": "bravo", "prompt": PROMPT, "assert": [{"tool_caled": "Edit"}]},
        },
    )

    exit_code = run_skillbench(str(suite_dir))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "b.yaml" in error_output
    assert "tool_caled" in error_output
    assert not fake_claude.was_launched


def test_a_directory_with_no_tests_is_an_error(fake_claude, capsys):
    empty_dir = Path.cwd() / "empty"
    empty_dir.mkdir()

    exit_code = run_skillbench(str(empty_dir))

    assert exit_code != 0
    assert "no *.yaml" in capsys.readouterr().err
    assert not fake_claude.was_launched
