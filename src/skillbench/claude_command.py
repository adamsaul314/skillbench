"""Building the `claude` command-line arguments for one test turn."""

import json

from skillbench.config import TestSpec

_PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
# Asking the user mid-turn would block a headless run; skillbench answers between turns instead.
_ALWAYS_DENIED_TOOLS = ("AskUserQuestion",)
_EMPTY_MCP_CONFIG = json.dumps({"mcpServers": {}})


def build_claude_args(spec: TestSpec, model: str | None, has_api_key: bool) -> list[str]:
    """Arguments after the executable. `model` is the effective model, or None for the default."""
    # The prompt is positional and comes first: claude's list-valued flags
    # (--disallowedTools, --add-dir, ...) would otherwise swallow it.
    args = [
        "-p",
        spec.prompt,
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
    if (spec.skill_dir / _PLUGIN_MANIFEST).is_file():
        return ["--plugin-dir", str(spec.skill_dir)]
    return ["--add-dir", str(spec.skill_dir)]
