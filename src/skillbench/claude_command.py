"""Building the `claude` command-line arguments for each turn of a test."""

import json

from skillbench.config import TestSpec

# Asking the user mid-turn would block a headless run; skillbench answers between turns instead.
_ALWAYS_DENIED_TOOLS = ("AskUserQuestion",)
_EMPTY_MCP_CONFIG = json.dumps({"mcpServers": {}})


def build_turn_args(message: str, resume_session_id: str | None) -> list[str]:
    """The arguments that differ per turn. They must come before `build_session_args`: the
    message is positional, and claude's list-valued flags (--disallowedTools, --add-dir, ...)
    would otherwise swallow it."""
    args = ["-p", message]
    if resume_session_id is not None:
        args += ["--resume", resume_session_id]
    return args


def build_session_args(spec: TestSpec, model: str | None, has_api_key: bool) -> list[str]:
    """The arguments every turn shares. `model` is the effective model, or None for the default."""
    args = [
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
        "--disallowedTools",
        *_ALWAYS_DENIED_TOOLS,
        *spec.denied_tools,
    ]
    if spec.allowed_tools:
        args += ["--allowedTools", *spec.allowed_tools]
    if model is not None:
        args += ["--model", model]
    args += _isolation_args(spec.isolation, has_api_key)
    args += _skill_dir_args(spec)
    return args


def _isolation_args(isolation: str, has_api_key: bool) -> list[str]:
    if isolation == "none":
        return []
    if has_api_key:
        return ["--bare"]
    # --bare needs an API key; with a subscription login, approximate it instead.
    return [
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        _EMPTY_MCP_CONFIG,
    ]


def _skill_dir_args(spec: TestSpec) -> list[str]:
    if spec.skill_dir is None:
        return []
    if spec.is_skill_dir_a_plugin:
        return ["--plugin-dir", str(spec.skill_dir)]
    return ["--add-dir", str(spec.skill_dir)]
