# Review: skillbench-core / slice-5
**Latest verdict: PASS (round 0)**

## Round 0 (2026-11-14, base bef9513)
## Verdict: PASS
(PASS only if every AC is met and there are no blocking findings.)

### Stage A: Acceptance criteria
| AC | Verdict | Evidence |
|----|---------|----------|
| AC5.1 | met | tests `test_ac5_1_launches_copilot_print_mode_with_json_output_and_full_access`, `test_ac5_1_isolation_none_keeps_custom_instructions`, `test_ac5_1_model_follows_yaml_then_command_line`, `test_ac5_1_plugin_skill_dir_is_passed_as_plugin_dir`, `test_ac5_1_plain_skill_dir_is_copied_into_the_workspace_skills`, `test_ac5_1_follow_up_turns_continue_one_session_by_id`, `test_ac5_1_denied_tools_are_passed_as_deny_tool` (C:\Users\adams\Desktop\skillbench\tests\test_copilot.py:24-108, 185); impl C:\Users\adams\Desktop\skillbench\src\skillbench\copilot_command.py:13-41, runner.py:657-659 (assigned session id), runner.py:76-86 |
| AC5.2 | met | tests `test_ac5_2_copilot_events_normalize_into_the_trace`, `test_ac5_2_usage_reports_premium_requests_and_tokens_not_usd`, `test_ac5_2_premium_requests_add_up_across_turns`, `test_ac5_2_assertions_work_unchanged_on_a_copilot_trace`, `test_ac5_2_dashboard_shows_premium_requests_instead_of_dollars`, `test_ac5_2_run_report_shows_premium_requests` (tests\test_copilot.py:97-197); odd-input tests in tests\test_copilot_trace.py:12-41; impl src\skillbench\copilot_trace.py:28-119, trace.py:746-791 and merge at trace.py:~125, dashboard.py:143-145, report.py:85 |
| AC5.3 | met | test `test_ac5_3_a_backend_that_fails_version_counts_as_not_installed` (tests\test_backends.py:28); impl src\skillbench\backends.py:85-100 (`find_installed` runs `--version`, non-zero / OSError / timeout means not installed) |
| AC5.4 | met | tests `test_ac5_4_the_only_installed_backend_is_used_and_the_other_reported_skipped`, `test_ac5_4_no_backend_installed_exits_non_zero_with_an_install_hint`, `test_ac5_4_an_explicit_backend_that_is_not_installed_is_an_error` (tests\test_backends.py:35-67); impl src\skillbench\cli.py:265-290, backends.py:103-106 |
| AC5.5 | met | tests `test_ac5_5_answering_yes_runs_each_test_on_both_backends`, `..._answering_no_runs_on_the_primary_only`, `..._asks_once_for_a_whole_directory`, `..._without_a_terminal_runs_the_primary_without_asking`, `..._an_explicit_backend_skips_the_question` (tests\test_backends.py:70-114); impl src\skillbench\cli.py:229, 265-276, 293-295, 333-339 |
| AC5.6 | met | tests `test_ac5_6_runs_of_one_multi_backend_invocation_share_an_eval_id` (tests\test_backends.py:117), `test_ac5_6_runs_of_one_eval_and_test_are_adjacent_and_labelled_by_backend` (tests\test_dashboard.py:194); impl src\skillbench\cli.py:234-238, 308-310, runner.py:667-668, dashboard.py:58-64, 106-113 |
| AC5.7 | met | test `test_ac5_7_a_test_limited_to_claude_is_skipped_on_copilot` (tests\test_backends.py:130); validation case in tests\test_validation.py:21; impl src\skillbench\cli.py:246-262, 372-374, config.py:403-406, 419-420 |

### Stage B: Standards
| # | Severity | Rule | Location | Finding | Suggested fix |
|---|----------|------|----------|---------|---------------|
| 1 | nit | S6 | src\skillbench\cli.py:279-290 | `_detect_backends` reads like a query but also prints the "skipping" lines to stderr. The docstring says so, but `_select_backends` hides it from the caller. | Return the missing names as well and print them in `_select_backends`/`_run_command`, or rename it to something like `_detect_and_report_backends`. |
| 2 | nit | S8 | src\skillbench\copilot_trace.py:1358-1360 vs src\skillbench\claude_trace.py:124-126 | Both builders have the same `_tool_calls_by_id` plus `_items_under` nesting logic. | Could move into a small shared helper in trace.py next to `new_tool_call`. It's fine to leave as is while only two copies exist. |
| 3 | nit | S15 | src\skillbench\copilot_command.py:1238 | The literal `"clean"` repeats the isolation mode names held in `config._ISOLATION_MODES`. | Expose a named constant, for example `ISOLATION_CLEAN`, and use it in both command builders. |
| 4 | nit | S5 | tests\conftest.py:884 | `_install_fake_agent(tmp_path, bin_dir, monkeypatch, name)` takes 4 parameters. | Derive `bin_dir` from `tmp_path`, or pass a small context object. |
| 5 | nit | S17 | tests\test_backends.py (AC5.3) | Only the "on PATH but `--version` fails" case is tested. The "not on PATH at all" branch (`shutil.which` returns None, backends.py:1187-1189) has no test. | Add a case that removes the fake executable from PATH. This is optional, since the real PATH may contain the real CLI. |

### Other
- Nit (robustness): copilot_trace.py:1350-1351 does `usage.get(...) + data.get("inputTokens", 0)`, which raises TypeError if an event carries `"inputTokens": null`. That contradicts the module's "never raises on odd input" promise. Coercing non-int values to 0 would fix it.
- Nit: copilot_trace.py:1327-1329 uses `str(data.get("toolCallId"))`, so every call missing an id gets the id `"None"` and later calls overwrite earlier ones in `_tool_calls_by_id`.
- Nit: copilot_command.py:1253 `copytree` raises FileExistsError if the fixture already contains `.claude/skills/<skill name>`. `dirs_exist_ok=True` or a clear error would handle it.
- Known gap, already noted in the plan: the Copilot event names and flags (`--session-id` on the first turn, `--plugin-dir`, discovery from `.claude/skills`) have not been checked against a real Copilot run.
- Process note: the given standards path C:\Users\adams\Desktop\overpowered\standards.md does not exist, and the repo has no copy either. I reviewed against the default at C:\Users\adams\Desktop\overpowered\skills\understand\standards.md instead.
