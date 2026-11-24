# bugfix-agent

[עברית](README.md) · English

Point it at a project with failing tests. It finds the root cause, fixes it with
the smallest change it can, proves the fix by running the full test suite, and
shows you the exact change before anything touches your files.

```text
$ bugfix fix ./my-project
Fixing my-project with the raw agent (claude-sonnet-5-5)
Working on a private copy; your files are not touched yet.
Runner: python/pytest  |  2 failing, 14 passing
  > running tests
  > reading src/pricing.py
  thinking: The discount is applied before the tax, but the test expects ...
  > editing src/pricing.py
  > running tests

## Result
Root cause: ...

6 steps, 41s, about $0.0312
Fixed: all 16 tests pass on the agent's copy.

Proposed change:
--- a/src/pricing.py
+++ b/src/pricing.py
...
Apply this change to your project? [y/N]
```

The same agent is built **twice**, on one shared foundation, so the two can be
compared line by line:

- `raw_agent/` talks to the Messages API over plain HTTP, with a hand-written loop.
- `sdk_agent/` hands the loop to the Claude Agent SDK.

The comparison is in [COMPARISON.md](COMPARISON.md) (Hebrew:
[COMPARISON.he.md](COMPARISON.he.md)). A usage guide in Hebrew is in
[USAGE.he.md](USAGE.he.md).

## Quickstart

You need Python 3.11 or later and an Anthropic API key. On Windows, if `python`
only prints "Python", use `py` instead (`py -m pip install -e .`), and if the
`bugfix` command is not found, use `py -m cli` in its place.

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...      # Windows: setx ANTHROPIC_API_KEY "sk-ant-..."

bugfix check ./my-project                # what is failing? (no key needed)
bugfix fix ./my-project                  # find the bug, show the fix, ask before applying
```

That is the whole workflow. Prefer a window to a terminal? `bugfix ui` opens the
same agent in your browser, in Hebrew: pick a sample bug, upload a project folder or
type its path, watch each step live, compare both agents side by side, then download
the fixed project or apply the fix with one click.

Everything else is optional:

| Command | What it does |
|---|---|
| `bugfix check [PATH]` | Detects the language and test runner, runs the tests, reports what fails. Add `-v` to see the test output. |
| `bugfix fix [PATH]` | Runs the agent on a private copy, then shows the change and asks before applying it. |
| `bugfix fix --dry-run` | Shows the fix and never applies it. |
| `bugfix fix --yes` | Applies the fix without asking (for scripts and CI). |
| `bugfix fix --engine sdk` | Uses the Claude Agent SDK version instead of the raw one. Same tools, same result format. |
| `bugfix fix --max-cost 0.50` | Stops the run if it would cost more than $0.50. |
| `bugfix fix --model claude-opus-5-5` | Uses another model. |
| `bugfix fix --fallback groq:<model>` | Adds a backup model, used if the main one fails. Repeat for more. |
| `bugfix fix --model ollama:<model>` | Runs on a free model only. |
| `bugfix fix --trace run.jsonl` | Writes every model call and tool call to a file. |
| `bugfix fix -v` | Shows the agent's full notes instead of a one-line summary. |
| `bugfix ui` | Opens the agent in your browser (built with Streamlit). |
| `bugfix benchmark` | Runs both agents on the 13 built-in bugs and writes a comparison table. |

`PATH` defaults to the current directory.

## Supported languages

The agent itself is language-independent. Only running the tests and reading
their results depend on the language, and those are detected automatically:

| Language | Detected by | Runs |
|---|---|---|
| Python | `pyproject.toml`, `setup.py`, `pytest.ini`, or any `*.py` | `pytest` |
| JavaScript / TypeScript | `package.json` | `npm test`, or `node --test` when there is no test script |
| Go | `go.mod` | `go test ./...` |
| Rust | `Cargo.toml` | `cargo test` |
| Java / Kotlin | `pom.xml`, `build.gradle(.kts)` | `mvn test`, `gradle test` |

For anything else (Ruby, PHP, C#, ...), add a `bugfix.yaml` to the project
with the command that runs your tests:

```yaml
test_command: "bundle exec rspec"
language: ruby          # optional, shown in reports
```

## Free models and backup models

The raw agent can run on free models, and can fall back to them when Claude
fails: no credit left, a spend cap, an invalid key, or an outage that outlasts
the retries. The run then continues on the next model from the same point,
instead of starting over.

```bash
bugfix fix --fallback groq:<model>                       # Claude first, then Groq
bugfix fix --fallback groq:<model> --fallback ollama:<model>
bugfix fix --model ollama:<model>                        # a local model only
```

A free model is written `<host>:<model>`. The model must support tool calling.

| Host | What it is | Key |
|---|---|---|
| `ollama` | Runs on your own computer, fully free and offline. Install from ollama.com, then `ollama pull <model>`. | none |
| `groq` | Hosted, with a free tier. | `GROQ_API_KEY` |
| `openrouter` | Hosted; models whose name ends in `:free` cost nothing. | `OPENROUTER_API_KEY` |
| `gemini` | Google's models, with a free tier. | `GEMINI_API_KEY` |

Free models are usually weaker than Claude at finding bugs, so they make the
most sense as a backup. Their tokens count toward the token cap and cost $0.
This works with the raw agent only: the Claude Agent SDK runs on Claude alone,
which is one of the differences [COMPARISON.md](COMPARISON.md) measures.

## What it will and will not do

The agent can only list, read and search files, edit files, and run the test
suite. It has no shell, no web access and no access to anything outside its
copy of your project. On top of that:

- **Your files are safe until you say yes.** The agent works on a temporary
  copy. You see the exact diff first. If a file changed while the agent was
  working, the change is refused rather than overwriting your edit.
- **Tests are read-only.** It cannot edit test files or test configuration
  (`tests/`, `test_*.py`, `*_test.go`, `*.spec.js`, `conftest.py`,
  `package.json`, `Cargo.toml`, ...). Any change to them, by any route, is
  detected by hash and the run is rejected.
- **It does not grade itself.** After the agent stops, the full suite is run
  again independently. Only that run decides whether the bug is fixed.
- **It has a budget.** Every run is capped at 15 steps, 1.5M tokens and $2 by
  default. Change these with `--max-cost` or the settings below.
- **No secrets leak into your tests.** Test commands run with a minimal
  environment; your API key and other variables are not passed to them.

Known limitation: tests that live inside source files (Rust's
`#[cfg(test)] mod tests`) cannot be protected by file name. The grader's
independent run still decides the result.

