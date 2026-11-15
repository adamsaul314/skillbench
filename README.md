# skillbench

Test your agent skills, and whole agent loops, the way you test code.

skillbench runs **Claude Code** headless against a copy of your repo. It records every turn, tool call and skill the agent used, and checks what happened against the assertions you wrote.

```
$ skillbench init
$ skillbench skill
skillbench: 2 tests = 2 runs
test                      status  duration  turns
hello-greets-by-name      pass    8.2s      2
hello [should] say hi...  pass    3.1s      1
dashboard: skillbench/runs/index.html
```

---

## Install

You need **Python 3.11+**, **git**, **[uv](https://docs.astral.sh/uv/)**, and Claude Code installed and logged in (`claude --version`). It works on macOS, Linux and Windows.

```
uv tool install /path/to/skillbench
skillbench --help
```

## Quick start

Run this in the repo where your skills or agent setup live:

```
skillbench init        # creates skillbench/ with defaults, sample tests and a sample repo
skillbench skill       # runs skillbench/skills/*.yaml
skillbench agent       # runs skillbench/agent/*.yaml
```

The samples run as they are, against `skillbench/fixtures/sample-repo`. Change `fixture:` to `.` to test against your own repo instead.

Everything skillbench creates lives in one folder, so your repo stays clean:

```
skillbench/
  skillbench.yaml      defaults for every test
  skills/              skill tests
  agent/               agent loop tests
  fixtures/            sample repos (optional)
  runs/                results and the dashboard (git-ignored)
```

Commands find `skillbench/` from any folder inside your repo, the way git finds `.git`.

---

## Skill tests

A skill test invokes one skill directly and checks what it did.

`skillbench/skills/grilling.yaml`:

```yaml
name: grilling-asks-questions
skill: grilling                 # the skill's name, or its path in the repo
input: an idea for a todo app   # the text after the /command
fixture: .                      # this repo
assert:
  - skill_invoked: grilling
  - final_text_contains: "Q1"
  - tool_not_called: Write
```

You never write the `/command` yourself. skillbench finds the skill in the repo, looking in `.claude/skills/`, `skills/`, `.github/skills/` and any plugin (a folder with `.claude-plugin/plugin.json`), and builds the command, e.g. `/my-plugin:grilling` for a plugin's skill.

If two skills share a name, skillbench lists them. In that case write `plugin:skill` or the skill's path (`skill: plugins/mine/skills/grilling`).

### Trigger tests

Trigger tests check whether the skill's description makes the agent pick it without being told:

```yaml
name: grilling
skill: grilling
triggers:
  should: ["stress-test my plan", "grill me on this design"]
  should_not: ["fix this typo"]
```

Each prompt is its own short run, sent as plain text with no `/command`. A run stops as soon as the agent calls a skill, so trigger tests are cheap. A `should` prompt passes if the skill fired, and a `should_not` prompt passes if it didn't. If you also set `input:`, the forced run happens as well.

## Agent loop tests

An agent loop test uses a normal prompt and forces nothing. The agent works with every skill and plugin in the repo, and you check that it chose the right ones and finished the job.

`skillbench/agent/csv-export.yaml`:

```yaml
name: csv-export
prompt: add CSV export to the report page
fixture: .
answers:                                  # replies sent one per turn when the agent asks
  - Yes to all your recommendations, including any further questions.
  - approved
timeout_s: 1800
assert:
  - skill_invoked: planner
  - skill_not_invoked: grilling
  - tool_order: [Read, Edit, Bash]        # in this order; other calls in between are fine
  - command_succeeds: python -m pytest -q
```

Tips:
- **Put the names you assert on in the prompt** (a slug, a file name) so paths are predictable.
- **Make answers forgiving.** "Yes to all your recommendations, including any further questions" holds up better than "yes".
- Long loops cost real money. Get your skill tests passing first.

### Answering live

Set `interactive: true` to answer the agent yourself, as in a real session. Once the scripted answers run out, skillbench shows the agent's last message and you type the reply. An empty reply or `/done` ends the conversation.

---

## Fixtures: what repo the agent works in

Each run gets a fresh temp folder that holds the fixture, so your real files are never touched. The folder is kept afterwards so you can inspect it.

| `fixture:` | What you get |
|---|---|
| *(omitted)* | An empty git repo (greenfield) |
| `.` | A copy of this repo, including uncommitted and untracked files. Git-ignored files and the `skillbench/` folder are left out. |
| `path/in/repo` | A copy of that folder |
| `https://github.com/you/app.git` | A clone of the default branch |
| `{git: <url>, ref: v1.2}` | A clone checked out at a branch, tag or commit |

Paths are relative to the repo root (the folder that holds `skillbench/`).

The exact commit each run used is recorded and shown in the dashboard. Pin a `ref` when you want results that don't move with `main`. Private repos use the git credentials you already have. If a clone fails, that test is reported as `error` and the rest of the suite carries on.

> **Heads-up:** the agent has full tool access inside the workspace, and `command_succeeds` runs commands there. Only use fixture repos you trust.

## Running tests

```
skillbench skill                    # every skill test
skillbench skill grilling           # tests named grilling, in grilling.yaml, or testing skill grilling
skillbench agent csv-export
skillbench agent path/to/t.yaml     # or a folder
skillbench agent --model claude-sonnet-5-5
```

`--model` is passed to `claude` as given. The exit code is `0` only if every test passed. The dashboard is rewritten after every run.

While tests run, the terminal shows the current test, turn, latest tool call, elapsed time, and an overall bar with an estimated finish time. The estimate comes from the last recorded run of the same test, or else from the tests finished so far. When there's nothing to base it on yet, only elapsed time is shown. When output isn't a terminal (CI, pipes), you get one plain line per run instead.

### Repeats

Agents aren't deterministic: one run can pass by luck or fail by bad luck. Repeats show how reliable a test really is:

```
skillbench skill --repeat 5
```

```
skillbench: 3 tests × 5 repeats = 15 runs
...
grilling-asks-questions: 4/5 runs passed, needs 100% -> fail
  skill_invoked: grilling  5/5
  final_text_contains: Q1  4/5
```

The default is 1, so nobody pays for repeats by accident. Every repeat uses real quota. You can also set `repeat:` per test or in `skillbench.yaml`, and the flag wins. A repeated test passes when its pass rate is at least `min_pass_rate`. The default of 1.0 means every run must pass; set 0.8 to allow one flaky run in five.

## Reading the results

Open `skillbench/runs/index.html` in a browser. It needs no server and no network. Click a run to see its timeline: your messages, the agent's text, every tool call with its input and output, subagents nested under their parent, and each assertion with the reason it passed or failed.

| Status | Meaning |
|---|---|
| `pass` | Run finished and every assertion passed |
| `fail` | Run finished but an assertion failed |
| `error` | The agent crashed or timed out, or the fixture or skill couldn't be set up |

---

## Reference

### Test fields

| Key | What it does | Default |
|---|---|---|
| `skill` | Skill tests: the skill to invoke, by name, `plugin:skill` or path | |
| `input` | Skill tests: the text after the skill's `/command` | `""` |
| `triggers` | Skill tests: `should` / `should_not` prompt lists | |
| `prompt` | Agent loop tests: the first message | |
| `name` | Name shown in results | file name |
| `fixture` | The repo the agent works in (see Fixtures) | `skillbench.yaml`, else empty |
| `answers` | Scripted replies, one per turn | `[]` |
| `interactive` | Ask in the terminal once scripted answers run out | `false` |
| `timeout_s` | Seconds before a turn is stopped | `600` |
| `repeat` | Runs per invocation | `1` |
| `min_pass_rate` | With repeats: the share of runs that must pass | `1.0` |
| `assert` | List of checks | `[]` |

A test has either `skill` or `prompt`. Precedence is CLI flag, then the test, then `skillbench.yaml`, then the built-in default.

### Assertions

| Assertion | Passes when |
|---|---|
| `skill_invoked: grilling` | The skill ran, through the Skill tool or a `/skill` message. `grilling` also matches `my-plugin:grilling`. |
| `skill_not_invoked: grilling` | The skill never ran |
| `tool_not_called: Write` | The tool was never called (subagents included) |
| `tool_order: [Read, Edit, Bash]` | Those tools were called in that order, with anything in between. `[Bash]` checks a tool was called at all. |
| `final_text_contains: "Q1"` | The agent's last message contains the text |
| `file_contains: {path: docs/plan.md, text: "approved"}` | The file exists in the workspace and contains the text |
| `command_succeeds: python -m pytest -q` | The command exits 0 in the workspace |

`command_succeeds` runs through the platform shell: `sh` on macOS and Linux, `cmd` on Windows. For portable tests, use commands that work in both. Typos in assertions are caught before anything runs.

### skillbench.yaml

```yaml
model: claude-sonnet-5-5
timeout_s: 600
repeat: 1
min_pass_rate: 1.0
fixture: .
```

### How skillbench runs Claude Code

- Each turn runs `claude -p ... --output-format stream-json`, and later turns resume the same session. Every plugin found in the workspace is passed with `--plugin-dir`.
- **Isolation:** every run ignores your personal config, so a global skill can't make a test pass or fail. With `ANTHROPIC_API_KEY` set it uses `--bare`; otherwise it keeps your login but drops personal settings and MCP servers.
- The agent can't stop to ask questions mid-turn (`AskUserQuestion` is disabled); skillbench answers between turns instead.
- Raw output for every turn is in `skillbench/runs/<id>/raw/`.

---

## Developing skillbench

```
uv run ruff check .
uv run ruff format .
```
