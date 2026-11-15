"""The `skillbench` command line: `init`, `skill` and `agent`."""

import argparse
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from skillbench.claude import ClaudeNotFoundError, find_claude
from skillbench.config import TEST_FOLDERS, ConfigError, Project, TestSpec, find_project, load_test
from skillbench.dashboard import INDEX_FILE_NAME, format_duration, format_optional, write_dashboard
from skillbench.progress import Eta, Progress, RunHeading, load_history, make_progress
from skillbench.runner import AskUser, RunOptions, run_test
from skillbench.scaffold import scaffold

_EXIT_OK = 0
_EXIT_RUN_FAILED = 1
_EXIT_USAGE_ERROR = 2
_SUMMARY_HEADER = ("test", "status", "duration", "turns")
_ANSWER_PROMPT = "your answer (empty or /done to finish): "
_NO_PROJECT = "no skillbench/ folder here or above; run `skillbench init` to create one"
_TRIGGER_PROMPT_SHOWN_CHARS = 40


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
        specs = _load_specs(project, args.command, args.target)
        executable = find_claude()
    except (ConfigError, ClaudeNotFoundError, _UsageError) as error:
        print(f"skillbench: {error}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    progress = make_progress()
    options = RunOptions(
        executable=executable,
        eval_id=f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}",
        model_override=args.model,
        ask_user=_terminal_asker(progress) if sys.stdin.isatty() else None,
        progress=progress,
    )
    variants = [variant for spec in specs for variant in _variants(spec, args.repeat)]
    print(f"skillbench: {_describe_plan(variants)}")
    return _run_all(_repeats_of(variants), options, project.runs_dir, progress)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillbench", description="Test agent skills and full agent loops."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the skillbench/ folder with sample tests")
    for kind, folder in TEST_FOLDERS.items():
        test_parser = commands.add_parser(kind, help=f"run tests in skillbench/{folder}/")
        test_parser.add_argument(
            "target", nargs="?", help="a test or skill name, or a test file or folder"
        )
        test_parser.add_argument("--model", help="model to use, passed to claude as given")
        test_parser.add_argument(
            "--repeat", type=_positive_int, metavar="N", help="run each test N times"
        )
    return parser


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


# --- choosing tests ------------------------------------------------------------------------


def _load_specs(project: Project, kind: str, target: str | None) -> list[TestSpec]:
    """Every test of this kind, or those `target` picks: a path, or a test's name, file name or
    skill."""
    if target is not None and Path(target).exists():
        return [load_test(path, project, kind) for path in _find_test_files(Path(target))]
    tests_dir = project.tests_dir(kind)
    specs = [load_test(path, project, kind) for path in _find_test_files(tests_dir)]
    if target is None:
        return specs
    matching = [spec for spec in specs if _is_picked_by(spec, target)]
    if not matching:
        names = ", ".join(spec.name for spec in specs)
        raise _UsageError(f"no test or skill named '{target}' in {tests_dir} (tests: {names})")
    return matching


def _is_picked_by(spec: TestSpec, target: str) -> bool:
    skill = spec.skill or ""
    return target in (spec.name, spec.source_path.stem, skill, skill.rsplit(":", 1)[-1])


def _find_test_files(test_path: Path) -> list[Path]:
    if not test_path.is_dir():
        return [test_path]
    test_files = sorted(test_path.rglob("*.yaml"), key=lambda p: p.relative_to(test_path).parts)
    if not test_files:
        raise ConfigError(f"{test_path}: no *.yaml test files found")
    return test_files


# --- planning runs: one per trigger prompt and repeat --------------------------------------


@dataclass(frozen=True)
class _PlannedRun:
    spec: TestSpec
    repeat_index: int


def _repeats_of(variants: list[TestSpec]) -> list[_PlannedRun]:
    return [
        _PlannedRun(variant, index)
        for variant in variants
        for index in range(1, variant.repeat + 1)
    ]


def _variants(spec: TestSpec, repeat_override: int | None) -> list[TestSpec]:
    """The test itself (unless it only has trigger prompts), then one test per trigger prompt."""
    if repeat_override is not None:
        spec = replace(spec, repeat=repeat_override)
    variants = [spec] if spec.has_forced_run else []
    for case in spec.triggers:
        label = "should" if case.should_invoke else "should not"
        name = f"{spec.name} [{label}] {_shorten(case.prompt, _TRIGGER_PROMPT_SHOWN_CHARS)}"
        variants.append(replace(spec, name=name, trigger=case, triggers=[]))
    return variants


