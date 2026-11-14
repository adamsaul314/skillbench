"""Shared fixtures: a fake `claude` on PATH and helpers to write tests and read runs."""

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
        stream_path = STREAMS_DIR / stream_name
        monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(stream_path))
        return stream_path

    @property
    def was_launched(self) -> bool:
        return self.record_path.exists()

    @property
    def argv(self) -> list[str]:
        return json.loads(self.record_path.read_text(encoding="utf-8"))["argv"]

    @property
    def cwd(self) -> Path:
        return Path(json.loads(self.record_path.read_text(encoding="utf-8"))["cwd"])


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
    record_path = tmp_path / "fake-claude-invocation.json"
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
