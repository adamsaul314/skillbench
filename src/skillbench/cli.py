"""The `skillbench` command line."""

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from skillbench.answers import AskUser
from skillbench.answers_file import AnswersNotSavedError, save_answers
from skillbench.backends import BackendNotFoundError, find_installed, not_installed_error
from skillbench.config import BACKEND_NAMES, ConfigError, TestSpec, load_test
from skillbench.dashboard import INDEX_FILE_NAME, write_dashboard
from skillbench.progress import Eta, Progress, RunHeading, load_history, make_progress
from skillbench.project import AGENT_TESTS_DIR_NAME, SKILL_TESTS_DIR_NAME, Project, find_project
from skillbench.report import format_duration, format_optional
from skillbench.runner import RunOptions, run_test
from skillbench.scaffold import scaffold
from skillbench.suite import GroupResult, Plan, PlannedRun, collect_results, plan_runs

_EXIT_OK = 0
_EXIT_RUN_FAILED = 1
_EXIT_USAGE_ERROR = 2
_SUMMARY_HEADER = ("test", "backend", "status", "duration", "turns")
_ANSWER_PROMPT = "your answer (empty or /done to finish): "
_NO_PROJECT = "no skillbench/ folder here or above; run `skillbench init` to create one"
# Which tests folder each test command runs.
_TEST_COMMANDS = {"skill": SKILL_TESTS_DIR_NAME, "agent": AGENT_TESTS_DIR_NAME}


