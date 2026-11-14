# skillbench

Test your agent skills the way you test code.

You write a small YAML file that says "run this skill with this prompt, and check these things afterwards". skillbench runs it through the real agent CLI (**Claude Code** or **GitHub Copilot CLI**), records everything the agent did, and tells you pass or fail.

```
$ skillbench run evals/
test                     backend  status  duration  turns
grilling-asks-questions  claude   pass    20.0s     2
dashboard: runs/index.html
```

---

## How it works

1. **Fresh workspace.** For each test, skillbench makes a new temp folder, copies your optional `fixture` project into it, and runs `git init` there. Your real files are never touched. The folder is kept afterwards so you can look inside.
2. **Real agent, headless.** It launches the agent CLI you already have installed (`claude -p ...` or `copilot -p ...`), with your skill loaded and full tool access inside the workspace. There are no SDKs and no mocks, so **every run uses your real quota**.
3. **Conversation.** When the agent finishes a turn, skillbench sends your next scripted answer (`answers:`). With `interactive: true` it asks you in the terminal instead.
4. **Everything is recorded:** each turn, the assistant text, every tool call with its input and output, subagents nested under the call that started them, tokens, cost (Claude) or premium requests (Copilot), duration, and which skills were loaded.
5. **Checks.** Your `assert:` list is checked against the recording and the workspace. A test with no assertions passes if the run finished cleanly.
6. **Dashboard.** Results go to `runs/<run-id>/` and a single `runs/index.html` that you open straight from disk. It lists every run, and you click one to see its full timeline.

---

## 1. Install

