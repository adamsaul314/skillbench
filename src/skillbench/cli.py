"""The `skillbench` command line: `init`, `skill`, `agent`, `check` and `install-skill`."""

import argparse
import importlib.resources
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
from skillbench.progress import (
    Eta,
    Progress,
    TestHeading,
    format_check,
    load_history,
    make_progress,
)
from skillbench.runner import AskUser, RunOptions, run_test
from skillbench.scaffold import scaffold

_EXIT_OK = 0
_EXIT_RUN_FAILED = 1
_EXIT_USAGE_ERROR = 2
_SUMMARY_HEADER = ("test", "status", "duration", "turns")
_ANSWER_PROMPT = "your answer (empty or /done to finish): "
_NO_PROJECT = "no skillbench/ folder here or above; run `skillbench init` to create one"
_TRIGGER_PROMPT_SHOWN_CHARS = 40
_EVAL_CREATOR = "eval-creator"
# Personal skills: in every project, and never loaded into test runs (they ignore user config).
_PERSONAL_SKILLS_DIR = Path.home() / ".claude" / "skills"


class _UsageError(Exception):
    """A problem with how skillbench was invoked; reported with exit code 2."""


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    args = _parser().parse_args(argv)
    if args.command == "init":
        return _init_command()
    if args.command == "install-skill":
        return _install_skill_command()
    try:
        project = find_project(Path.cwd())
        if project is None:
            raise _UsageError(_NO_PROJECT)
        if args.command == "check":
            return _check_command(project)
        specs = _load_specs(project, args.command, args.target)
        executable = find_claude()
    except (ConfigError, ClaudeNotFoundError, _UsageError) as error:
        print(f"skillbench: {error}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    return _run_command(args, project, specs, executable)


def _run_command(
    args: argparse.Namespace, project: Project, specs: list[TestSpec], executable: str
) -> int:
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


def _use_utf8_output() -> None:
    """Piped output is UTF-8 on every OS (Windows would otherwise use its legacy code page, which
    can't show ✓ or most non-English text), and nothing unprintable ever crashes a run."""
    for stream in (sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        if stream.isatty():
            stream.reconfigure(errors="replace")
        else:
            stream.reconfigure(encoding="utf-8", errors="replace")


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
    commands.add_parser("check", help="check every test file is valid, without running anything")
    commands.add_parser(
        "install-skill", help=f"add the /{_EVAL_CREATOR} skill to Claude Code, to write tests"
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


def _install_skill_command() -> int:
    """Copy the eval-creator skill that matches this skillbench version into the user's
    personal skills."""
    source = importlib.resources.files("skillbench") / "eval_creator" / "SKILL.md"
    content = source.read_text(encoding="utf-8")
    target = _PERSONAL_SKILLS_DIR / _EVAL_CREATOR / "SKILL.md"
    if target.is_file() and target.read_text(encoding="utf-8") == content:
        print(f"/{_EVAL_CREATOR} is already installed and up to date: {target}")
        return _EXIT_OK
    action = "updated" if target.exists() else "installed"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"{action} /{_EVAL_CREATOR}: {target}")
    print(f"\nnext: in Claude Code, type /{_EVAL_CREATOR} and what you want to test")
    return _EXIT_OK


def _check_command(project: Project) -> int:
    """Load every test file and report each one's problems, without running anything."""
    problems = 0
    checked = 0
    specs: list[TestSpec] = []
    for kind in TEST_FOLDERS:
        tests_dir = project.tests_dir(kind)
        test_files = sorted(tests_dir.rglob("*.yaml")) if tests_dir.is_dir() else []
        for test_path in test_files:
            checked += 1
            try:
                specs.append(load_test(test_path, project, kind))
            except ConfigError as error:
                problems += 1
                problem = str(error).removeprefix(f"{test_path}: ")
                print(f"error    {_shown_path(test_path)}: {problem}")
            else:
                print(f"ok       {_shown_path(test_path)}")
    if (warning := _missing_plugin_warning(project, specs)) is not None:
        print(f"warning  {warning}")
    if checked == 0:
        print(f"no test files yet in {_shown_path(project.directory)}/skills or /agent")
    elif problems == 0:
        print(f"\nall {_plural(checked, 'test file')} are valid")
    else:
        print(f"\n{problems} of {_plural(checked, 'test file')} have problems")
    return _EXIT_OK if problems == 0 else _EXIT_USAGE_ERROR


def _missing_plugin_warning(project: Project, specs: list[TestSpec]) -> str | None:
    """The repo is a plugin, but tests run on other repos without `plugin: .`, so they won't
    have it."""
    is_plugin_repo = (project.repo_root / ".claude-plugin" / "plugin.json").is_file()
    if not is_plugin_repo or project.plugins:
        return None
    elsewhere = [spec.name for spec in specs if spec.fixture != project.repo_root.resolve()]
    if not elsewhere:
        return None
    return (
        f"this repo is a plugin, but skillbench.yaml has no 'plugin: .', so tests that run on "
        f"another repo won't have it loaded ({', '.join(elsewhere)}). Add 'plugin: .' to "
        f"{_shown_path(project.directory / 'skillbench.yaml')}"
    )


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
                hits[format_check(result)] += 1 if result["passed"] else 0
        return list(hits.items())


def _run_all(
    runs: list[_PlannedRun], options: RunOptions, runs_root: Path, progress: Progress
) -> int:
    eta = Eta(load_history(runs_root), [run.spec.name for run in runs])
    results: dict[str, _TestResult] = {}
    for index, run in enumerate(runs):
        eta.start_run(index)
        progress.test_started(_heading(run, options, index, len(runs)), eta)
        record = run_test(run.spec, runs_root, options, repeat_index=run.repeat_index)
        eta.finish_run(record["duration_s"])
        write_dashboard(runs_root)
        progress.test_finished(record)
        result = results.setdefault(
            run.spec.name, _TestResult(run.spec.name, run.spec.min_pass_rate)
        )
        result.records.append(record)
    print(_summary(list(results.values())))
    print(f"dashboard: {_shown_path(runs_root / INDEX_FILE_NAME)}")
    is_all_passed = all(result.status == "pass" for result in results.values())
    return _EXIT_OK if is_all_passed else _EXIT_RUN_FAILED


def _heading(run: _PlannedRun, options: RunOptions, index: int, total: int) -> TestHeading:
    spec = run.spec
    return TestHeading(
        index=index + 1,
        total=total,
        test_name=spec.name,
        model=options.model_for(spec),
        steps=[_first_message(spec), *([] if spec.trigger is not None else spec.answers)],
        repeat_index=run.repeat_index,
        repeat_count=spec.repeat,
    )


def _first_message(spec: TestSpec) -> str:
    """The prompt as planned; a skill test's exact /command is only known once the skill is
    found in the workspace, and replaces this when the step starts."""
    if spec.trigger is not None:
        return spec.trigger.prompt
    if spec.skill is not None:
        return f"/{spec.skill} {spec.skill_input}".strip()
    return spec.prompt


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