## Settings

Every setting has a default and can be changed with an environment variable
that starts with `BUGFIX_`:

| Variable | Default |
|---|---|
| `BUGFIX_MODEL` | `claude-sonnet-5-5` |
| `BUGFIX_FALLBACK_MODELS` | none; comma-separated, e.g. `groq:<model>,ollama:<model>` |
| `BUGFIX_EFFORT` | `medium` |
| `BUGFIX_MAX_ITERATIONS` | `15` |
| `BUGFIX_MAX_COST_USD` | `2.0` |
| `BUGFIX_MAX_TOTAL_TOKENS` | `1500000` |
| `BUGFIX_TEST_TIMEOUT_S` | `120` |

The full list is in `core/config.py`.

## How it is built

```text
cli/          the `bugfix` command
ui/           the browser interface (`bugfix ui`): logic.py does the work, app.py draws it, texts.py holds the Hebrew
core/         everything both agents share
  tools.py        the five tools: run_tests, list_files, read_file, search_code, edit_file
  sandbox.py      the private copy, path containment, timeouts, clean environment
  guards.py       read-only tests, tamper detection, budgets
  languages.py    test runners and result parsers per language
  evaluation.py   independent grading
  harness.py      the run around the loop: baseline, snapshot, solve, grade, patch
  prompts.py      one system prompt, used verbatim by both agents
raw_agent/    the agent without an SDK
  client.py       HTTP, auth, error types, retries with backoff
  providers/      the model layer: Anthropic, free models (OpenAI-compatible), fallback chain
  loop.py         the agent loop
sdk_agent/    the agent on the Claude Agent SDK
  agent.py        our tools as in-process MCP tools, built-ins off, message handling
benchmark/    13 bugs in Python, JavaScript, Go and Rust, and the comparison runner
tests/        216 tests, none of which need an API key
```

Both agents plug into the same `run_case()` in `core/harness.py`. The only thing
that differs between them is the loop that talks to the model, which is what
makes the comparison fair.

## The benchmark

```bash
bugfix benchmark                         # both agents, all 13 cases, 3 runs each
bugfix benchmark --engine raw --cases 01 11 --repeats 1
```

It writes `benchmark/results/results.md`: resolve rate, steps, tokens, cost and
time per agent, results per case, and any run that touched tests or hit a
budget. Each case runs several times because model runs are not deterministic.

The cases range from an off-by-one to two cross-file bugs and a rounding trap
where the obvious fix breaks other tests. Each one is validated by the test
suite: it must fail as shipped, and its reference fix must make it pass.

## Learning comments

Lines that start with `# 📘` are explanations in Hebrew for studying the code,
tagged `[בלי SDK]` / `[עם SDK]` where the two implementations differ. They can
all be removed at once, leaving the code unchanged:

```bash
make strip-explanations
```

## Development

```bash
pip install -e ".[dev]"
make check          # ruff, mypy --strict and pytest
```

The tests use a scripted fake API for the raw agent and a scripted message
stream for the SDK agent, so they run offline and cost nothing.
