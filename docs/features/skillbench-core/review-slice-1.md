# Review: skillbench-core / slice-1
**Latest verdict: PASS (round 0)**

## Round 0 (2026-11-14, base 0e814c0)
## Verdict: PASS
(PASS only if every AC is met and there are no blocking findings.)

### Stage A: Acceptance criteria
| AC | Verdict | Evidence |
|----|---------|----------|
| AC1.1 | met | tests `test_ac1_1_launches_claude_print_mode_with_prompt_and_stream_json` (tests/test_launch.py:29), `test_ac1_1_disallows_ask_user_question_alongside_denied_tools` (:41), `test_ac1_1_passes_allowed_tools_when_given` (:53), `test_ac1_1_plugin_skill_dir_is_passed_as_plugin_dir` (:63), `test_ac1_1_plain_skill_dir_is_passed_as_add_dir` (:76), `test_ac1_1_process_runs_in_a_new_temp_workspace` (:91), `test_ac1_1_each_run_gets_its_own_workspace` (:104); impl src/skillbench/claude_command.py:13-35, :53-58; src/skillbench/workspace.py:11; src/skillbench/runner.py:57-60 |
| AC1.2 | met | test `test_ac1_2_workspace_is_git_repo_with_fixture_copy_and_fixture_untouched` (tests/test_launch.py:114); impl src/skillbench/workspace.py:11-17 |
| AC1.3 | met | tests `test_ac1_3_yaml_model_is_passed` (tests/test_launch.py:134), `test_ac1_3_command_line_model_overrides_yaml_model` (:145), `test_ac1_3_no_model_anywhere_passes_no_model_flag` (:157); impl src/skillbench/runner.py:52, src/skillbench/claude_command.py:31-32, src/skillbench/cli.py:21 |
| AC1.4 | met | tests `test_ac1_4_run_json_records_run_metadata` (tests/test_capture.py:18), `test_ac1_4_trace_lists_texts_and_tool_calls_in_order` (:37), `test_ac1_4_trace_records_usage_and_cost` (:66), `test_ac1_4_nonzero_exit_is_recorded_as_error` (:79); impl src/skillbench/runner.py:49-81, src/skillbench/claude_trace.py:35-111 |
| AC1.5 | met | test `test_ac1_5_raw_stdout_is_saved_byte_for_byte` (tests/test_capture.py:90); impl src/skillbench/runner.py:58, :109-117 (stdout goes straight to the file) |
| AC1.6 | met | test `test_ac1_6_subagent_tool_calls_nest_under_parent_tool_call` (tests/test_capture.py:99); impl src/skillbench/claude_trace.py:109-111 (`_items_under`) |
| AC1.7 | met | tests `test_ac1_7_report_shows_assistant_texts_escaped` (tests/test_report.py:16), `test_ac1_7_report_shows_each_tool_call_with_input_and_output` (:24), `test_ac1_7_report_shows_subagent_tool_calls` (:34), `test_ac1_7_report_loads_nothing_from_the_network` (:42); impl src/skillbench/report.py:37-92, src/skillbench/runner.py:97-99 |
| AC1.8 | met | tests `test_ac1_8_invalid_yaml_names_file_and_field_and_launches_nothing` (tests/test_validation.py:23, covers missing prompt, unknown key, bad types, missing dirs), `test_ac1_8_unparseable_yaml_names_file_and_launches_nothing` (:38), `test_ac1_8_non_mapping_yaml_names_file_and_launches_nothing` (:49); impl src/skillbench/config.py:83-138, src/skillbench/cli.py:25-31 |
| AC1.9 | met | test `test_ac1_9_timeout_kills_process_and_keeps_partial_trace` (tests/test_capture.py:132); impl src/skillbench/runner.py:118-122, :132-146 |
| AC1.10 | met | tests `test_ac1_10_clean_isolation_with_api_key_uses_bare` (tests/test_launch.py:165), `test_ac1_10_clean_isolation_without_api_key_limits_settings_and_mcp` (:177), `test_ac1_10_isolation_none_passes_no_isolation_flags` (:189); impl src/skillbench/claude_command.py:38-50, src/skillbench/runner.py:53 |
| Note: unknown events | met | test `test_unrecognized_events_and_truncated_lines_are_listed_as_unknown` (tests/test_capture.py:147); impl src/skillbench/claude_trace.py:35-51, :125-139 |

### Stage B: Standards
| # | Severity | Rule | Location | Finding | Suggested fix |
|---|----------|------|----------|---------|---------------|
| 1 | nit | S5 | src/skillbench/runner.py:102 | `_run_process(command, cwd, stdout_path, timeout_s)` takes 4 parameters, one over the limit. | Group the launch inputs into a small frozen dataclass (for example `_Launch(command, cwd, timeout_s)`), or derive `cwd`/`timeout_s` from one object. |
| 2 | nit | S4 | src/skillbench/runner.py:49-81 | `run_test` mixes high-level orchestration (find, launch, parse, write) with inline low-level assembly of the record dict. | Move the record construction into a `_build_record(...)` helper. |
| 3 | nit | S8 | src/skillbench/cli.py:29, :33 | `Path.cwd() / _RUNS_DIR_NAME` is computed twice, and the `run_id / "report.html"` layout is repeated outside runner.py, which owns it (runner.py:97-99). | Compute `runs_root` once. Have the runner expose or return the report path so the layout lives in one place. |
| 4 | nit | S8 | tests/conftest.py:34, :39 | `FakeClaude.argv` and `FakeClaude.cwd` each re-read and re-parse the same record file. | Add one private `_invocation()` helper that both properties use. |
| 5 | nit | S13 | src/skillbench/config.py:77-78 | `prompt` (and `name`) accept the empty string, so an empty prompt is launched instead of being rejected at the boundary. | Use a non-empty-string validator for `prompt`. |

### Other
- (nit, risk to verify) The `parse_claude_stream` docstring (src/skillbench/claude_trace.py:125) says "Never raises on odd input", but structurally odd known events can still raise: `block["text"]` (:65), `block["id"]`/`block["name"]` (:75-76), and a non-dict `message` or non-dict content blocks (`.get` on a str/list) all give KeyError/AttributeError. Real claude output has these fields, so this isn't blocking. Either fix the docstring or send malformed blocks to `unknown`.
- (nit, Windows-specific risk) When `claude` resolves to an npm `claude.cmd` shim, `subprocess.Popen` runs it through cmd.exe. A prompt that contains `"` plus `&|<>^` or `%VAR%` can then be reinterpreted by cmd. The shipped tests use prompts with no metacharacters, so they wouldn't catch this. This isn't an issue if the installed `claude` is a native `.exe`. Worth confirming which one the target machine uses.
- (nit) On POSIX, `os.killpg` (src/skillbench/runner.py:145) can raise `ProcessLookupError` if the child exits between the timeout firing and the kill. It's a narrow race, but wrapping the call or checking `poll()` first would make it safe.
