# skillbench

Test your Claude Code skills and agent workflows, the way you test code.

You write a short YAML file: *"send Claude this, then check these things"*. skillbench runs Claude Code for real on a **copy** of a repo, records everything it did, and tells you **pass** or **fail**.

**Jump to:** [Get started](#get-started) · [Write a test](#write-a-test) · [Run tests](#run-tests) · [Reference](#reference)

---

## Get started

You need Python 3.11+, git, and Claude Code installed and logged in (`claude --version` should work).

**1. Install it.** Use whichever tool you normally use:

```
pip install skillbench-cli
pipx install skillbench-cli
uv tool install skillbench-cli
```

The package is `skillbench-cli`; the command is `skillbench`.

**2. Set up your repo.** In the repo you want to test:

```
skillbench init
```

This creates one folder, `skillbench/`, with two example tests:

```
skillbench/
  skillbench.yaml     settings for every test
  skills/             skill tests
  agent/              agent tests
  fixtures/           small sample repos for tests to run on
  runs/               results (git-ignored)
```

**3. Run the examples.**

```
skillbench skill
skillbench agent
```

**4. Look at the results.** Open `skillbench/runs/index.html` in your browser. Each run shows what it tested, its steps, why it didn't pass (if it didn't), and everything Claude did.

Now write your own tests: next section.

---

## Write a test

Find what you want to check in this list, then follow that recipe.

