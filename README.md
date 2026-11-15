# skillbench

Test your Claude Code skills and agent workflows, the way you test code.

You write a small YAML file that says *"send this to Claude, then check these things"*. skillbench runs Claude Code for real on a copy of your repo, records everything it did, and tells you **pass** or **fail**.

---

## 1. Install

You need Python 3.11+, git, and Claude Code installed and logged in (check with `claude --version`).

Install from [PyPI](https://pypi.org/project/skillbench-cli/) with **one** of these, whichever you normally use:

| Tool | Command |
|---|---|
| pip | `pip install skillbench-cli` |
| pipx | `pipx install skillbench-cli` |
| uv | `uv tool install skillbench-cli` |

The package is called `skillbench-cli`, but the command is `skillbench`. Check it worked:

```
$ skillbench --help
usage: skillbench [-h] {init,skill,agent} ...

    init              create the skillbench/ folder with sample tests
    skill             run tests in skillbench/skills/
    agent             run tests in skillbench/agent/
```

To upgrade later, run the same tool with its upgrade option, e.g. `pip install -U skillbench-cli`.

## 2. Try it (2 minutes)

In your repo:

```
skillbench init      # creates a skillbench/ folder with two example tests
skillbench skill     # runs the skill test
skillbench agent     # runs the agent test
```

You'll see something like:

```
[1/1] hello-greets-by-name  default model
hello-greets-by-name: pass in 2.3s
dashboard: skillbench/runs/index.html
```

Open `skillbench/runs/index.html` in your browser to see exactly what Claude did: every message, tool call and check.

That's it. The rest of this page shows how to write your own tests.

---

## 3. Two kinds of test

| | **Skill test** | **Agent test** |
|---|---|---|
| Question it answers | "When my skill runs, does it do the right thing?" | "Given a normal request, does Claude pick the right skills and get the job done?" |
| Lives in | `skillbench/skills/` | `skillbench/agent/` |
| Run with | `skillbench skill` | `skillbench agent` |
| You write | `skill:` and `input:` | `prompt:` |

### A skill test

`skillbench/skills/grilling.yaml`

```yaml
skill: grilling                 # your skill's name
input: an idea for a todo app   # what to send it
fixture: .                      # run it on a copy of this repo

assert:
  - final_text_contains: "Q1"
  - tool_not_called: Write
```

skillbench finds the skill wherever it lives in your repo (`.claude/skills/`, `skills/`, `.github/skills/` or a plugin) and runs it as `/grilling an idea for a todo app`.

### An agent test

`skillbench/agent/csv-export.yaml`

```yaml
prompt: add CSV export to the report page
fixture: .

answers:                       # replies, sent one at a time when Claude asks something
  - Yes to all your recommendations.
  - approved

assert:
  - skill_invoked: planner
  - tool_order: [Read, Edit, Bash]
  - command_succeeds: python -m pytest -q
```

Nothing is forced here. Claude sees every skill in your repo and decides for itself, and the test checks what it chose.

---

## 4. Checks you can make

Put any of these under `assert:`.

| Check | Passes when |
|---|---|
| `skill_invoked: grilling` | Claude used that skill |
| `skill_not_invoked: grilling` | Claude didn't use that skill |
| `tool_order: [Read, Edit]` | Claude used these tools in this order (other tools in between are fine) |
| `tool_not_called: Write` | Claude never used that tool |
| `final_text_contains: "Done"` | Claude's last message contains the text |
| `file_contains: {path: notes.md, text: "approved"}` | That file exists afterwards and contains the text |
| `command_succeeds: python -m pytest -q` | The command runs successfully in the workspace afterwards |

A test passes when every check passes.

---

## 5. Which repo Claude works in (`fixture:`)

Claude always works on a **copy**, so your real files are never touched.

| `fixture:` | Claude gets |
|---|---|
| *(leave it out)* | An empty folder |
| `.` | A copy of your repo, including work you haven't committed |
| `some/folder` | A copy of that folder |
| `https://github.com/you/app.git` | A fresh clone |
| `{git: https://github.com/you/app.git, ref: v1.2}` | A clone at that branch, tag or commit |

Paths are relative to your repo root. The copy is kept after the run so you can look inside, and its path is in the dashboard.

---

## 6. Running tests

```
skillbench skill                 # all skill tests
skillbench skill grilling        # just the tests for one skill (or one test by name)
skillbench agent csv-export      # one agent test
skillbench agent --repeat 5      # run each test 5 times
skillbench agent --model claude-sonnet-5-5
```

While it runs you'll see the current test, what Claude is doing, and roughly how long is left.

---

## 7. More options

You won't need these to start.

**Check a skill gets picked on its own.** List prompts that should and shouldn't trigger it. Each runs briefly and stops as soon as Claude decides:

```yaml
skill: grilling
triggers:
  should: ["stress-test my plan"]
  should_not: ["fix this typo"]
```

**Answer Claude yourself.** Add `interactive: true` and skillbench asks *you* in the terminal whenever Claude is waiting for a reply. Press Enter on an empty line to finish.

**Check for flakiness.** Claude doesn't do exactly the same thing every time. `--repeat 5` (or `repeat: 5` in the test) runs it 5 times and shows how often each check passed. Add `min_pass_rate: 0.8` to pass when 4 out of 5 runs pass.

**Set defaults for every test** in `skillbench/skillbench.yaml`:

```yaml
model: claude-sonnet-5-5
timeout_s: 600        # seconds before a turn is stopped
fixture: .
repeat: 1
min_pass_rate: 1.0
```

A test's own settings win over these, and command-line flags win over both.

**All test fields:**

| Field | Meaning |
|---|---|
| `skill` / `input` | Skill tests: which skill, and what to send it |
| `prompt` | Agent tests: the first message |
| `answers` | Replies sent one per turn |
| `interactive` | Ask you in the terminal for replies |
| `triggers` | Prompts that should / shouldn't pick the skill |
| `fixture` | Which repo Claude works in |
| `assert` | The checks |
| `name` | A display name (defaults to the file name) |
| `timeout_s`, `repeat`, `min_pass_rate` | As above |

---

## Good to know

- **It costs real usage.** Every run is a real Claude session. Start with skill tests; long agent tests take longer and cost more.
- **Your personal setup is ignored.** Only the skills and plugins in the repo being tested are loaded, so results don't depend on whose machine runs them.
- **Only test repos you trust.** Claude has full tool access inside the copy, and `command_succeeds` runs commands there.
- **`command_succeeds` uses your system shell** (`sh` on Mac/Linux, `cmd` on Windows), so keep commands simple if you share tests across systems.
- **Results** are in `skillbench/runs/`, which is git-ignored. Each run has a `run.json` and Claude's raw output.

| Status | Meaning |
|---|---|
| `pass` | Every check passed |
| `fail` | Claude finished, but a check failed |
| `error` | Claude crashed or timed out, or the skill or fixture couldn't be found |
