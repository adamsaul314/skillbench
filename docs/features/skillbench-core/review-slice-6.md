# Review: skillbench-core / slice-6
**Latest verdict: CHANGES REQUESTED (round 0)**

## Round 0 (2026-11-14, base 9d42000)
## Verdict: CHANGES REQUESTED
(PASS only if every AC is met and there are no blocking findings.)

The code is in good shape. The only reason this isn't a PASS is that the manual live runs for AC6.4 and AC6.5 haven't happened yet. Once the user runs them and they succeed, this slice should pass as is.

### Stage A: Acceptance criteria
| AC | Verdict | Evidence |
|----|---------|----------|
| AC6.1 | met | test `test_ac6_1_tiny_project_has_one_passing_test_in_a_git_workspace` (C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:39) copies the fixture with `create_workspace`, then checks that pytest reports "1 passed" and that `git status` succeeds. Impl: C:\Users\adams\Desktop\skillbench\fixtures\tiny-project\greet.py, C:\Users\adams\Desktop\skillbench\fixtures\tiny-project\tests\test_greet.py:4, C:\Users\adams\Desktop\skillbench\fixtures\tiny-project\pyproject.toml:6-8, C:\Users\adams\Desktop\skillbench\fixtures\tiny-project\.gitignore |
| AC6.2 | met | test `test_ac6_2_smoke_test_drives_understand_then_approve_then_build_slice_1` (C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:57) and `test_ac6_3_smoke_test_runs_end_to_end_on_a_fake_agent` (C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:83), which checks that every answer is sent (conversation turns = 1 + number of answers). Impl: C:\Users\adams\Desktop\skillbench\evals\smoke\overpowered-loop.yaml:6-16 |
| AC6.3 | met | YAML assertions: `test_ac6_3_smoke_test_asserts_the_approved_plan_the_build_and_a_passing_test_command` (C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:69). `command_succeeds` runs in the workspace, pass case: C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:116. Fail case reports exit code and output: :130. Rejects a bad value: :140. A slash command sent as a scripted answer counts as `skill_invoked`: :157 and :172. Impl: C:\Users\adams\Desktop\skillbench\src\skillbench\assertions.py:119-136 (`skill_invoked` reads user messages), :200-215 and :237-241 (`command_succeeds`); YAML at C:\Users\adams\Desktop\skillbench\evals\smoke\overpowered-loop.yaml:19-24 |
| AC6.4 | unclear | No automated test, and per the builder the manual live Claude run hasn't been done. Nobody has yet confirmed that the dashboard timeline shows the skill invocations with the reviewer subagent nested under its parent. Becomes met once the user's live run shows this. |
| AC6.5 | unclear | The YAML exists and loads, and it asserts its own skill was invoked: `test_ac6_5_skill_unit_tests_load_and_each_asserts_its_skill_was_invoked` (C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:186); impl C:\Users\adams\Desktop\skillbench\evals\skills\grilling.yaml. The "passes live" part hasn't been run yet. |

### Stage B: Standards
| # | Severity | Rule | Location | Finding | Suggested fix |
|---|----------|------|----------|---------|---------------|
| 1 | nit | S8 | C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:99-113 | `_make_fixture` and `_run_one_assertion` are near-copies of `_make_fixture` and `_run_assertions` in C:\Users\adams\Desktop\skillbench\tests\test_assertions.py:12-26 (same stream, fixture layout, prompt and YAML shape). The `PROMPT` constant is also repeated. | Move the `command_succeeds` and `skill_invoked` unit tests into test_assertions.py and reuse its helpers there (add an `answers` passthrough), or move the shared helper into conftest. |
| 2 | nit | S17 | C:\Users\adams\Desktop\skillbench\tests\test_smoke.py:172 | `test_ac6_3_skill_invoked_ignores_a_slash_command_that_is_not_the_message_start` only checks `passed is False`. It would still pass if the user-message path were removed entirely, so the only thing it catches is a regex-style "contains" match. That is fine, but the companion test at :157 is the one that actually guards the new path. | Optional: also assert the reason says "never invoked" so the failure path is pinned too. |

### Other
- (nit, not blocking) C:\Users\adams\Desktop\skillbench\src\skillbench\assertions.py:201: `subprocess.run` in `_check_command_succeeds` has no `timeout`. A command that hangs (for example a test that blocks) would hang the whole skillbench run after the agent finishes, and the per-turn `timeout_s` wouldn't help. Consider a bounded timeout that reports a clear "timed out" reason.
- (nit) C:\Users\adams\Desktop\skillbench\evals\smoke\overpowered-loop.yaml:24: `command_succeeds: python -m pytest -q` relies on whatever `python` is first on PATH having pytest installed. It passes on this machine according to the fake end-to-end test, but it may not on other machines. Worth a comment, or using `uv run pytest -q` if that's the project convention.
