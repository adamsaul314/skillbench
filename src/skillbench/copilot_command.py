"""Building the `copilot` command-line arguments for each turn of a test, and making the test's
skill visible to Copilot."""

import shutil
from pathlib import Path

from skillbench.config import TestSpec

# Copilot discovers project skills here, one folder per skill.
_WORKSPACE_SKILLS_DIR = Path(".claude") / "skills"


def build_turn_args(message: str, session_id: str | None) -> list[str]:
    """The arguments that differ per turn. Every turn names the session, so follow-up turns
    continue the conversation the first one started."""
    args = ["-p", message]
    if session_id is not None:
        args += ["--session-id", session_id]
    return args


def build_session_args(spec: TestSpec, model: str | None) -> list[str]:
    """The arguments every turn shares. `model` is the effective model, or None for the default.
    `allowed_tools` has no Copilot equivalent: --allow-all already grants every tool."""
    args = ["--output-format", "json", "--allow-all", "--no-ask-user"]
    if spec.isolation == "clean":
        args.append("--no-custom-instructions")
    for tool in spec.denied_tools:
        args += ["--deny-tool", tool]
    if model is not None:
        args += ["--model", model]
    if spec.is_skill_dir_a_plugin:
        args += ["--plugin-dir", str(spec.skill_dir)]
    return args


def install_skill(spec: TestSpec, workspace: Path) -> None:
    """Copy a plain skill folder into the workspace's skills; plugins are passed as a flag."""
    if spec.skill_dir is None or spec.is_skill_dir_a_plugin:
        return
    shutil.copytree(spec.skill_dir, workspace / _WORKSPACE_SKILLS_DIR / spec.skill_dir.name)