| I want to check that… | Recipe |
|---|---|
| my skill does the right thing when it runs | [A. Test a skill](#a-test-a-skill) |
| Claude picks my skill when it should, and not when it shouldn't | [B. Test a skill gets picked](#b-test-a-skill-gets-picked) |
| a normal request gets done, with the right skills | [C. Test a request end to end](#c-test-a-request-end-to-end) |
| my multi-step workflow works on a real repo | [D. Test a workflow on a repo](#d-test-a-workflow-on-a-repo) |
| the plugin I'm building works on other projects | [E. Test a plugin you're building](#e-test-a-plugin-youre-building) |

**Or let Claude write it.** Install the `/eval-creator` skill once:

```
skillbench install-skill
```

Then in Claude Code, in your repo, say what you want to test:

```
/eval-creator test that my grilling skill asks questions before writing anything
```

It reads your skills, asks what it can't work out, writes the test files and checks them. It never runs them, so you decide when to spend tokens. Run `skillbench install-skill` again after you upgrade skillbench.

Whichever way you write tests, check them for free before running:

```
skillbench check
```

### A. Test a skill

Runs your skill directly (`/grilling an idea for a todo app`) and checks what it did.

Create `skillbench/skills/grilling.yaml`:

```yaml
description: Grilling asks questions before it writes anything.
skill: grilling                 # the skill's name
input: an idea for a todo app   # the text after /grilling
fixture: .                      # run on a copy of this repo

assert:
  - final_text_contains: "?"
  - tool_not_called: Write
```

Run it: `skillbench skill grilling`

skillbench finds the skill wherever it is: `.claude/skills/`, `skills/`, `.github/skills/`, or inside a plugin.

### B. Test a skill gets picked

Checks your skill's description is good enough for Claude to choose it on its own. Each prompt is a short run that stops as soon as Claude decides, so these are cheap.

Add `triggers:` to a skill test:

```yaml
skill: grilling
triggers:
  should: ["stress-test my plan before I build it"]
  should_not: ["fix the typo in README.md"]
```

Run it: `skillbench skill grilling`

### C. Test a request end to end

Sends a normal message, with nothing forced. Claude sees every skill in the repo and decides for itself; the test checks what it chose and whether the job got done.

Create `skillbench/agent/csv-export.yaml`:

```yaml
description: Adding CSV export uses the planner and leaves the tests passing.
prompt: add CSV export to the report page
fixture: .

answers:                        # sent one at a time, each time Claude stops and waits
  - Yes to all your recommendations.
  - approved

assert:
  - skill_invoked: planner
  - command_succeeds: python -m pytest -q
```

Run it: `skillbench agent csv-export`

Not sure what Claude will ask? Leave out `answers:` and add `interactive: true`. skillbench then asks *you* in the terminal each time Claude waits for a reply (press Enter on an empty line to stop). Copy your replies into `answers:` afterwards.

### D. Test a workflow on a repo

For workflows with several phases, where in Claude Code you'd type things between messages: commit, `/clear`, start the next skill. Put those in `answers:` exactly as you'd type them:

| In `answers:` | What happens |
|---|---|
| `approved` | Sent to Claude as a message |
| `/clear` | The next message starts a fresh session |
| `!git add -A && git commit -m "plan"` | Runs in the repo as a shell command, not sent to Claude |

Point `fixture:` at the repo to run on. A git URL is cloned fresh for each run, and pushing from the clone is turned off.

Create `skillbench/agent/full-loop.yaml`:

```yaml
description: The plan, build and review loop adds a dark mode toggle to test-app.
prompt: /my-plugin:plan add a dark mode toggle. Use the slug dark-mode.
fixture: https://github.com/you/test-app.git

answers:
  - Yes to all your recommendations.
  - approved
  - '!git add -A && git commit -m "plan: dark-mode"'
  - /clear
  - /my-plugin:build dark-mode
  - '!git add -A && git commit -m "dark-mode: build"'
  - /clear
  - /my-plugin:final-review dark-mode

assert:
  - subagent_invoked: reviewer
  - file_contains: {path: docs/features/dark-mode/plan.md, text: "approved"}
  - command_succeeds: npm test
```

Run it on a cheap model first: `skillbench agent full-loop --model claude-haiku-5-5`

If a `!` command fails, the run stops there with `error`, since later steps depend on it.

### E. Test a plugin you're building

If your repo **is** a Claude Code plugin (it has `.claude-plugin/plugin.json`), add this line to `skillbench/skillbench.yaml`:

```yaml
plugin: .
```

Now your plugin's skills and subagents are loaded into every test, whatever repo the test runs on. Combine it with recipe D to run your workflow on any project. Each run gets its own copy of the plugin, so the dashboard shows exactly which version was tested. `skillbench check` warns you if you forget this line.

---

## Run tests

```
skillbench skill                 # every skill test
skillbench agent                 # every agent test
skillbench agent full-loop       # one test, by name (or every test for one skill)
skillbench agent --model claude-haiku-5-5
skillbench agent --repeat 5      # run each test 5 times
```

The terminal shows one line per test:

```
✓ hello-greets-by-name   passed      12s   $0.03
✗ full-loop              error       41s   $0.02
    stopped at step 7: '!git commit -m "dark-mode: build"' exited with code 1

1 passed, 1 failed  ·  53s  ·  $0.05
details: skillbench/runs/index.html
```

| Result | Meaning |
|---|---|
| `pass` | Every check passed |
| `fail` | Claude finished, but a check failed |
| `error` | The run stopped: Claude crashed or timed out, a `!` command failed, or the skill or repo couldn't be found |

For the details, open `skillbench/runs/index.html`: what was tested, each step, why it didn't pass, Claude's last message, every check, and the full timeline of messages and tool calls.

**Keep costs down.** Every run is a real Claude session. Use `--model claude-haiku-5-5` while you're getting a test right, start with skill tests, and run long workflows only when you need to.

**Flaky results?** Claude doesn't do exactly the same thing every time. `--repeat 5` shows how often each check passed. Add `min_pass_rate: 0.8` to a test to pass it when 4 of 5 runs pass.

---

## Reference

### Checks (`assert:`)

A test passes when every check passes.

| Check | Passes when |
|---|---|
| `skill_invoked: grilling` | Claude used that skill |
| `skill_not_invoked: grilling` | Claude didn't use that skill |
| `subagent_invoked: reviewer` | Claude handed work to that subagent |
| `tool_order: [Read, Edit]` | Claude used these tools in this order (others in between are fine) |
| `tool_not_called: Write` | Claude never used that tool |
| `final_text_contains: "Done"` | Claude's last message contains the text |
| `file_contains: {path: notes.md, text: "approved"}` | The file exists afterwards and contains the text |
| `command_succeeds: npm test` | The command succeeds in the repo afterwards |

### Repos (`fixture:`)

Claude always works on a copy, so your real files are never touched.

| `fixture:` | Claude works in |
|---|---|
| *(left out)* | An empty folder |
| `.` | A copy of your repo, including uncommitted work |
| `some/folder` | A copy of that folder (paths are from your repo root) |
| `https://github.com/you/app.git` | A fresh clone |
| `{git: https://github.com/you/app.git, ref: v1.2}` | A clone at that branch, tag or commit |

The copy is kept after the run so you can look inside. Its path is on the run's dashboard page.

### Test fields

| Field | Meaning |
|---|---|
| `description` | One sentence saying what the test checks, shown on the dashboard |
| `skill`, `input` | Skill tests: which skill, and the text sent with it |
| `triggers` | Skill tests: prompts that `should` and `should_not` pick the skill |
| `prompt` | Agent tests: the first message |
| `answers` | Replies, one per turn; also `/clear` and `!command` |
| `interactive` | `true` to answer in the terminal once `answers` run out |
| `fixture` | The repo Claude works in |
| `assert` | The checks |
| `name` | Display name (defaults to the file name) |
| `timeout_s` | Seconds before one turn is stopped |
| `repeat`, `min_pass_rate` | Run it N times; the share of runs that must pass |

### Settings for every test (`skillbench/skillbench.yaml`)

```yaml
plugin: .                 # load this repo's plugin into every test
model: claude-haiku-5-5
fixture: .
timeout_s: 600
repeat: 1
min_pass_rate: 1.0
```

A test's own fields win over these, and `--model` / `--repeat` win over both.

### Commands

| Command | Does |
|---|---|
| `skillbench init` | Creates `skillbench/` with example tests |
| `skillbench skill [name]` | Runs skill tests |
| `skillbench agent [name]` | Runs agent tests |
| `skillbench check` | Checks every test file, without running anything |
| `skillbench install-skill` | Adds `/eval-creator` to Claude Code |

---

## Good to know

- **Your personal Claude setup is ignored.** Only the skills and plugins in the repo being tested (plus `plugin:`) are loaded, so results don't depend on whose machine runs them.
- **Only test repos you trust.** Claude has full tool access inside the copy, and `command_succeeds` and `!` commands run there.
- **Commands use your system shell**: `sh` on Mac/Linux, `cmd` on Windows. Keep them simple if tests are shared across systems.
- **Each run is saved** in `skillbench/runs/`, with a `run.json` and Claude's raw output.
- **Upgrading:** `pip install -U skillbench-cli` (or your tool's upgrade command), then `skillbench install-skill`.

---

## Releasing a new version (maintainers)

From an up-to-date `main` with no uncommitted changes:

```
scripts/release.sh 0.2.0 --dry-run   # bump, lint, build and test the install, then undo
scripts/release.sh 0.2.0             # the same, then commit, tag, publish to PyPI and push
```

Publishing needs a PyPI API token: `export UV_PUBLISH_TOKEN=pypi-...` first, or uv asks for it.
