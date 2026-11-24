# Benchmark results

Model `claude-haiku-5-5`, effort `medium`. A run counts as resolved only if the grader's own test run passes and no test or test-configuration file changed.

## Summary

| Agent | Resolved | Mean iterations | Mean input tokens | Mean output tokens | Mean cost (USD) | Mean time (s) | Tampered | Budget hit | Errors |
|---|---|---|---|---|---|---|---|---|---|
| raw | 39/39 (100%) | 4.9 | 13,456 | 1,063 | 0.0009 | 6.5 | 0 | 0 | 0 |
| sdk | 39/39 (100%) | 5.1 | 15,848 | 1,119 | 0.0011 | 7.9 | 0 | 0 | 0 |

## Per case

| Case | Difficulty | raw | sdk |
|---|---|---|---|
| 01_off_by_one | easy | 3/3 | 3/3 |
| 02_inverted_condition | easy | 3/3 | 3/3 |
| 03_mutable_default | medium | 3/3 | 3/3 |
| 04_empty_input | easy | 3/3 | 3/3 |
| 05_type_coercion | medium | 3/3 | 3/3 |
| 06_operator_precedence | easy | 3/3 | 3/3 |
| 07_stale_state | medium | 3/3 | 3/3 |
| 08_cross_file_week_start | hard | 3/3 | 3/3 |
| 09_cross_file_bulk_tiers | hard | 3/3 | 3/3 |
| 10_rounding_trap | hard | 3/3 | 3/3 |
| 11_js_leaderboard_sort | easy | 3/3 | 3/3 |
| 12_go_pagination | medium | 3/3 | 3/3 |
| 13_rust_unicode_truncate | hard | 3/3 | 3/3 |

## Lines of code (excluding comments and docstrings)

| Implementation | Layer | Lines |
|---|---|---|
| raw_agent | HTTP client, errors, retries | 181 |
| raw_agent | Provider interface | 50 |
| raw_agent | Wire format (Anthropic) | 121 |
| raw_agent | Agent loop | 86 |
| raw_agent | Free models and fallback (no SDK equivalent) | 252 |
| sdk_agent | SDK wiring and message handling | 149 |
| shared (core) | Tools, sandbox, guards, grading, languages | 904 |
