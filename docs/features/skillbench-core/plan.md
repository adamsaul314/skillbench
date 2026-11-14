# Plan: skillbench — eval framework for agent skills

- **slug:** skillbench-core
- **approval:** approved   <!-- awaiting-approval | approved. Only the user's explicit OK sets "approved". -->
- **feature_base:** b777502f64d0330f568b3d4214a387c58c0c639c
- **branch:** skillbench-core

## Goals
- `skillbench run <test.yaml | dir>` runs one or more YAML-defined skill tests by launching the user's installed agent CLI (Claude Code `claude` or GitHub Copilot `copilot`) headless as a black-box subprocess — no SDKs.
- Every run happens in a fresh temp workspace (copied from an optional fixture, `git init`ed) that is kept on disk afterwards; the agent gets full tool access inside it.
- Everything the agent does is captured: every turn, assistant text, each tool call with inputs and outputs, subagent activity (nested under the parent tool call), tokens/usage, cost (Claude) or premium requests (Copilot), duration, session id, and the loaded skills/plugins.
- Skill "unit tests": a test names a skill folder and a first prompt (`/skill args`), runs it, and passes/fails on optional built-in assertions. A test with no assertions is capture-only and passes if the run completes.
- Multi-turn: when the agent ends a turn awaiting input, skillbench uses the next scripted answer from the YAML, or else asks the user live in the terminal, and offers to save live answers back into the YAML.
- E2E smoke test: the same mechanism runs a full agent loop (overpowered `understand` → approve → `build slice-1`) on a fixture project and checks the end state.
- Each run writes its own data under `runs/<run-id>/`, and a single static, self-contained `runs/index.html` dashboard lists every run as one item and drills down into its full timeline.
- Model is selectable per test (`model:`) and per invocation (`--model`); unset means the backend's default.

