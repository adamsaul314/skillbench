# Review: skillbench-core / slice-2
**Latest verdict: PASS (round 0)**

## Round 0 (2026-11-14, base 5fd988d)
## Verdict: PASS
(PASS only if every AC is met and there are no blocking findings.)

### Stage A: Acceptance criteria
| AC | Verdict | Evidence |
|----|---------|----------|
| AC2.1 | met | Each type has a passing and a failing case in the parametrized `test_ac2_1_each_assertion_type_passes_and_fails` (tests/test_assertions.py:558-598). `exit_ok` fails in `test_ac2_1_exit_ok_fails_when_the_cli_exits_non_zero` (tests/test_assertions.py:601). `max_turns` with no turn count is covered at tests/test_assertions.py:611. The `skill_invoked` slash-prompt cases are at tests/test_assertions.py:749 and :774. Impl: the checkers and the `_ASSERTION_TYPES` registry in src/skillbench/assertions.py:372-476, and `evaluate_assertions` at src/skillbench/assertions.py:497 |
| AC2.2 | met | `test_ac2_2_any_failing_assertion_fails_the_test_and_each_result_is_recorded` (tests/test_assertions.py:624) checks `status == "fail"` and the exact recorded type/expected/passed/reason, including "Write was called 2 times". `test_ac2_2_all_passing_assertions_pass_the_test` (tests/test_assertions.py:652). Impl: `_test_status` at src/skillbench/runner.py:223; the record's `assertions` at src/skillbench/runner.py:216; `AssertionResult.to_json` at src/skillbench/assertions.py:309 |
| AC2.3 | met | `test_ac2_3_no_assertions_and_completed_run_passes` (tests/test_assertions.py:661), `test_ac2_3_no_assertions_and_non_zero_exit_is_an_error` (:672), `test_ac2_3_no_assertions_and_timeout_is_an_error` (:682). Impl: src/skillbench/runner.py:223-227 |
| AC2.4 | met | `test_ac2_4_runs_every_yaml_under_the_directory_in_sorted_order` (tests/test_directories.py:820) and `test_ac2_4_prints_a_summary_table_with_status_duration_and_turns` (tests/test_directories.py:837). Impl: `_find_test_files` at src/skillbench/cli.py:61; `_format_summary` / `_summary_row` at src/skillbench/cli.py:78-94. See nit 1 for how strong the ordering test is. |
| AC2.5 | met | `test_ac2_5_exit_code_is_0_when_every_test_passes` (tests/test_directories.py:858) and `test_ac2_5_exit_code_is_1_when_any_test_does_not_pass` (:870). The error case is also covered by the AC2.3 tests. Impl: src/skillbench/cli.py:57-58 |
| AC2.6 | met | `test_ac2_6_invalid_assertion_is_a_validation_error_and_launches_nothing` (tests/test_assertions.py:708) and `test_ac2_6_an_invalid_test_anywhere_in_the_directory_stops_all_runs` (tests/test_directories.py:883). Both assert `not fake_claude.was_launched`. Impl: `parse_assertion` at src/skillbench/assertions.py:479; `_parse_assertions` at src/skillbench/config.py:139; the CLI loads every spec before running any at src/skillbench/cli.py:44 |
| Note: workspace-escaping paths | met | `test_file_paths_escaping_the_workspace_are_a_validation_error` (tests/test_assertions.py:735) covers `..`, nested `..`, POSIX absolute and Windows absolute paths. Impl: `_is_workspace_path` at src/skillbench/assertions.py:330 |

### Stage B: Standards
| # | Severity | Rule | Location | Finding | Suggested fix |
|---|----------|------|----------|---------|---------------|
| 1 | nit | S17 | tests/test_directories.py:820 | The sorted-order test only uses `a.yaml`, `b.yaml` and `nested/c.yaml`. On NTFS, `rglob` already returns these in alphabetical, top-down order, so this test would probably still pass on this Windows machine if the `sorted(...)` in `_find_test_files` were removed. | Pick names where filesystem order or default `Path` ordering would differ from the intended order. For example, have a nested file sort ahead of a top-level file (`a/z.yaml` vs `b.yaml`), or create the files in reverse order on a filesystem that keeps insertion order. |
| 2 | nit | S8 | src/skillbench/assertions.py:322 | `_is_positive_int` is a copy of `config._is_positive_int` (src/skillbench/config.py:49). | Optionally keep one copy in a shared private validators module. Leaving it is defensible, because sharing it would widen a module interface (S10). |

### Other
- None
