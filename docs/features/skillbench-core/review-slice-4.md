# Review: skillbench-core / slice-4
**Latest verdict: PASS (round 0)**

## Round 0 (2026-11-14, base d2ab2c6)
## Verdict: PASS
(PASS only if every AC is met and there are no blocking findings.)

### Stage A: Acceptance criteria
| AC | Verdict | Evidence |
|----|---------|----------|
| AC4.1 | met | tests `test_ac4_1_every_run_regenerates_the_dashboard_with_all_runs` (tests/test_dashboard.py:156), `test_ac4_1_row_shows_the_runs_summary_fields` (tests/test_dashboard.py:165, checks every column: date, backend, model, status, duration, turns, cost and tokens), `test_ac4_1_runs_are_listed_newest_first_by_start_time` (tests/test_dashboard.py:181); impl src/skillbench/cli.py:59 (`write_dashboard` after each `run_test`), src/skillbench/dashboard.py:47-52 (scan and newest-first sort), dashboard.py:76-94 (row cells), dashboard.py:113-122 (cost/usage) |
| AC4.2 | met | tests `test_ac4_2_selecting_a_run_shows_its_full_timeline` (tests/test_dashboard.py:196, covers assistant text, user answer order, tool input/output, assertion reasons), `test_ac4_2_tool_calls_are_expandable_and_subagent_calls_nest_inside_their_parent` (tests/test_dashboard.py:221), `test_ac4_2_each_run_selects_its_own_detail` (tests/test_dashboard.py:240); impl src/skillbench/dashboard.py:89, 141-145 (`:target` detail sections), src/skillbench/report.py:61-68 (`render_run_detail`), report.py:93-106 (assertions with reasons) |
| AC4.3 | met | test `test_ac4_3_dashboard_embeds_all_data_and_loads_nothing_from_the_network` (tests/test_dashboard.py:253) using `assert_loads_nothing_from_the_network` (tests/conftest.py:120); impl src/skillbench/dashboard.py:148-161 (one static page with no scripts and inline CSS) |
| AC4.4 | met | tests `test_ac4_4_missing_or_corrupt_run_json_is_a_broken_item_and_the_rest_still_render` (tests/test_dashboard.py:266, covers missing, invalid JSON and wrong shape), `test_ac4_4_broken_item_shows_why_it_is_broken` (tests/test_dashboard.py:288); impl src/skillbench/dashboard.py:55-73, 90-93, 97-110 |
| AC4.5 | met | tests `test_ac4_5_dashboard_command_regenerates_without_running_tests` (tests/test_dashboard.py:304, checks the backend invocation count does not change), `test_ac4_5_dashboard_command_with_no_runs_writes_an_empty_dashboard` (tests/test_dashboard.py:317); impl src/skillbench/cli.py:34-47 |
| AC4.6 | met | test `test_ac4_6_large_tool_output_is_truncated_with_the_full_text_on_expand` (tests/test_dashboard.py:351, 1.1 MB output: preview is visible, full text sits inside a collapsed `<details>`, and later content still renders); impl src/skillbench/report.py:132-133, 189-196 (`_render_long_text`, `_PREVIEW_CHARS`) |

### Stage B: Standards
| # | Severity | Rule | Location | Finding | Suggested fix |
|---|----------|------|----------|---------|---------------|
| 1 | nit | S9 | tests/test_dashboard.py:37, 41, 53 | `_RunsTableParser._in_header` is set but never read. | Remove the attribute. |
| 2 | nit | S17 | tests/test_dashboard.py:193 | `rows[0]["date"].startswith("2026-01-03")` depends on the machine's timezone. The dashboard shows `09:00+00:00` in local time, so on a machine at UTC-10 or further west it shows as 2026-01-02 and the test fails. | Use start times with no offset or at midday, or check only the order. |
| 3 | nit | S17 | tests/test_dashboard.py:66-100 | `_ElementTextParser` decreases depth on end tags only, so a void element inside the target section (such as `<br>` or `<img>`) would break its depth tracking. The current markup has no void elements, so the tests are correct today, but the helper is fragile. | Skip depth tracking for HTML void elements. |
| 4 | nit | S12 | src/skillbench/dashboard.py:90 | Catching `TypeError`/`AttributeError` around `render_run_detail` also turns a bug in the renderer into a "broken run" row. The repr is shown, so the error is not lost, but a code bug looks like bad data. | Optionally limit the try to the record field access, or keep it and accept the tradeoff. |

### Other
- None
