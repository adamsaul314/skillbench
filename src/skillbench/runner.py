"""Running one test on the Claude backend and recording everything under runs/<run-id>/."""

import json
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from skillbench.assertions import AssertionResult, RunFacts, evaluate_assertions
from skillbench.claude_command import build_claude_args
from skillbench.claude_trace import parse_claude_stream
from skillbench.config import TestSpec
from skillbench.report import render_report
from skillbench.workspace import create_workspace

_CLAUDE_EXECUTABLE = "claude"
_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
_FIRST_TURN = 1
_RUN_ID_SUFFIX_LENGTH = 6
_DURATION_DECIMALS = 3
_IS_WINDOWS = os.name == "nt"


class BackendNotFoundError(Exception):
    """The agent CLI is not on PATH."""


@dataclass(frozen=True)
class _ProcessOutcome:
    exit_code: int | None  # None when the process was killed
    timed_out: bool
    start_time: datetime
    end_time: datetime
    duration_s: float

    @property
    def run_status(self) -> str:
        if self.timed_out:
            return "timeout"
        return "completed" if self.exit_code == 0 else "error"


def run_test(spec: TestSpec, runs_root: Path, model_override: str | None) -> dict[str, Any]:
    """Run `spec` once on Claude, check its assertions, write run.json, raw output and
    report.html; return the record."""
    executable = _find_claude()
    model = model_override or spec.model
    args = build_claude_args(spec, model, has_api_key=bool(os.environ.get(_API_KEY_ENV_VAR)))
    run_dir = runs_root / _new_run_id(spec.name)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    workspace = create_workspace(spec.fixture)
    stdout_path = raw_dir / f"{_FIRST_TURN}.jsonl"

    outcome = _run_process([executable, *args], workspace, stdout_path, spec.timeout_s)

    run_info = parse_claude_stream(stdout_path.read_bytes())
    facts = RunFacts(
        trace=run_info.trace,
        workspace=workspace,
        exit_code=outcome.exit_code,
        timed_out=outcome.timed_out,
        prompt=spec.prompt,
        loaded_skills=run_info.skills,
    )
    assertion_results = evaluate_assertions(spec.assertions, facts)
    record = {
        "run_id": run_dir.name,
        "test_name": spec.name,
        "test_file": str(spec.source_path),
        "backend": "claude",
        "model": model or run_info.reported_model,
        "start_time": outcome.start_time.isoformat(),
        "end_time": outcome.end_time.isoformat(),
        "duration_s": outcome.duration_s,
        "session_id": run_info.session_id,
        "status": _test_status(outcome.run_status, assertion_results),
        "run_status": outcome.run_status,
        "exit_code": outcome.exit_code,
        "workspace": str(workspace),
        "plugins": run_info.plugins,
        "skills": run_info.skills,
        "assertions": [result.to_json() for result in assertion_results],
        "trace": run_info.trace,
    }
    _write_run_files(run_dir, record)
    return record


def _test_status(run_status: str, assertion_results: list[AssertionResult]) -> str:
    """`error` if the CLI did not finish cleanly, else `fail` if any assertion failed."""
    if run_status != "completed":
        return "error"
    return "pass" if all(result.passed for result in assertion_results) else "fail"


def _find_claude() -> str:
    executable = shutil.which(_CLAUDE_EXECUTABLE)
    if executable is None:
        raise BackendNotFoundError(f"'{_CLAUDE_EXECUTABLE}' was not found on PATH")
    return executable


def _new_run_id(test_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", test_name).strip("-") or "test"
    suffix = uuid.uuid4().hex[:_RUN_ID_SUFFIX_LENGTH]
    return f"{datetime.now():%Y%m%d-%H%M%S}-{safe_name}-{suffix}"


def _write_run_files(run_dir: Path, record: dict[str, Any]) -> None:
    (run_dir / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (run_dir / "report.html").write_text(render_report(record), encoding="utf-8")


def _run_process(
    command: list[str], cwd: Path, stdout_path: Path, timeout_s: float
) -> _ProcessOutcome:
    """Run to completion or timeout. Stdout streams straight to disk so partial output survives."""
    stderr_path = stdout_path.with_suffix(".stderr.txt")
    start_time = datetime.now().astimezone()
    started = time.monotonic()
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=not _IS_WINDOWS,
        )
        try:
            exit_code, timed_out = process.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            exit_code, timed_out = None, True
    return _ProcessOutcome(
        exit_code=exit_code,
        timed_out=timed_out,
        start_time=start_time,
        end_time=datetime.now().astimezone(),
        duration_s=round(time.monotonic() - started, _DURATION_DECIMALS),
    )


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """The agent CLI spawns its own children (tools, subagents); kill all of them."""
    if _IS_WINDOWS:
        # taskkill fails if the process already exited; the poll below handles that case.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if process.poll() is None:
            process.kill()
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
