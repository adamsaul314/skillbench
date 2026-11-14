"""The `skillbench` command line."""

import argparse
import sys
from pathlib import Path

from skillbench.config import ConfigError, load_test
from skillbench.runner import BackendNotFoundError, run_test

_RUNS_DIR_NAME = "runs"
_EXIT_OK = 0
_EXIT_RUN_FAILED = 1
_EXIT_USAGE_ERROR = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillbench", description="Run agent skill tests.")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run a test YAML file")
    run_parser.add_argument("test_path", type=Path, help="path to a test .yaml file")
    run_parser.add_argument("--model", help="model to use, overriding the test's model:")
    args = parser.parse_args(argv)
    return _run_command(args.test_path, args.model)


def _run_command(test_path: Path, model_override: str | None) -> int:
    try:
        spec = load_test(test_path)
        record = run_test(spec, Path.cwd() / _RUNS_DIR_NAME, model_override)
    except (ConfigError, BackendNotFoundError) as error:
        print(f"skillbench: {error}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    report_path = Path.cwd() / _RUNS_DIR_NAME / record["run_id"] / "report.html"
    print(f"{record['test_name']}: {record['status']} in {record['duration_s']}s -> {report_path}")
    return _EXIT_OK if record["status"] == "completed" else _EXIT_RUN_FAILED
