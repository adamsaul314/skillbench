"""`skillbench init`: the project folder with defaults, sample tests and a sample repo."""

from pathlib import Path

from skillbench.config import CONFIG_FILE_NAME, PROJECT_DIR_NAME

_CONFIG = """\
# Defaults for every test in this folder. A test's own fields win over these,
# and command-line flags (--model, --repeat) win over both.

# plugin: .                  # building a plugin? load it into every test, whatever the fixture
# model: claude-haiku-5-5    # passed straight to claude (Haiku is the cheapest)
timeout_s: 600               # seconds before one turn is stopped
# repeat: 1                  # run every test N times (each run uses real quota)
# min_pass_rate: 1.0         # with repeats: the share of runs that must pass
# fixture: .                 # the repo tests work in when they don't name one ("." = this repo)
"""

_GITIGNORE = """\
runs/
"""

_SKILL_TEST = """\
# A skill test: skillbench invokes one skill directly and checks what it did.
# Run it with:  skillbench skill
#
#   skill:    the skill's name, or its path in the repo. skillbench looks in .claude/skills/,
#             skills/, .github/skills/ and any plugin, and builds the /command.
#   input:    the text after the /command.
#   fixture:  the repo the agent works in, copied to a temp folder first so nothing real is
#             touched. "." is this repo, a path is a folder in it, a git URL is cloned:
#               fixture: {git: https://github.com/you/app.git, ref: main}
name: hello-greets-by-name
skill: hello
input: the platform team
fixture: skillbench/fixtures/sample-repo
assert:
  - skill_invoked: hello
  - final_text_contains: Hello
  - tool_not_called: Write

# Trigger tests: does the skill's description make the agent pick it? Each prompt is one
# short run that stops as soon as the agent decides. Uncomment to try:
# triggers:
#   should: ["say hi to Ada for me"]
#   should_not: ["what is 2 + 2?"]
"""

_AGENT_TEST = """\
# An agent loop test: a normal prompt, nothing forced. Checks that the agent picked the right
# skills and tools on its own and finished the job.
# Run it with:  skillbench agent
#
#   answers:  replies sent one per turn when the agent stops to ask something.
#   assert:   checks on what happened. tool_order passes if those tools were called in that
#             order (other calls in between are fine).
name: hello-loop
prompt: Please greet the platform team for me.
fixture: skillbench/fixtures/sample-repo
answers: []
assert:
  - skill_invoked: hello
  - skill_not_invoked: deploy
  - tool_order: [Skill]
  - final_text_contains: Hello
"""

_SAMPLE_REPO_README = """\
# sample-repo

A tiny repo for skillbench's sample tests. Point `fixture:` at your own repo instead.
"""

_SAMPLE_SKILL = """\
---
name: hello
description: Greet a person or group by name. Use when asked to greet someone or say hello.
---

Reply with exactly one line, `Hello, <name>!`, using the name you were given.
Do not create or edit any files.
"""

_FILES = {
    CONFIG_FILE_NAME: _CONFIG,
    ".gitignore": _GITIGNORE,
    "skills/example.yaml": _SKILL_TEST,
    "agent/example.yaml": _AGENT_TEST,
    "fixtures/sample-repo/README.md": _SAMPLE_REPO_README,
    "fixtures/sample-repo/.claude/skills/hello/SKILL.md": _SAMPLE_SKILL,
}


def scaffold(parent: Path) -> tuple[list[Path], list[Path]]:
    """Write the project folder under `parent`, never overwriting a file. Returns (created,
    already existing) paths."""
    project_dir = parent / PROJECT_DIR_NAME
    created, existing = [], []
    for relative_path, content in _FILES.items():
        path = project_dir / relative_path
        if path.exists():
            existing.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)
    return created, existing
