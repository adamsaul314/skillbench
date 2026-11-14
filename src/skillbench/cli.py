"""The `skillbench` command line."""

import argparse
import sys
from pathlib import Path
from typing import Any

from skillbench.answers import AskUser
from skillbench.answers_file import AnswersNotSavedError, save_answers
from skillbench.config import ConfigError, load_test
from skillbench.runner import BackendNotFoundError, RunOptions, run_test

_RUNS_DIR_NAME = "runs"
_TEST_FILE_PATTERN = "*.yaml"
_EXIT_OK = 0
_EXIT_RUN_FAILED = 1
_EXIT_USAGE_ERROR = 2
_SUMMARY_HEADER = ("test", "status", "duration", "turns")
_UNKNOWN_TURNS = "-"
_COLUMN_GAP = "  "
_ANSWER_PROMPT = "your answer (empty or /done to finish): "
_YES_ANSWERS = ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillbench", description="Run agent skill tests.")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run a test YAML file or a directory of them")
    run_parser.add_argument(
        "test_path", type=Path, help=f"a test .yaml file, or a directory of {_TEST_FILE_PATTERN}"
    )
    run_parser.add_argument("--model", help="model to use, overriding the test's model:")
    args = parser.parse_args(argv)
    return _run_command(args.test_path, args.model)


def _run_command(test_path: Path, model_override: str | None) -> int:
    runs_root = Path.cwd() / _RUNS_DIR_NAME
    ask_user: AskUser | None = _ask_in_terminal if sys.stdin.isatty() else None
    options = RunOptions(model_override=model_override, ask_user=ask_user)
    try:
        # Validate every test before running any, so one bad file doesn't waste a partial suite.
        specs = [load_test(path) for path in _find_test_files(test_path)]
        records = []
        for spec in specs:
            record = run_test(spec, runs_root, options)
            print(_format_run_line(record, runs_root))
            if ask_user is not None:
                _offer_to_save_answers(record)
            records.append(record)
    except (ConfigError, BackendNotFoundError) as error:
        print(f"skillbench: {error}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    print(_format_summary(records))
    all_passed = all(record["status"] == "pass" for record in records)
    return _EXIT_OK if all_passed else _EXIT_RUN_FAILED


def _find_test_files(test_path: Path) -> list[Path]:
    if not test_path.is_dir():
        return [test_path]
    test_files = sorted(
        test_path.rglob(_TEST_FILE_PATTERN),
        key=lambda path: path.relative_to(test_path).as_posix(),
    )
    if not test_files:
        raise ConfigError(f"{test_path}: no {_TEST_FILE_PATTERN} test files found")
    return test_files


def _ask_in_terminal(agent_final_text: str) -> str | None:
    """Show what the agent ended its turn with and read the user's reply; None at end of input."""
    print(f"\n--- agent ---\n{agent_final_text}\n-------------")
    try:
        return input(_ANSWER_PROMPT)
    except EOFError:
        return None


def _offer_to_save_answers(record: dict[str, Any]) -> None:
    """After a conversation with live answers, offer to make them the test's scripted answers."""
    messages = [item for item in record["trace"]["items"] if item["kind"] == "user_message"]
    if not any(message["source"] == "live" for message in messages):
        return
    test_path = Path(record["test_file"])
    try:
        reply = input(f"save these answers to {test_path}? [y/N] ")
    except EOFError:
        return
    if reply.strip().lower() not in _YES_ANSWERS:
        return
    try:
        save_answers(test_path, [message["text"] for message in messages])
    except AnswersNotSavedError as error:
        print(f"skillbench: {error}", file=sys.stderr)


def _format_run_line(record: dict[str, Any], runs_root: Path) -> str:
    report_path = runs_root / record["run_id"] / "report.html"
    line = f"{record['test_name']}: {record['status']} in {record['duration_s']}s -> {report_path}"
    return f"{line}\n  {record['error']}" if record["error"] else line


def _format_summary(records: list[dict[str, Any]]) -> str:
    rows = [_SUMMARY_HEADER] + [_summary_row(record) for record in records]
    widths = [max(len(row[column]) for row in rows) for column in range(len(_SUMMARY_HEADER))]
    return "\n".join(
        _COLUMN_GAP.join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in rows
    )


def _summary_row(record: dict[str, Any]) -> tuple[str, str, str, str]:
    turns = record["trace"]["num_turns"]
    return (
        record["test_name"],
        record["status"],
        f"{record['duration_s']:.1f}s",
        _UNKNOWN_TURNS if turns is None else str(turns),
    )
