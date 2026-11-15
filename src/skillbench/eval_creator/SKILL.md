---
name: eval-creator
description: Write skillbench test files (YAML) for Claude Code skills, plugins and multi-step agent workflows. Use when the user asks to create, write, add or set up skillbench tests or evals.
argument-hint: <what you want to test>
---

# eval-creator: write skillbench tests

The user describes what they want to test (in the arguments or the conversation). You write skillbench test files for it, check they are valid, and tell the user how to run them. **Never run the tests yourself**: every run is a real Claude session that costs the user money. Only run `skillbench skill` or `skillbench agent` if the user explicitly asks.

## Steps

1. **Find the project.** Look for a `skillbench/` folder with `skillbench.yaml` at or above the current directory. If there is none, run `skillbench init`, then delete the two sample tests (`skillbench/skills/example.yaml`, `skillbench/agent/example.yaml`) and the sample repo (`skillbench/fixtures/sample-repo/`) unless the user wants them.
2. **Read what is being tested.** Find the skills involved and read each `SKILL.md`: its `name`, its `description`, and whether its frontmatter has `disable-model-invocation: true` (then it can only be started by its `/command`, so Claude will never pick it by itself). Skills live in `.claude/skills/<name>/`, `skills/<name>/`, `.github/skills/<name>/`, or a plugin's `skills/<name>/`. A plugin is a folder with `.claude-plugin/plugin.json`; its skills are called `/<plugin>:<skill>`, and its subagents are in `agents/`.
3. **Load the plugin, if this repo is one.** If the repo root has `.claude-plugin/plugin.json` and any test you write uses a `fixture:` other than `.`, then `skillbench/skillbench.yaml` must contain `plugin: .`. Add it if it's missing. Without it, the agent in the other repo has none of this plugin's skills or subagents, and every `/<plugin>:...` command fails.
4. **Ask only what you can't find out.** Usually at most: which repo the agent should work on, and what a good result looks like. Don't ask about things you can read from the files.
5. **Propose the tests. Write nothing yet.** Show the user the proposal in the format below and stop. Ask the questions in it, if any.
6. **Wait for approval.** Only the user's explicit approval ("approve", "go", "yes") lets you write files. If they ask for changes, change the proposal and show it again. Don't write files to test an idea.
7. **Write the test files**, following the format below, exactly as approved. One test per file, named after what it checks (`grilling-asks-first.yaml`). Skill tests go in `skillbench/skills/`, agent tests in `skillbench/agent/`. If a file already exists, show the user what will change before you overwrite it.
8. **Validate.** Run `skillbench check`. It costs nothing. Fix every error and warning it reports and run it again until it is clean.
9. **Hand over.** List the files you wrote and what each checks, then give the exact command to run them, with the cheapest model for a first try, e.g. `skillbench skill grilling --model claude-haiku-5-5`. For a workflow whose answers you had to guess, suggest a first run with `interactive: true`.

## The proposal (step 5)

Keep it to one screen, in plain words. For each test:

- **Name and purpose:** the test's file name, and one plain sentence on what it checks.
- **What it runs:** the skill and input, or the prompt, and the fixture (which repo).
- **What it checks:** each check as a plain sentence ("Claude uses the grilling skill", "Claude doesn't write any files"), not as YAML.
- **Passes when:** for a repeated or trigger test, the pass rate. For a test whose verdict depends on wording, say which word or verdict counts.
- **Cost:** a rough estimate, using these bands: *cheap* (a single skill test, a few cents on Haiku), *moderate* (an agent test with a few replies), *expensive* (a full multi-step workflow, several dollars). Say it's an estimate.

End with the questions that matter, two or three at most, and the line: "Reply **approve**, or tell me what to change. Nothing is written until you approve."

Don't show YAML in the proposal. Show it only after approval, when writing the files.

## Choosing the kind of test

| The user wants to know | Write |
|---|---|
| "Does my skill do the right thing when it runs?" | A **skill test**: `skill:` + `input:` |
| "Does Claude pick my skill on its own for the right requests?" | A **trigger test**: `skill:` + `triggers:`. Not possible for skills with `disable-model-invocation: true` |
| "Does a normal request get handled well end to end?" | An **agent test**: `prompt:` |
| "Does my multi-step workflow work from start to finish?" | An **agent test** whose `answers:` script every step (see Workflows) |

## Where the agent works (`fixture:`)

Paths are relative to the repo root (the folder holding `skillbench/`). The agent always works on a fresh temp copy.

- left out: an empty git repo
- `.`: a copy of this repo (uncommitted work included, git-ignored files and `skillbench/` left out)
- `some/folder`: a copy of that folder
- `https://github.com/you/app.git`: a clone. Prefer `{git: <url>, ref: <tag or commit>}` so the starting point never moves.

If the repo being tested **is a plugin** and the tests should run on other projects, add `plugin: .` to `skillbench/skillbench.yaml`. Then the plugin (skills and subagents) is loaded into every test whatever the fixture is. Without it, only skills inside the fixture are loaded.

