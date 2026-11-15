"""Trigger tests: does the skill fire for prompts that should trigger it, and not for others."""

import time
from pathlib import Path

from conftest import init_project, project_records, skillbench, write_skill, write_test_yaml


def _trigger_test(project_dir: Path, **fields) -> None:
    write_test_yaml(
        project_dir / "skills", {"name": "hello", "skill": "hello", "fixture": ".", **fields}
    )


def test_a_should_prompt_passes_when_the_skill_fires(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "hello_skill.jsonl")
    write_skill(Path.cwd(), "hello")
    _trigger_test(init_project(), triggers={"should": ["say hi to Ada"]})

    assert skillbench("skill") == 0

    [record] = project_records()
    assert record["test_name"] == "hello [should] say hi to Ada"
    assert record["trigger"] == {"prompt": "say hi to Ada", "should_invoke": True}
    assert [result["type"] for result in record["assertions"]] == ["skill_invoked"]
    assert fake_claude.argv[fake_claude.argv.index("-p") + 1] == "say hi to Ada"


def test_a_should_not_prompt_passes_when_the_skill_stays_quiet(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "basic.jsonl")  # no Skill call
    write_skill(Path.cwd(), "hello")
    _trigger_test(init_project(), triggers={"should_not": ["what is 2 + 2?"]})

    assert skillbench("skill") == 0

    [record] = project_records()
    assert record["assertions"][0]["type"] == "skill_not_invoked"
    assert record["status"] == "pass"


def test_trigger_results_are_judged_both_ways(fake_claude, monkeypatch, capsys):
    fake_claude.use_stream(monkeypatch, "hello_skill.jsonl")  # always fires
    write_skill(Path.cwd(), "hello")
    _trigger_test(init_project(), triggers={"should": ["greet Ada"], "should_not": ["2 + 2"]})

    assert skillbench("skill") == 1

    statuses = {record["test_name"]: record["status"] for record in project_records()}
    assert statuses == {"hello [should] greet Ada": "pass", "hello [should not] 2 + 2": "fail"}


def test_a_trigger_run_stops_as_soon_as_the_skill_fires(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "hello_skill.jsonl")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP_S", "30")  # the agent would hang on after deciding
    write_skill(Path.cwd(), "hello")
    _trigger_test(init_project(), triggers={"should": ["greet Ada"]}, timeout_s=60)

    started = time.monotonic()
    assert skillbench("skill") == 0

    assert time.monotonic() - started < 20
    [record] = project_records()
    assert record["stop_reason"] == "trigger_decided"
    assert record["run_status"] == "completed"


def test_with_input_the_forced_run_happens_too(fake_claude, monkeypatch):
    fake_claude.use_stream(monkeypatch, "hello_skill.jsonl")
    write_skill(Path.cwd(), "hello")
    _trigger_test(init_project(), input="Ada", triggers={"should": ["greet Ada"]})

    skillbench("skill")

    prompts = [inv["argv"][inv["argv"].index("-p") + 1] for inv in fake_claude.invocations]
    assert prompts == ["/hello Ada", "greet Ada"]


def test_a_trigger_for_a_missing_skill_is_an_error(fake_claude):
    _trigger_test(init_project(), triggers={"should": ["greet Ada"]})

    assert skillbench("skill") == 1

    assert "skill 'hello' not found" in project_records()[0]["error"]
    assert not fake_claude.was_launched
