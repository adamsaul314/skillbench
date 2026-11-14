"""Shared fixtures: a fake `claude` on PATH and helpers to write tests and read runs."""

import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from skillbench.cli import main

TESTS_DIR = Path(__file__).parent
STREAMS_DIR = TESTS_DIR / "streams"
FAKE_CLAUDE_SCRIPT = TESTS_DIR / "fakes" / "fake_claude.py"


@dataclass
class FakeClaude:
    record_path: Path

    def use_stream(self, monkeypatch, stream_name: str) -> Path:
        [stream_path] = self.use_streams(monkeypatch, stream_name)
        return stream_path

    def use_streams(self, monkeypatch, *stream_names: str) -> list[Path]:
        """Invocation N replays stream N; the last stream repeats for later invocations."""
        stream_paths = [STREAMS_DIR / name for name in stream_names]
        monkeypatch.setenv("FAKE_CLAUDE_STREAM", os.pathsep.join(map(str, stream_paths)))
        return stream_paths

    @property
    def was_launched(self) -> bool:
        return self.record_path.exists()

    @property
    def invocations(self) -> list[dict]:
        if not self.was_launched:
            return []
        lines = self.record_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    @property
    def argv(self) -> list[str]:
        """The first invocation's arguments."""
        return self.invocations[0]["argv"]

    @property
    def cwd(self) -> Path:
        """The first invocation's working directory."""
        return Path(self.invocations[0]["cwd"])


def _install_fake_executable(bin_dir: Path) -> None:
    bin_dir.mkdir()
    if os.name == "nt":
        launcher = bin_dir / "claude.cmd"
        launcher.write_text(f'@"{sys.executable}" "{FAKE_CLAUDE_SCRIPT}" %*\r\n', encoding="utf-8")
    else:
        launcher = bin_dir / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE_SCRIPT}" "$@"\n')
        launcher.chmod(0o755)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch) -> FakeClaude:
    bin_dir = tmp_path / "fake-bin"
    _install_fake_executable(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    record_path = tmp_path / "fake-claude-invocations.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record_path))
    monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(STREAMS_DIR / "basic.jsonl"))
    monkeypatch.delenv("FAKE_CLAUDE_SLEEP_S", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_EXIT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    return FakeClaude(record_path=record_path)


def write_test_yaml(directory: Path, spec: dict, file_name: str = "test.yaml") -> Path:
    test_path = directory / file_name
    test_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return test_path


def run_skillbench(*args: str) -> int:
    return main(["run", *args])


def only_run_dir() -> Path:
    run_dirs = [entry for entry in (Path.cwd() / "runs").iterdir() if entry.is_dir()]
    assert len(run_dirs) == 1, f"expected exactly one run dir, found {run_dirs}"
    return run_dirs[0]


def load_run_record() -> dict:
    return json.loads((only_run_dir() / "run.json").read_text(encoding="utf-8"))


class FakeTerminal(io.StringIO):
    """Stands in for stdin: typed lines are read in order; `is_tty` says whether it's a terminal."""

    def __init__(self, typed: str, is_tty: bool = True) -> None:
        super().__init__(typed)
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def type_into_terminal(monkeypatch, *lines: str) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTerminal("".join(f"{line}\n" for line in lines)))