## The test format

A skill test:

```yaml
name: grilling-asks-first          # optional; defaults to the file name
description: Grilling asks questions before it writes anything.
skill: grilling                    # skill name, plugin:skill, or path to the skill folder
input: an idea for a todo app      # sent as "/<plugin>:grilling an idea for a todo app"
fixture: .
assert:
  - final_text_contains: "?"
  - tool_not_called: Write
```

A trigger test (each prompt is its own short run that stops at the first skill call):

```yaml
skill: grilling
triggers:
  should: ["stress-test my plan before I build it", "grill me on this design"]
  should_not: ["fix the typo in README.md", "what does this function return?"]
```

An agent test:

```yaml
prompt: add CSV export to the report page. Use the file name export.py.
fixture: .
answers:
  - Yes to all your recommendations, including any further questions.
timeout_s: 1800
assert:
  - skill_invoked: planner
  - command_succeeds: python -m pytest -q
```

Always write a `description:`: one plain sentence saying what the test checks, in the user's terms ("The full loop builds calcDamage and passes the final review."). The dashboard shows it under the test's name, so anyone can see what a test is for.

All fields: `name`, `description`, `skill`, `input`, `triggers` (with `should` / `should_not`), `prompt`, `fixture`, `answers`, `interactive` (true/false), `timeout_s` (seconds per turn, default 600), `repeat`, `min_pass_rate` (0 to 1), `assert`. A test has either `skill` or `prompt`, never both. `input` and `triggers` need `skill`. Any other field is an error.

`skillbench/skillbench.yaml` may set defaults: `plugin`, `model`, `timeout_s`, `repeat`, `min_pass_rate`, `fixture`.

## Checks (`assert:`)

| Check | Passes when |
|---|---|
| `skill_invoked: grilling` | Claude used the skill (bare names match plugin skills too) |
| `skill_not_invoked: grilling` | Claude did not use it |
| `subagent_invoked: reviewer` | Claude handed work to that subagent |
| `tool_order: [Read, Edit, Bash]` | Those tools ran in this order, anything in between. `[Bash]` = "Bash was used" |
| `tool_not_called: Write` | That tool never ran |
| `final_text_contains: "Done"` | Claude's last message contains the text |
| `file_contains: {path: docs/plan.md, text: "approved"}` | The file exists afterwards and contains the text. Path relative, inside the workspace |
| `command_succeeds: python -m pytest -q` | The command exits 0 in the workspace afterwards (runs in sh, or cmd on Windows) |

Prefer checks on **outcomes** (files written, tests passing, the right skill or subagent used) over exact wording or long tool sequences, which break easily. A skill that should only ask questions gets `tool_not_called: Write` and `tool_not_called: Edit`.

**Copy exact text for `file_contains`.** When the file is one a skill writes from a template (a plan, a review), open that template in the skill's folder and copy the text as it will appear, Markdown included: if the template has `- **approval:** awaiting-approval`, check for `"**approval:** approved"`, not `"approval: approved"`. Never guess a file's format.

**Decide what "passed" means, and say so.** A check for a review verdict passes either way if it only looks for the word "Verdict". Check for the verdict you expect (e.g. `PASS`) when the test should only pass on a clean result, and tell the user which you chose.

## Workflows (several steps)

`answers:` is the script of what the user would do after each of Claude's turns, written as they would type it in Claude Code:

- plain text: sent to Claude as the user's reply
- `/clear`: the next message starts a fresh session
- `'!<command>'`: skillbench runs the command in the workspace itself, e.g. `'!git add -A && git commit -m "plan"'`. Quote it in YAML. A failing command stops the run.
- `/<plugin>:<skill> args`: starts the next phase

```yaml
prompt: /myflow:plan add a save command. Use the slug save-game. One slice is enough.
fixture: {git: https://github.com/you/app.git, ref: v1.0}
answers:
  - Yes to all your recommended answers, including any further questions. Write the plan.
  - approved
  - '!git add -A && git commit -m "plan: save-game"'
  - /clear
  - /myflow:build save-game slice-1
timeout_s: 2400
assert:
  - file_contains: {path: docs/features/save-game/plan.md, text: "approved"}
  - subagent_invoked: reviewer
  - command_succeeds: npm test
```

Rules that make workflow tests reliable:
- **Fix every name the checks depend on in the prompt** (slugs, file names), so paths are predictable.
- **Make answers forgiving.** "Yes to all your recommended answers, including any further questions" survives an extra round of questions; "yes" does not.
- **Read the workflow's skills to learn its gates**: what it waits for ("approved"), whether it needs a clean git tree (add a `!git ... commit` step), and whether it wants a fresh context between phases (add `/clear`).
- **Keep the task small.** One small feature, one slice. Big tasks are slow, expensive and flaky.
- **Offer stage tests too.** A full loop is expensive. A test that starts from a later point (a `ref:` where the plan already exists, then straight to the build command) is cheaper and shows which stage broke.