def _describe_plan(variants: list[TestSpec]) -> str:
    """e.g. `6 tests × 5 repeats = 30 runs`."""
    repeats = {variant.repeat for variant in variants}
    total = sum(variant.repeat for variant in variants)
    tests = _plural(len(variants), "test")
    if repeats == {1}:
        return f"{tests} = {_plural(total, 'run')}"
    if len(repeats) == 1:
        return f"{tests} × {_plural(repeats.pop(), 'repeat')} = {_plural(total, 'run')}"
    return f"{tests} (some repeated) = {_plural(total, 'run')}"


# --- running and reporting -----------------------------------------------------------------


@dataclass
class _TestResult:
    """One test, judged from all its repeats."""

    test_name: str
    min_pass_rate: float
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(1 for record in self.records if record["status"] == "pass")

    @property
    def is_repeated(self) -> bool:
        return len(self.records) > 1

    @property
    def status(self) -> str:
        if not self.is_repeated:
            return self.records[0]["status"]
        if self.passes / len(self.records) >= self.min_pass_rate:
            return "pass"
        if all(record["status"] == "error" for record in self.records):
            return "error"
        return "fail"

    def assertion_hits(self) -> list[tuple[str, int]]:
        """Each assertion, labelled `type: expected`, and in how many runs it passed."""
        hits: Counter[str] = Counter()
        for record in self.records:
            for result in record["assertions"]:
                label = f"{result['type']}: {_format_expected(result['expected'])}"
                hits[label] += 1 if result["passed"] else 0
        return list(hits.items())


def _run_all(
    runs: list[_PlannedRun], options: RunOptions, runs_root: Path, progress: Progress
) -> int:
    eta = Eta(load_history(runs_root), [run.spec.name for run in runs])
    results: dict[str, _TestResult] = {}
    for index, run in enumerate(runs):
        eta.start_run(index)
        progress.run_started(_heading(run, options, index, len(runs)), eta)
        record = run_test(run.spec, runs_root, options, repeat_index=run.repeat_index)
        eta.finish_run(record["duration_s"])
        write_dashboard(runs_root)
        progress.run_finished(_run_line(record))
        result = results.setdefault(
            run.spec.name, _TestResult(run.spec.name, run.spec.min_pass_rate)
        )
        result.records.append(record)
    print(_summary(list(results.values())))
    print(f"dashboard: {_shown_path(runs_root / INDEX_FILE_NAME)}")
    is_all_passed = all(result.status == "pass" for result in results.values())
    return _EXIT_OK if is_all_passed else _EXIT_RUN_FAILED


def _heading(run: _PlannedRun, options: RunOptions, index: int, total: int) -> RunHeading:
    spec = run.spec
    planned_turns = None
    if not spec.is_interactive:
        planned_turns = 1 if spec.trigger is not None else 1 + len(spec.answers)
    return RunHeading(
        index=index + 1,
        total=total,
        test_name=spec.name,
        model=options.model_for(spec),
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


def _run_line(record: dict[str, Any]) -> str:
    repeat = record["repeat_count"]
    repeat_text = f" (repeat {record['repeat_index']}/{repeat})" if repeat > 1 else ""
    line = f"{record['test_name']}{repeat_text}: {record['status']} in {record['duration_s']}s"
    return f"{line}\n  {record['error']}" if record["error"] else line


def _summary(results: list[_TestResult]) -> str:
    """The results table, then for repeated tests the pass rate and each check's hit count."""
    table = [_SUMMARY_HEADER, *(_summary_row(result) for result in results)]
    widths = [max(len(row[column]) for row in table) for column in range(len(_SUMMARY_HEADER))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in table
    ]
    for result in results:
        if result.is_repeated:
            lines += _repeat_lines(result)
    return "\n".join(lines)


def _summary_row(result: _TestResult) -> tuple[str, ...]:
    status = result.status
    if result.is_repeated:
        status = f"{status} {result.passes}/{len(result.records)}"
    return (
        result.test_name,
        status,
        format_duration(sum(record["duration_s"] for record in result.records)),
        format_optional(result.records[-1]["trace"]["num_turns"]),
    )


def _repeat_lines(result: _TestResult) -> list[str]:
    runs = len(result.records)
    header = (
        f"\n{result.test_name}: {result.passes}/{runs} runs passed, "
        f"needs {result.min_pass_rate:.0%} -> {result.status}"
    )
    return [header, *(f"  {label}  {hits}/{runs}" for label, hits in result.assertion_hits())]


def _format_expected(expected: Any) -> str:
    if isinstance(expected, list):
        return " -> ".join(map(str, expected))
    if isinstance(expected, dict):
        return ", ".join(f"{key}={value}" for key, value in expected.items())
    return str(expected)


def _shown_path(path: Path) -> str:
    """Relative to where the command was run, when it's below there."""
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(path)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