## Non-goals
- LLM-as-judge grading.
- Record/replay or mocking of model calls (all runs are live and use the user's quota).
- Parallel test execution.
- A web server for the dashboard; CI integration.
- Backends other than Claude Code and Copilot CLI; using either vendor's SDK.
- An in-agent `/skillbench` command or plugin.
- Cost caps beyond what each backend supports natively (`--max-budget-usd` on Claude); Copilot is bounded only by timeout.
- A merged cross-backend comparison view (multi-backend runs appear as separate items sharing an eval id).

## Design decisions (settled in grilling)
- **Language/packaging:** Python 3.11, `pyproject.toml` with a `skillbench` console script, `uv` (pip fallback), PyYAML, pytest for skillbench's own tests. HTML rendered from Python templates; no JS build step.
- **Backend adapters:** one adapter per CLI that knows its flags and event format and normalizes events into one internal trace model.
  - Claude: `claude -p <prompt> --output-format stream-json --verbose`, `--resume <session_id>` for follow-up turns, `--plugin-dir`/`--add-dir` for skills, `--model`, `--max-turns`, `--permission-mode bypassPermissions`, `--allowedTools`/`--disallowedTools`, `AskUserQuestion` disallowed. Subagent events are linked via `parent_tool_use_id`.
  - Copilot: `copilot -p <prompt> --output-format json --allow-all`, `--session-id <uuid>` for follow-ups, skills copied into the workspace `.claude/skills/` or via `--plugin-dir`, `--model`, `--no-ask-user`, `--no-custom-instructions`. Event names are taken from the docs (`assistant.message`, `tool.execution_start/complete`, `subagent.*`, `assistant.usage`, `session.shutdown`) and are unverified until a real run.
- **Isolation (default "clean"):** uses the user's login but not their personal config. Claude: `--bare` when `ANTHROPIC_API_KEY` is set; otherwise `--setting-sources project` plus an empty strict MCP config, with the report listing the plugins actually loaded. `isolation: none` in YAML opts out.
- **Backend selection:** auto-detect installed CLIs; a missing backend is skipped with a notice; none installed → clear error, non-zero exit. When both are installed and no `--backend` is given, ask once before starting whether to run on both (optional, default = the detected primary). `backends:` in YAML restricts a test.
- **Turn-awaiting-input detection:** a turn ends (process exits / result event); if more scripted answers remain or the test declares `interactive: true`, the next turn is sent. Live mode shows the agent's final text and prompts; an empty answer or `/done` ends the conversation.
- **Run storage:** `runs/<run-id>/run.json` (summary, results, normalized trace), `runs/<run-id>/raw/<turn>.jsonl` (raw CLI stdout), and the workspace path. `runs/index.html` is regenerated after every run with all run data embedded (file:// can't fetch JSON).
- **Testing skillbench itself:** its own pytest suite uses fake `claude`/`copilot` executables (scripts emitting canned event streams) put on PATH, so its tests never call a real model. The smoke test (slice 6) is the only live run and is run manually.

### Test YAML shape (illustrative)
```yaml
name: grilling-asks-questions
skill_dir: ../overpowered          # plugin or skill folder made visible to the agent
fixture: fixtures/empty-repo       # optional; copied into the temp workspace
prompt: "/overpowered:grilling an idea for a todo app"
model: claude-sonnet-5-5           # optional; --model overrides
backends: [claude, copilot]        # optional
answers: ["yes to all"]            # optional scripted turns
interactive: false
max_turns: 30
timeout_s: 600
allowed_tools: []                  # optional
denied_tools: []                   # optional
isolation: clean                   # clean | none
assert:
  - skill_invoked: overpowered:grilling
  - tool_not_called: Write
  - final_text_contains: "Q1"
```

## Checks
<!-- build confirms these on its first run. -->
- **test:** `uv run pytest`
- **lint:** `uv run ruff check .` (to confirm in build)
- **typecheck:** none (to confirm in build)
- **format:** `uv run ruff format .`   **format-check:** `uv run ruff format --check .` (to confirm in build)

## Slices

### slice-1: single capture run on Claude
- **status:** todo
- **base:**
- **fix_rounds:** 0
- **Acceptance criteria:**
  - AC1.1 Given a valid test YAML and a `claude` on PATH, `skillbench run test.yaml` launches `claude -p` with the test prompt, stream-json output, bypass permissions, `AskUserQuestion` disallowed, and the skill dir passed via `--plugin-dir`/`--add-dir`; the process cwd is a new temp workspace.
  - AC1.2 Given `fixture:` is set, the temp workspace contains a copy of the fixture and is a git repo; the fixture source is unchanged after the run.
  - AC1.3 Given `model:` in YAML, `--model <it>` is passed; given `--model X` on the command line, X is passed instead; given neither, no `--model` flag is passed.
  - AC1.4 After the run, `runs/<run-id>/run.json` exists and contains: test name, backend, model, start/end time, duration, session id, exit status, workspace path, loaded plugins/skills, and a normalized trace listing assistant texts, tool calls (name, input, output, ok/error) in order, and usage/cost.
  - AC1.5 The raw CLI stdout is saved to `runs/<run-id>/raw/1.jsonl` byte-for-byte.
  - AC1.6 Tool calls emitted inside a subagent (non-null `parent_tool_use_id`) appear in the trace nested under the parent tool call.
  - AC1.7 `runs/<run-id>/report.html` is written, opens from disk without network access, and shows each assistant text and each tool call with its input and output.
  - AC1.8 Given an invalid YAML (missing `prompt`, unknown key, bad type), skillbench exits non-zero with a message naming the file and field, and launches nothing.
  - AC1.9 Given `timeout_s` elapses, the CLI process is killed, the run is recorded with status `timeout`, and partial trace data captured so far is kept.
  - AC1.10 Given `isolation: clean` (default) and `ANTHROPIC_API_KEY` set, `--bare` is passed; without the key, `--setting-sources project` and an empty strict MCP config are passed; given `isolation: none`, neither is passed.
- **Notes:** Fake `claude` script for tests. Unknown event types are kept in the raw log and listed in the trace as `unknown` rather than crashing.

### slice-2: assertions, pass/fail, directories
- **status:** todo
- **base:**
- **fix_rounds:** 0
- **Acceptance criteria:**
  - AC2.1 Each assertion type evaluates correctly against a trace/workspace, with one passing and one failing test each: `skill_invoked`, `tool_called`, `tool_not_called`, `file_exists`, `file_contains`, `final_text_contains`, `max_turns`, `exit_ok`.
  - AC2.2 A test with any failing assertion has status `fail`, and `run.json` records each assertion with pass/fail and a reason (e.g. "Write was called 2 times").
  - AC2.3 A test with no assertions has status `pass` if the run completed, `error` if the CLI exited non-zero or timed out.
  - AC2.4 `skillbench run <dir>` runs every `*.yaml` under it in sorted order, one run per test, and prints a summary table (test, status, duration, turns).
  - AC2.5 The exit code is 0 only if every test passed; otherwise it is 1.
  - AC2.6 An unknown assertion key is a validation error before any run starts (per AC1.8).
- **Notes:** `file_*` paths are relative to the workspace; paths escaping the workspace are a validation error.

### slice-3: multi-turn answers (scripted + live)
- **status:** todo
- **base:**
- **fix_rounds:** 0
- **Acceptance criteria:**
  - AC3.1 Given `answers: [a, b]`, after turn 1 skillbench sends `a` as turn 2 by resuming the same session (`--resume <session_id>`), then `b` as turn 3, then stops; raw logs are `raw/1.jsonl`..`raw/3.jsonl`, and the trace shows the turns in order with each user answer.
  - AC3.2 Given `interactive: true` and no scripted answers left, skillbench prints the agent's final text from the turn and reads an answer from the terminal; that answer is sent as the next turn.
  - AC3.3 In live mode, an empty answer or `/done` ends the conversation and the run proceeds to assertions.
  - AC3.4 After a live session, skillbench asks "save these answers to <file>?"; on yes the YAML's `answers:` list is updated with the live answers (other keys and comments preserved as far as practical); on no the file is unchanged.
  - AC3.5 Given stdin is not a TTY and a live answer would be needed, the run ends with status `error` and a message saying scripted answers are required, rather than hanging.
  - AC3.6 `max_turns` limits total conversation turns across answers; exceeding it stops the conversation and the `max_turns` assertion (if present) fails.

### slice-4: dashboard
- **status:** todo
- **base:**
- **fix_rounds:** 0
- **Acceptance criteria:**
  - AC4.1 After any run, `runs/index.html` is regenerated and lists every run found under `runs/` (newest first) with: date, test name, backend, model, status, duration, turns, cost/usage.
  - AC4.2 Selecting a run on the dashboard shows its full timeline: turns, user answers, assistant text, tool calls with expandable input/output, subagents nested under their parent, and assertion results with reasons.
  - AC4.3 The dashboard opens from disk with no network requests and no server; all data is embedded in the file.
  - AC4.4 A `runs/<id>/` with a missing or corrupt `run.json` is shown as a broken item (with the error) and does not stop the dashboard from rendering the rest.
  - AC4.5 `skillbench dashboard` regenerates `runs/index.html` without running any tests.
  - AC4.6 Large tool outputs are truncated in the view with the full text available on expand, so a run with a >1 MB tool output still renders.
- **Notes:** This may supersede the per-run `report.html` from slice-1 (keep it, or make it a link into the dashboard — decide in build).

### slice-5: Copilot backend + multi-backend prompt
- **status:** todo
- **base:**
- **fix_rounds:** 0
- **Acceptance criteria:**
  - AC5.1 Given `--backend copilot`, skillbench launches `copilot -p` with `--output-format json`, `--allow-all`, `--no-ask-user`, `--no-custom-instructions` (when isolation is clean), `--model` per AC1.3, and the skill made visible to it; follow-up turns use `--session-id <uuid>`.
  - AC5.2 Copilot events (`assistant.message`, `tool.execution_start/complete`, `subagent.*`, `assistant.usage`, `session.shutdown`) normalize into the same trace model, so assertions and the dashboard work unchanged; usage shows premium requests instead of USD.
  - AC5.3 With no `--backend`, skillbench detects which of `claude`/`copilot` are on PATH and actually runnable (a stub that errors on `--version` counts as not installed).
  - AC5.4 Given exactly one backend installed, it is used; the other is reported as skipped. Given none, exit non-zero with an install hint.
  - AC5.5 Given both installed and no `--backend`, skillbench asks once "run on both claude and copilot?"; yes → each test runs on both, no → runs on the primary only. Non-TTY → primary only, no prompt.
  - AC5.6 Runs from one multi-backend invocation share an `eval_id` in `run.json` and appear as separate, adjacent items on the dashboard labelled by backend.
  - AC5.7 Given `backends: [claude]`, the test is not run on copilot even if both are selected, and is reported as skipped for copilot.
- **Notes:** Copilot isn't installed on the dev machine; the adapter is built against the documented event format and a hand-written sample stream. "Verify with one real Copilot run" is a known gap until Copilot is installed.

### slice-6: overpowered smoke test
- **status:** todo
- **base:**
- **fix_rounds:** 0
- **Acceptance criteria:**
  - AC6.1 `fixtures/tiny-project/` exists: a minimal Python project with one passing pytest test, usable as a git repo.
  - AC6.2 `evals/smoke/overpowered-loop.yaml` runs `/overpowered:understand <small feature idea>` with scripted answers that accept the grilling, approve the plan, then send `/overpowered:build <slug> slice-1`.
  - AC6.3 The smoke test asserts `docs/features/<slug>/plan.md` exists and contains `approval: approved`, that `overpowered:build` was invoked, and that a test command in the workspace passes afterwards (new assertion `command_succeeds: <cmd>`, run in the workspace).
  - AC6.4 Running the smoke test live on Claude produces a dashboard item whose timeline shows the skill invocations and the reviewer subagent nested under its parent.
  - AC6.5 `evals/skills/` contains at least one unit-test YAML for a single overpowered skill (e.g. grilling) that passes live.
- **Notes:** AC6.4/6.5 are verified by a manual live run (uses real quota); the `command_succeeds` assertion gets fake-backend unit tests like the others.

## Log
- 2026-11-14 plan written, awaiting approval
- 2026-11-14 approved; feature_base pending (repo has no commits yet)
- 2026-11-14 approved at b777502f64d0330f568b3d4214a387c58c0c639c
