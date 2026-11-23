# bugfix-agent

An autonomous agent that takes a Python repository with failing tests, finds the
root cause, applies a minimal fix and iterates until the full suite passes. It is
built twice on one shared foundation, so the two can be compared fairly:

- `raw_agent/`: the Anthropic Messages API over raw HTTP, with a hand-written loop.
- `sdk_agent/`: the Claude Agent SDK.

> Status: phase 1 of 4 (shared foundation and benchmark) is complete. The two
> agents, the benchmark run and the full write-up follow.

## What is here so far

| Path | Purpose |
|---|---|
| `core/tools.py` | The five tools both agents use: `run_tests`, `list_files`, `read_file`, `search_code`, `edit_file` |
| `core/sandbox.py` | A disposable copy of each case, path containment, timeouts, no secrets in child processes |
| `core/guards.py` | Test files are read-only, tampering is detected by hash, and iteration/token/cost budgets are enforced |
| `core/evaluation.py` | Independent grading: the grader re-runs the suite and never trusts the agent's claim |
| `core/prompts.py` | One system prompt shared verbatim by both agents |
| `core/telemetry.py` | JSONL trace of every model and tool call |
| `core/config.py` | Every tunable in one place, overridable with `BUGFIX_*` environment variables |
| `benchmark/cases/` | 10 bug cases, from easy to hard, including two cross-file bugs and one rounding trap |

## Run the checks

```bash
pip install -e ".[dev]"
make check
```

The test suite needs no API key. It also validates the benchmark itself: every
case must fail as shipped, and its reference fix must make it pass without
touching a protected file.
