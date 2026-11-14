"""The overpowered smoke test: its fixture project, its eval YAMLs, and `command_succeeds`."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skillbench.config import load_test
from skillbench.workspace import create_workspace

from conftest import load_run_record, run_skillbench, write_test_yaml

REPO_ROOT = Path(__file__).parent.parent
TINY_PROJECT = REPO_ROOT / "fixtures" / "tiny-project"
SMOKE_TEST = REPO_ROOT / "evals" / "smoke" / "overpowered-loop.yaml"
SKILL_TESTS_DIR = REPO_ROOT / "evals" / "skills"
# The feature slug the smoke test's prompt tells the agent to use.
SMOKE_SLUG = "greet-name"
# The eval YAMLs point at a checkout of the overpowered plugin next to this repo.
OVERPOWERED_DIR = REPO_ROOT.parent / "overpowered"
needs_overpowered = pytest.mark.skipif(
    not OVERPOWERED_DIR.is_dir(), reason=f"overpowered plugin not found at {OVERPOWERED_DIR}"
)
PROMPT = "/overpowered:grilling an idea for a todo app"
PYTHON = f'"{sys.executable}"'


def _run_pytest(project_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )


def test_ac6_1_tiny_project_has_one_passing_test_in_a_git_workspace():
    workspace = create_workspace(TINY_PROJECT)
    try:
        result = _run_pytest(workspace)
        git_status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert git_status.returncode == 0
    # A .gitignore keeps pytest's caches out of the diffs the overpowered loop reviews.
    assert (TINY_PROJECT / ".gitignore").is_file()


@needs_overpowered
def test_ac6_2_smoke_test_drives_understand_then_approve_then_build_slice_1():
    spec = load_test(SMOKE_TEST)

    assert spec.prompt.startswith("/overpowered:understand ")
    assert f"slug {SMOKE_SLUG}" in spec.prompt
    assert spec.fixture == TINY_PROJECT.resolve()
    assert spec.skill_dir == OVERPOWERED_DIR.resolve()
    assert "approved" in spec.answers
    assert spec.answers[-1] == f"/overpowered:build {SMOKE_SLUG} slice-1"


@needs_overpowered
def test_ac6_3_smoke_test_asserts_the_approved_plan_the_build_and_a_passing_test_command():
    spec = load_test(SMOKE_TEST)

    expected = [(assertion.type, assertion.expected) for assertion in spec.assertions]
    plan_is_approved = {
        "path": f"docs/features/{SMOKE_SLUG}/plan.md",
        "text": "approval: approved",
    }
    assert ("file_contains", plan_is_approved) in expected
    assert ("skill_invoked", "overpowered:build") in expected
    assert ("command_succeeds", "python -m pytest -q") in expected


@needs_overpowered
def test_ac6_3_smoke_test_runs_end_to_end_on_a_fake_agent(fake_claude, monkeypatch):
    # The fake writes no plan, so only the fixture's own test command can pass; the run still
    # sends every scripted answer and checks each assertion against the copied tiny project.
    fake_claude.use_stream(monkeypatch, "assertions.jsonl")
    spec = load_test(SMOKE_TEST)

    run_skillbench(str(SMOKE_TEST))

    record = load_run_record()
    assert record["conversation_turns"] == 1 + len(spec.answers)
    results = {result["type"]: result for result in record["assertions"]}
    assert results["command_succeeds"]["passed"] is True
    assert results["file_contains"]["passed"] is False
    assert results["skill_invoked"]["passed"] is True


def _make_fixture(parent: Path) -> None:
    (parent / "fixture" / "docs").mkdir(parents=True)
    (parent / "fixture" / "docs" / "notes.md").write_text("notes\n", encoding="utf-8")


def _run_one_assertion(fake_claude, monkeypatch, assertion: dict, **spec) -> dict:
    fake_claude.use_stream(monkeypatch, "assertions.jsonl")
    _make_fixture(Path.cwd())
    test_path = write_test_yaml(
        Path.cwd(),
        {"name": "asserts", "prompt": PROMPT, "fixture": "fixture", "assert": [assertion], **spec},
    )
    run_skillbench(str(test_path))
    [result] = load_run_record()["assertions"]
    return result


def test_ac6_3_command_succeeds_passes_when_the_command_exits_0_in_the_workspace(
    fake_claude, monkeypatch
):
    # Exits 0 only where the fixture was copied, so it proves the command ran in the workspace.
    command = (
        f"{PYTHON} -c \"import os, sys; sys.exit(0 if os.path.isfile('docs/notes.md') else 1)\""
    )

    result = _run_one_assertion(fake_claude, monkeypatch, {"command_succeeds": command})

    assert result["passed"] is True
    assert "exited with code 0" in result["reason"]


def test_ac6_3_command_succeeds_fails_with_the_exit_code_and_output(fake_claude, monkeypatch):
    command = f"{PYTHON} -c \"print('tests broke'); raise SystemExit(3)\""

    result = _run_one_assertion(fake_claude, monkeypatch, {"command_succeeds": command})

    assert result["passed"] is False
    assert "exited with code 3" in result["reason"]
    assert "tests broke" in result["reason"]


@pytest.mark.parametrize("bad_value", ["", 5, ["pytest"]])
def test_ac6_3_command_succeeds_needs_a_command_string(fake_claude, capsys, bad_value):
    test_path = write_test_yaml(
        Path.cwd(),
        {"name": "bad", "prompt": PROMPT, "assert": [{"command_succeeds": bad_value}]},
        "bad-command.yaml",
    )

    exit_code = run_skillbench(str(test_path))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "bad-command.yaml" in error_output
    assert "command_succeeds" in error_output
    assert not fake_claude.was_launched


def test_ac6_3_skill_invoked_counts_a_slash_command_sent_as_a_scripted_answer(
    fake_claude, monkeypatch
):
    # assertions.jsonl loads overpowered:build but never calls the Skill tool for it.
    result = _run_one_assertion(
        fake_claude,
        monkeypatch,
        {"skill_invoked": "overpowered:build"},
        answers=["approved", "/overpowered:build todo slice-1"],
    )

    assert result["passed"] is True
    assert "user message" in result["reason"]


def test_ac6_3_skill_invoked_ignores_a_slash_command_that_is_not_the_message_start(
    fake_claude, monkeypatch
):
    result = _run_one_assertion(
        fake_claude,
        monkeypatch,
        {"skill_invoked": "overpowered:build"},
        answers=["later, run /overpowered:build todo slice-1"],
    )

    assert result["passed"] is False


@needs_overpowered
def test_ac6_5_skill_unit_tests_load_and_each_asserts_its_skill_was_invoked():
    skill_tests = sorted(SKILL_TESTS_DIR.glob("*.yaml"))
    assert skill_tests, f"no skill unit tests in {SKILL_TESTS_DIR}"

    for test_path in skill_tests:
        spec = load_test(test_path)
        skill_name = spec.prompt.split()[0].removeprefix("/")
        assert spec.skill_dir == OVERPOWERED_DIR.resolve()
        assert ("skill_invoked", skill_name) in {
            (assertion.type, assertion.expected) for assertion in spec.assertions
        }