You need **Python 3.11+**, **git**, and **[uv](https://docs.astral.sh/uv/)**, plus at least one agent CLI that is installed and logged in:

| Agent | Check it works |
|---|---|
| Claude Code | `claude --version` |
| GitHub Copilot CLI | `copilot --version` |

Install skillbench as a command from your copy of this repo:

```
uv tool install /path/to/skillbench
skillbench --help
```

(Without uv: `pip install /path/to/skillbench`.)

---

## 2. Set up your project

In the project where your skill lives (or any folder you like), make this layout:

```
my-project/
  evals/
    my-skill.yaml        <- your tests
  fixtures/
    sample-repo/         <- optional: files the agent starts with
```

Your skill can be either:
- **a plugin**: a folder with `.claude-plugin/plugin.json`, or
- **a plain skill folder**: a folder with a `SKILL.md`.

skillbench works out which one it is and loads it the right way for each agent.

---

## 3. Write a test

`evals/my-skill.yaml`:

```yaml
name: my-skill-asks-questions
skill_dir: ../my-plugin            # your plugin or skill folder (relative to this file)
prompt: /my-plugin:my-skill an idea for a todo app
assert:
  - skill_invoked: my-plugin:my-skill
  - final_text_contains: "Q1"
  - tool_not_called: Write
  - exit_ok: true
```

Paths (`skill_dir`, `fixture`) are relative to the YAML file.

### All the options

| Key | What it does | Default |
|---|---|---|
| `prompt` | **Required.** The first message, usually `/skill args` | |
| `name` | Name shown in results | file name |
| `skill_dir` | Plugin or skill folder to load | none |
| `fixture` | Folder copied into the workspace before the run | empty workspace |
| `answers` | Scripted replies, sent one per turn | `[]` |
| `interactive` | Ask you in the terminal once scripted answers run out | `false` |
| `max_turns` | Most conversation turns allowed | no limit |
| `timeout_s` | Seconds before a turn is killed | `600` |
| `model` | Model to use | the agent's default |
| `backends` | Limit to `[claude]`, `[copilot]` or both | both |
| `allowed_tools` / `denied_tools` | Tool allow and deny lists | `[]` |
| `isolation` | `clean` ignores your personal agent config; `none` uses it | `clean` |
| `assert` | List of checks (below) | `[]` |

### Assertions

| Assertion | Passes when |
|---|---|
| `skill_invoked: plugin:skill` | The skill ran, through the Skill tool or a `/skill` message |
| `tool_called: Bash` | The tool was called at least once (subagents included) |
| `tool_not_called: Write` | The tool was never called |
| `file_exists: docs/plan.md` | The file exists in the workspace afterwards |
| `file_contains: {path: docs/plan.md, text: "approved"}` | The file contains the text |
| `final_text_contains: "Q1"` | The agent's last message contains the text |
| `max_turns: 5` | The conversation took 5 turns or fewer |
| `exit_ok: true` | The agent CLI exited cleanly and didn't time out |
| `command_succeeds: python -m pytest -q` | The shell command exits 0 when run in the workspace |

File paths must stay inside the workspace. A typo in an assertion is caught before anything runs.

---

## 4. Run it

```
skillbench run evals/my-skill.yaml       # one test
skillbench run evals/                    # every *.yaml under evals/, in order
skillbench run evals/ --model claude-sonnet-5-5
skillbench run evals/ --backend claude   # or --backend copilot
skillbench dashboard                     # rebuild runs/index.html only
```

Run it from the folder where you want the `runs/` folder to go. Add `runs/` to your `.gitignore`.

The exit code is `0` only if every test passed.

**Which agent runs?** skillbench finds the installed CLIs. If only one is installed, it uses that one. If both are, it asks once whether to run on both (it won't ask when input isn't a terminal). Use `--backend` to choose yourself.

---

## Using Claude Code

Skill names are prefixed with the plugin name:

```yaml
skill_dir: ../my-plugin
prompt: /my-plugin:my-skill some arguments
backends: [claude]
assert:
  - skill_invoked: my-plugin:my-skill
```

- Plugins are loaded with `--plugin-dir`, plain skill folders with `--add-dir`.
- With `isolation: clean`, skillbench uses your login but not your personal settings or MCP servers. If `ANTHROPIC_API_KEY` is set, it uses `--bare`.
- The dashboard shows cost in USD.

## Using GitHub Copilot CLI

Copilot uses the **bare** skill name, with no plugin prefix. If you test both agents, write a separate YAML for each:

```yaml
skill_dir: ../my-plugin
prompt: /my-skill some arguments
backends: [copilot]
assert:
  - skill_invoked: my-skill
```

- Plugins are loaded with `--plugin-dir`. Plain skill folders are copied into the workspace's `.claude/skills/`.
- skillbench runs Copilot with `--allow-all` and `--no-ask-user`. With `isolation: clean` it also passes `--no-custom-instructions`.
- The dashboard shows premium requests instead of USD. Copilot can only be capped by `timeout_s`.
- **Heads-up:** Copilot support was built from its documented event format and hasn't been checked against a real run yet. If a trace looks wrong, the raw output is in `runs/<id>/raw/`.

---

## Multi-turn tests

Many skills ask questions. Script the replies, one per turn:

```yaml
prompt: /my-plugin:planner add CSV export
answers:
  - "Yes to all your recommendations."
  - approved
max_turns: 6
```

Or answer live: set `interactive: true`. You'll see the agent's last message and type your reply. An empty reply or `/done` ends the conversation. Afterwards skillbench offers to save your live answers into the YAML so the test can run unattended next time.

---

## Example: a full end-to-end test

`evals/smoke/overpowered-loop.yaml` in this repo runs a whole workflow (plan, approve, build) on `fixtures/tiny-project`, then checks the result:

```yaml
fixture: ../../fixtures/tiny-project
prompt: /overpowered:understand add an optional name parameter to greet() ... Use the slug greet-name.
answers:
  - Yes to all of your recommended answers ... write the plan now.
  - approved
  - 'Commit everything now with: git add -A && git commit -m "plan: greet-name"'
  - /overpowered:build greet-name slice-1
timeout_s: 2400
assert:
  - file_contains: {path: docs/features/greet-name/plan.md, text: "approval: approved"}
  - skill_invoked: overpowered:build
  - command_succeeds: python -m pytest -q
```

Tips for tests like this:
- **Put the names you assert on in the prompt** (like the slug above) so the paths are predictable.
- **Make answers forgiving.** The agent may ask more rounds of questions than you expect, so "Yes to all your recommendations, including any further questions" holds up better than "yes".
- **`command_succeeds` runs your shell's `python`.** Make sure that one has your test tools installed.
- Long runs cost real money. Start with one-turn unit tests of single skills.

---

## Reading the results

Open `runs/index.html` in a browser. It needs no server and no network.

- Each run is one row: date, test, agent, model, status, duration, turns, cost.
- Click a row to see the timeline: your messages, the agent's text, every tool call (click to expand its input and output), subagents nested under their parent, and each assertion with the reason it passed or failed.
- The workspace path is listed too, so you can open the folder and see exactly what the agent left behind.

| Status | Meaning |
|---|---|
| `pass` | Run finished and every assertion passed |
| `fail` | Run finished but an assertion failed |
| `error` | The agent crashed or timed out, or the conversation couldn't continue |

---

## Developing skillbench

```
uv run pytest            # its own tests use fake claude/copilot scripts, so they're free
uv run ruff check .
uv run ruff format .
```
