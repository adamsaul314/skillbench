"""The `skillbench` command line."""

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from skillbench.answers import AskUser
from skillbench.answers_file import AnswersNotSavedError, save_answers
from skillbench.backends import (
    BackendNotFoundError,
    InstalledBackend,
    find_installed,
    not_installed_error,
)
from skillbench.config import BACKEND_NAMES, ConfigError, TestSpec, load_test
from skillbench.dashboard import INDEX_FILE_NAME, write_dashboard
from skillbench.report import format_duration, format_optional
from skillbench.runner import RunOptions, run_test

_RUNS_DIR_NAME = "runs"
_TEST_FILE_PATTERN = "*.yaml"
_EXIT_OK = 0
_EXIT_RUN_FAILED = 1
_EXIT_USAGE_ERROR = 2
_SUMMARY_HEADER = ("test", "backend", "status", "duration", "turns")
_SKIPPED_STATUS = "skipped"
_EVAL_ID_SUFFIX_LENGTH = 6
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
    run_parser.add_argument(
        "--backend",
        choices=BACKEND_NAMES,
        help="agent CLI to run on (default: the installed ones; asks if both are)",
    )
    commands.add_parser(
        "dashboard", help=f"regenerate {_RUNS_DIR_NAME}/{INDEX_FILE_NAME} without running tests"
    )
    args = parser.parse_args(argv)
    runs_root = Path.cwd() / _RUNS_DIR_NAME
    if args.command == "dashboard":
        return _dashboard_command(runs_root)
    return _run_command(args, runs_root)


def _dashboard_command(runs_root: Path) -> int:
    write_dashboard(runs_root)
    print(f"dashboard: {runs_root / INDEX_FILE_NAME}")
    return _EXIT_OK


def _run_command(args: argparse.Namespace, runs_root: Path) -> int:
    ask_user: AskUser | None = _ask_in_terminal if sys.stdin.isatty() else None
    try:
        # Validate every test before running any, so one bad file doesn't waste a partial suite.
        specs = [load_test(path) for path in _find_test_files(args.test_path)]
        backends = _select_backends(args.backend, can_ask=ask_user is not None)
    except (ConfigError, BackendNotFoundError) as error:
        print(f"skillbench: {error}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    eval_id = _new_eval_id()
    all_options = [
        RunOptions(installed, eval_id, model_override=args.model, ask_user=ask_user)
        for installed in backends
    ]
    summary_rows, all_passed = _run_all(specs, all_options, runs_root)
    print(_format_summary(summary_rows))
    print(f"dashboard: {runs_root / INDEX_FILE_NAME}")
    return _EXIT_OK if all_passed else _EXIT_RUN_FAILED


def _run_all(
    specs: list[TestSpec], all_options: list[RunOptions], runs_root: Path
) -> tuple[list[tuple[str, ...]], bool]:
    """Run each test on each backend it allows; return the summary rows and whether every run
    passed. A backend a test's `backends:` excludes gets a `skipped` row instead."""
    summary_rows = []
    all_passed = True
    for spec in specs:
        for options in all_options:
            backend_name = options.installed.backend.name
            if not spec.runs_on(backend_name):
                summary_rows.append(_skipped_row(spec, backend_name))
                continue
            record = _run_and_report(spec, runs_root, options)
            summary_rows.append(_summary_row(record))
            all_passed = all_passed and record["status"] == "pass"
    return summary_rows, all_passed


def _select_backends(requested: str | None, can_ask: bool) -> list[InstalledBackend]:
    """The requested backend; otherwise every runnable one, narrowed to the primary unless the
    user agrees to run on all of them."""
    if requested is not None:
        installed = find_installed(requested)
        if installed is None:
            raise not_installed_error([requested])
        return [installed]
    available = _detect_backends()
    if len(available) > 1 and not (can_ask and _confirm(_run_on_all_question(available))):
        return available[:1]
    return available


def _detect_backends() -> list[InstalledBackend]:
    """Every runnable backend, primary first. Reports the others on stderr as skipped."""
    found = {name: find_installed(name) for name in BACKEND_NAMES}
    available = [installed for installed in found.values() if installed is not None]
    if not available:
        raise not_installed_error(list(BACKEND_NAMES))
    for name, installed in found.items():
        if installed is None:
            print(
                f"skillbench: {name} is not installed or not runnable; skipping it", file=sys.stderr
            )
    return available


def _run_on_all_question(available: list[InstalledBackend]) -> str:
    names = " and ".join(installed.backend.name for installed in available)
    return f"run on both {names}? [y/N] "


def _run_and_report(spec: TestSpec, runs_root: Path, options: RunOptions) -> dict[str, Any]:
    """Run one test on one backend, refresh the dashboard and print the outcome."""
    record = run_test(spec, runs_root, options)
    write_dashboard(runs_root)
    print(_format_run_line(record, runs_root))
    if options.ask_user is not None:
        _offer_to_save_answers(record)
    return record


def _new_eval_id() -> str:
    suffix = uuid.uuid4().hex[:_EVAL_ID_SUFFIX_LENGTH]
    return f"{datetime.now():%Y%m%d-%H%M%S}-{suffix}"


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
    if not _confirm(f"save these answers to {test_path}? [y/N] "):
        return
    try:
        save_answers(test_path, [message["text"] for message in messages])
    except AnswersNotSavedError as error:
        print(f"skillbench: {error}", file=sys.stderr)


def _confirm(question: str) -> bool:
    """Ask a yes/no question in the terminal; anything but yes (or end of input) is no."""
    try:
        reply = input(question)
    except EOFError:
        return False
    return reply.strip().lower() in _YES_ANSWERS


def _format_run_line(record: dict[str, Any], runs_root: Path) -> str:
    report_path = runs_root / record["run_id"] / "report.html"
    line = f"{record['test_name']}: {record['status']} in {record['duration_s']}s -> {report_path}"
    return f"{line}\n  {record['error']}" if record["error"] else line


def _format_summary(rows: list[tuple[str, ...]]) -> str:
    table = [_SUMMARY_HEADER, *rows]
    widths = [max(len(row[column]) for row in table) for column in range(len(_SUMMARY_HEADER))]
    return "\n".join(
        _COLUMN_GAP.join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in table
    )


def _summary_row(record: dict[str, Any]) -> tuple[str, ...]:
    return (
        record["test_name"],
        record["backend"],
        record["status"],
        format_duration(record["duration_s"]),
        format_optional(record["trace"]["num_turns"]),
    )


def _skipped_row(spec: TestSpec, backend_name: str) -> tuple[str, ...]:
    """A test whose `backends:` excludes a backend this invocation runs on."""
    return (spec.name, backend_name, _SKIPPED_STATUS, format_optional(None), format_optional(None))
