# Review: skillbench-core / slice-3
**Latest verdict: PASS (round 0)**

## Round 0 (2026-11-14, base 25a3a2e)
## Verdict: PASS
(PASS only if every AC is met and there are no blocking findings.)

### Stage A: Acceptance criteria
| AC | Verdict | Evidence |
|----|---------|----------|
| AC3.1 | met | tests `test_ac3_1_scripted_answers_resume_the_session_as_turns_2_and_3` (tests/test_multi_turn.py:36), `test_ac3_1_each_turn_has_its_own_raw_log` (:51), `test_ac3_1_trace_shows_turns_in_order_with_each_user_answer` (:62), `test_ac3_1_report_shows_each_user_answer_between_turns` (tests/test_report.py:49); impl `_converse` / `_TurnLauncher.run_turn` in src/skillbench/runner.py (diff lines 331-392), `build_turn_args` src/skillbench/claude_command.py:13, `merge_turn_traces` src/skillbench/trace.py, report rendering src/skillbench/report.py (`user_message` branch) |
| AC3.2 | met | tests `test_ac3_2_interactive_prints_final_text_and_sends_the_typed_answer` (tests/test_multi_turn.py:118), `test_ac3_2_live_answers_start_only_after_scripted_answers_run_out` (:135); impl `AnswerSource.next_answer` src/skillbench/answers.py, `_ask_in_terminal` src/skillbench/cli.py |
| AC3.3 | met | tests `test_ac3_3_empty_answer_or_done_ends_the_conversation_and_assertions_run` (parametrized "" and "/done", tests/test_multi_turn.py:148), `test_ac3_3_end_of_input_ends_the_conversation` (:164); impl `_END_COMMANDS` check in src/skillbench/answers.py `next_answer` |
| AC3.4 | met | tests `test_ac3_4_yes_saves_scripted_and_live_answers_keeping_other_keys_and_comments` (tests/test_multi_turn.py:185), `test_ac3_4_yes_adds_an_answers_key_when_the_file_had_none` (:207), `test_ac3_4_no_leaves_the_file_unchanged` (:225), `test_ac3_4_no_save_question_without_live_answers` (:236); impl `_offer_to_save_answers` src/skillbench/cli.py, `save_answers` / `_with_answers_block` src/skillbench/answers_file.py |
| AC3.5 | met | tests `test_ac3_5_non_terminal_stdin_needing_a_live_answer_is_an_error` (parametrized, tests/test_multi_turn.py:246), `test_ac3_5_non_terminal_stdin_is_fine_when_no_live_answer_is_needed` (:262); impl `sys.stdin.isatty()` gate in src/skillbench/cli.py `_run_command`, `LiveAnswerUnavailableError` in src/skillbench/answers.py, `_StopReason.NEEDS_LIVE_ANSWER` + `_test_status` in src/skillbench/runner.py |
| AC3.6 | met | tests `test_ac3_6_max_turns_stops_the_conversation_and_fails_the_max_turns_assertion` (tests/test_multi_turn.py:272), `test_ac3_6_max_turns_counts_live_turns_too` (:289), `test_ac3_6_a_conversation_within_max_turns_runs_every_answer` (:300); impl `_stop_before_next_turn` in src/skillbench/runner.py, `exceeded_turn_limit` check in `_check_max_turns` src/skillbench/assertions.py |

### Stage B: Standards
| # | Severity | Rule | Location | Finding | Suggested fix |
|---|----------|------|----------|---------|---------------|
| 1 | nit | S15 / S2 | src/skillbench/cli.py (`_offer_to_save_answers`, `message["source"] == "live"`); src/skillbench/answers.py (`source="scripted"`, `source="live"`) | The answer-source strings are bare literals spread across two modules. A typo in one place would quietly turn off the save prompt. | Define the source values once, for example as constants or a StrEnum in src/skillbench/trace.py next to `UserMessage`, and use them in both modules. |
| 2 | nit | S4 | src/skillbench/cli.py `_offer_to_save_answers` | This one function decides whether a save is relevant, asks the user, parses the reply and performs the save. | You could split out a small `_confirm(question) -> bool` helper. This is optional. |
| 3 | nit | S16 | src/skillbench/report.py `_render_item` | There is a stray blank line after the new `user_message` branch, before the fallback `return`. It doesn't match the surrounding branches. | Remove the blank line. |

### Other
- (nit, not blocking) In src/skillbench/runner.py, `_stop_before_next_turn` checks `answers.has_more`, and for an interactive test that is always True until the user ends the conversation. So when an interactive test reaches `max_turns`, it is stopped with `MAX_TURNS` and `exceeded_turn_limit` is set, and any `max_turns` assertion fails. This happens even if the user was about to type `/done`, in which case the limit would never have been exceeded. AC3.6 says "exceeding it", so this may be a false failure for interactive tests. One option is to ask the user first and stop with `MAX_TURNS` only if they actually give another answer. `test_ac3_6_max_turns_counts_live_turns_too` currently pins the stricter behavior, so this needs a decision rather than a quick fix.
