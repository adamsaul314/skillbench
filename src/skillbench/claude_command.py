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
    # Ignore the user's own config: --bare needs an API key; with a subscription login,
    # keep only project settings and no MCP servers instead.
    if has_api_key:
        args.append("--bare")
    else:
        args += ["--setting-sources", "project", "--strict-mcp-config"]
        args += ["--mcp-config", _EMPTY_MCP_CONFIG]
    for plugin_dir in spec.plugin_dirs:
        args += ["--plugin-dir", str(plugin_dir)]
    return args
