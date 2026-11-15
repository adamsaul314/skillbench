"""Shared fixtures: fake `claude`/`copilot` CLIs on PATH, and helpers to write tests and read
runs."""

import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from skillbench.cli import main

TESTS_DIR = Path(__file__).parent
STREAMS_DIR = TESTS_DIR / "streams"
FAKE_AGENT_SCRIPT = TESTS_DIR / "fakes" / "fake_agent.py"
_AGENT_NAMES = ("claude", "copilot")
_DEFAULT_STREAMS = {"claude": "basic.jsonl", "copilot": "copilot_basic.jsonl"}


@dataclass
class FakeAgent:
    """One fake agent CLI on PATH; see fakes/fake_agent.py for what it does."""

    name: str
    record_path: Path

    @property
    def env_prefix(self) -> str:
        return f"FAKE_{self.name.upper()}_"

    def use_stream(self, monkeypatch, stream_name: str) -> Path:
        [stream_path] = self.use_streams(monkeypatch, stream_name)
        return stream_path

    def use_streams(self, monkeypatch, *stream_names: str) -> list[Path]:
        """Invocation N replays stream N; the last stream repeats for later invocations."""
        stream_paths = [STREAMS_DIR / name for name in stream_names]
        monkeypatch.setenv(self.env_prefix + "STREAM", os.pathsep.join(map(str, stream_paths)))
        return stream_paths

    def break_install(self, monkeypatch) -> None:
        """Leave the executable on PATH but make it fail, even on --version."""
        monkeypatch.setenv(self.env_prefix + "BROKEN", "1")

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


@dataclass
class FakeAgents:
    claude: FakeAgent
    copilot: FakeAgent


def _install_fake_executable(bin_dir: Path, agent_name: str) -> None:
    if os.name == "nt":
        launcher = bin_dir / f"{agent_name}.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{FAKE_AGENT_SCRIPT}" {agent_name} %*\r\n', encoding="utf-8"
        )
    else:
        launcher = bin_dir / agent_name
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_AGENT_SCRIPT}" {agent_name} "$@"\n'
        )
        launcher.chmod(0o755)


def _install_fake_agent(tmp_path: Path, bin_dir: Path, monkeypatch, name: str) -> FakeAgent:
    _install_fake_executable(bin_dir, name)
    agent = FakeAgent(name=name, record_path=tmp_path / f"fake-{name}-invocations.jsonl")
    monkeypatch.setenv(agent.env_prefix + "RECORD", str(agent.record_path))
    monkeypatch.setenv(agent.env_prefix + "STREAM", str(STREAMS_DIR / _DEFAULT_STREAMS[name]))
    for setting in ("SLEEP_S", "EXIT", "BROKEN"):
        monkeypatch.delenv(agent.env_prefix + setting, raising=False)
    return agent


@pytest.fixture
def fake_agents(tmp_path, monkeypatch) -> FakeAgents:
    """Both a fake `claude` and a fake `copilot` on PATH, both runnable."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    agents = {
        name: _install_fake_agent(tmp_path, bin_dir, monkeypatch, name) for name in _AGENT_NAMES
    }
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    project_dir = tmp_path / "project"
    (project_dir / "skillbench").mkdir(parents=True)
    (project_dir / "skillbench" / "skillbench.yaml").write_text("", encoding="utf-8")
    monkeypatch.chdir(project_dir)
    return FakeAgents(**agents)


@pytest.fixture
def fake_claude(fake_agents, monkeypatch) -> FakeAgent:
    """Only `claude` is usable: the copilot on PATH is a broken install."""
    fake_agents.copilot.break_install(monkeypatch)
    return fake_agents.claude


@pytest.fixture
def fake_copilot(fake_agents, monkeypatch) -> FakeAgent:
    """Only `copilot` is usable: the claude on PATH is a broken install."""
    fake_agents.claude.break_install(monkeypatch)
    return fake_agents.copilot


def write_test_yaml(directory: Path, spec: dict, file_name: str = "test.yaml") -> Path:
    test_path = directory / file_name
    test_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return test_path


def run_skillbench(*args: str) -> int:
    return main(["run", *args])


def skillbench(*args: str) -> int:
    """Any skillbench command, e.g. skillbench("skill", "--claude")."""
    return main(list(args))


def init_project(defaults: str | None = None) -> Path:
    """`skillbench init` in the current folder, without the sample tests; returns skillbench/."""
    assert main(["init"]) == 0
    project_dir = Path.cwd() / "skillbench"
    for sample in ("skills/example.yaml", "agent/example.yaml"):
        (project_dir / sample).unlink()
    if defaults is not None:
        (project_dir / "skillbench.yaml").write_text(defaults, encoding="utf-8")
    return project_dir


def runs_dir() -> Path:
    return Path.cwd() / "skillbench" / "runs"


def project_records() -> list[dict]:
    paths = runs_dir().glob("*/run.json")
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    return sorted(records, key=lambda record: record["start_time"])


def write_skill(root: Path, name: str, relative_dir: str = ".claude/skills") -> Path:
    skill_dir = root / relative_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill {name}\n---\nDo {name}.\n", encoding="utf-8"
    )
    return skill_dir


def write_plugin(root: Path, plugin_name: str, skill_names: list[str]) -> Path:
    plugin_dir = root / plugin_name
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_name}), encoding="utf-8"
    )
    for skill_name in skill_names:
        write_skill(plugin_dir, skill_name, "skills")
    return plugin_dir


def only_run_dir() -> Path:
    run_dirs = [entry for entry in runs_dir().iterdir() if entry.is_dir()]
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


def assert_loads_nothing_from_the_network(html: str) -> None:
    """A page that works from disk: a full document with no external resources."""
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert not re.search(r"<(script|link|img|iframe)[^>]*(src|href)=", html, re.IGNORECASE)
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?https?:", html, re.IGNORECASE)