class _UsageError(Exception):
    """A problem with how skillbench was invoked; reported with exit code 2."""


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        return _init_command()
    try:
        project = find_project(Path.cwd())
        if project is None:
            raise _UsageError(_NO_PROJECT)
        if args.command == "dashboard":
            write_dashboard(project.runs_dir)
            print(f"dashboard: {project.runs_dir / INDEX_FILE_NAME}")
            return _EXIT_OK
        specs = _load_specs(args, project)
        backends = [name for name in BACKEND_NAMES if getattr(args, name)]
        options = _run_options(args, project, backends or project.backends)
    except (ConfigError, BackendNotFoundError, _UsageError) as error:
        print(f"skillbench: {error}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    return _run(plan_runs(specs, options, args.repeat), project.runs_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillbench", description="Test agent skills and full agent loops."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the skillbench/ folder with sample tests")
    for name, folder in _TEST_COMMANDS.items():
        test_parser = commands.add_parser(name, help=f"run tests in skillbench/{folder}/")
        test_parser.add_argument(
            "target", nargs="?", help="a test or skill name, or a test file or folder"
        )
        _add_run_flags(test_parser)
    run_parser = commands.add_parser("run", help="run a test file or a folder of them")
    run_parser.add_argument("target", type=Path)
    _add_run_flags(run_parser)
    commands.add_parser("dashboard", help="rebuild skillbench/runs/index.html")
    return parser


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    for name in BACKEND_NAMES:
        parser.add_argument(f"--{name}", action="store_true", help=f"run on {name}")
    parser.add_argument("--model", help="model to use, passed to the agent CLI as given")
    parser.add_argument("--repeat", type=_positive_int, metavar="N", help="run each test N times")


def _positive_int(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return int(text)


def _init_command() -> int:
    created, existing = scaffold(Path.cwd())
    for path in created:
        print(f"created  {path.relative_to(Path.cwd()).as_posix()}")
    for path in existing:
        print(f"exists   {path.relative_to(Path.cwd()).as_posix()} (left as is)")
    if created:
        print("\nnext: edit the sample tests, then run `skillbench skill` or `skillbench agent`")
    return _EXIT_OK


def _load_specs(args: argparse.Namespace, project: Project) -> list[TestSpec]:
    target = args.target
    if args.command == "run" or (target is not None and Path(target).exists()):
        return [load_test(path, project) for path in _find_test_files(Path(target))]
    tests_dir = project.directory / _TEST_COMMANDS[args.command]
    specs = [load_test(path, project) for path in _find_test_files(tests_dir)]
    if target is None:
        return specs
    # A test is picked by its name, its file name, or the skill it tests.
    matching = [
        spec
        for spec in specs
        if target in (spec.name, spec.source_path.stem, spec.skill)
        or (spec.skill or "").rsplit(":", 1)[-1] == target
    ]
    if not matching:
        names = ", ".join(spec.name for spec in specs)
        raise _UsageError(f"no test or skill named '{target}' in {tests_dir} (tests: {names})")
    return matching


def _find_test_files(test_path: Path) -> list[Path]:
    if not test_path.is_dir():
        return [test_path]
    test_files = sorted(test_path.rglob("*.yaml"), key=lambda p: p.relative_to(test_path).parts)
    if not test_files:
        raise ConfigError(f"{test_path}: no *.yaml test files found")
    return test_files


def _run_options(
    args: argparse.Namespace, project: Project, wanted: list[str] | None
) -> list[RunOptions]:
    """One RunOptions per backend: the wanted ones, else the installed one (Claude when both
    are installed)."""
    if wanted:
        installed = [find_installed(name) for name in wanted]
        missing = [name for name, found in zip(wanted, installed, strict=True) if found is None]
        if missing:
            raise not_installed_error(missing)
    else:
        installed = [found for name in BACKEND_NAMES if (found := find_installed(name))]
        if not installed:
            raise not_installed_error(list(BACKEND_NAMES))
        if len(installed) > 1:
            print(
                "skillbench: using claude (--copilot for copilot, both flags for both)",
                file=sys.stderr,
            )
            installed = installed[:1]
    if args.model is not None and len(installed) > 1:
        raise _UsageError("--model needs a single backend: add --claude or --copilot")
    progress = make_progress()
    ask_user = _terminal_asker(progress) if sys.stdin.isatty() else None
    eval_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    return [
        RunOptions(
            backend,
            eval_id,
            project.cache_dir,
            model_override=args.model,
            ask_user=ask_user,
            progress=progress,
        )
        for backend in installed
        if backend is not None
    ]


def _run(plan: Plan, runs_root: Path) -> int:
    print(f"skillbench: {plan.describe()}")
    eta = Eta(load_history(runs_root), [run.key for run in plan.runs])
    records = []
    for index, run in enumerate(plan.runs):
        progress = run.options.progress
        eta.start_run(index)
        progress.run_started(_heading(run, index, len(plan.runs)), eta)
        record = run_test(run.spec, runs_root, run.options, repeat_index=run.repeat_index)
        eta.finish_run(record["duration_s"])
        write_dashboard(runs_root)
        progress.run_finished(_run_line(record))
        if run.options.ask_user is not None:
            _offer_to_save_answers(record)
        records.append(record)
    results = collect_results(plan.runs, records)
    print(_summary(results, plan.skipped))
    print(f"dashboard: {runs_root / INDEX_FILE_NAME}")
    return _EXIT_OK if all(result.status == "pass" for result in results) else _EXIT_RUN_FAILED


def _heading(run: PlannedRun, index: int, total: int) -> RunHeading:
    spec = run.spec
    planned_turns = None
    if not spec.is_interactive:
        planned_turns = 1 if spec.trigger is not None else 1 + len(spec.answers)
        if spec.max_turns is not None:
            planned_turns = min(planned_turns, spec.max_turns)
    return RunHeading(
        index=index + 1,
        total=total,
        test_name=spec.name,
        backend=run.options.installed.backend.name,
        model=run.options.model_override or spec.model,
        planned_turns=planned_turns,
        repeat_index=run.repeat_index,
        repeat_count=spec.repeat,
    )


def _terminal_asker(progress: Progress) -> AskUser:
    """Show what the agent ended its turn with and read the user's reply; None at end of input."""

    def ask(agent_final_text: str) -> str | None:
        progress.pause()
        print(f"\n--- agent ---\n{agent_final_text}\n-------------")
        try:
            return input(_ANSWER_PROMPT)
        except EOFError:
            return None
        finally:
            progress.resume()

    return ask


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
    if reply.strip().lower() not in ("y", "yes"):
        return
    try:
        save_answers(test_path, [message["text"] for message in messages])
    except AnswersNotSavedError as error:
        print(f"skillbench: {error}", file=sys.stderr)


def _run_line(record: dict[str, Any]) -> str:
    repeat = record["repeat_count"]
    repeat_text = f" (repeat {record['repeat_index']}/{repeat})" if repeat > 1 else ""
    line = f"{record['test_name']}{repeat_text}: {record['status']} in {record['duration_s']}s"
    return f"{line}\n  {record['error']}" if record["error"] else line


def _summary(results: list[GroupResult], skipped: list[tuple[TestSpec, str]]) -> str:
    """The results table, then for repeated tests the pass rate and each check's hit count."""
    rows = [_summary_row(result) for result in results]
    rows += [(spec.name, backend, "skipped", "-", "-") for spec, backend in skipped]
    table = [_SUMMARY_HEADER, *rows]
    widths = [max(len(row[column]) for row in table) for column in range(len(_SUMMARY_HEADER))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in table
    ]
    for result in results:
        if result.is_repeated:
            runs = len(result.records)
            lines.append(
                f"\n{result.test_name} ({result.backend}): {result.passes}/{runs} runs passed, "
                f"needs {result.min_pass_rate:.0%} -> {result.status}"
            )
            lines += [f"  {label}  {hits}/{runs}" for label, hits in result.assertion_hits()]
    return "\n".join(lines)


def _summary_row(result: GroupResult) -> tuple[str, ...]:
    status = result.status
    if result.is_repeated:
        status = f"{status} {result.passes}/{len(result.records)}"
    return (
        result.test_name,
        result.backend,
        status,
        format_duration(sum(record["duration_s"] for record in result.records)),
        format_optional(result.records[-1]["trace"]["num_turns"]),
    )
