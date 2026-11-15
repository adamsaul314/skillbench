"""Building the `copilot` command-line arguments for each turn of a test."""

from skillbench.config import TestSpec


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
    args = ["--output-format", "json", "--allow-all", "--no-ask-user", "--no-custom-instructions"]
    for tool in spec.denied_tools:
        args += ["--deny-tool", tool]
    if model is not None:
        args += ["--model", model]
    for plugin_dir in spec.plugin_dirs:
        args += ["--plugin-dir", str(plugin_dir)]
    return args
