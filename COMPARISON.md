# Raw API vs. Claude Agent SDK: the same agent, built twice

This document compares two implementations of one bug-fixing agent. Both share
the same tools, system prompt, model, budgets, sandbox and grader (`core/`). They
differ only in the orchestration layer: `raw_agent/` talks to the Messages API
over HTTP with a hand-written loop; `sdk_agent/` hands the loop to the Claude
Agent SDK. Any difference in behavior is therefore caused by that layer alone.

## At a glance

| | `raw_agent` (no SDK) | `sdk_agent` (Claude Agent SDK) |
|---|---|---|
| Code we own, excluding comments and docstrings | **438 lines, 4 files** (plus 252 for free models and fallback) | **149 lines, 1 file** |
| Agent loop | Written by hand (`while True` over stop reasons) | Provided (`query()` drives it) |
| HTTP, auth headers, API versioning | Written by hand | Provided |
| Retries and error taxonomy | Written by hand (backoff with jitter, `retry-after`, `x-should-retry`) | Provided |
| Message and tool-result wire format | Written by hand | Provided |
| Prompt caching | Explicit (`cache_control` on every request) | Handled by the SDK |
| Turn and cost caps | Our `Budget`, checked every iteration | SDK `max_turns` / `max_budget_usd`, plus our `Budget` |
| Model providers | Claude plus free models (Ollama, Groq, OpenRouter, Gemini) behind `ModelProvider` | Claude only |
| Backup when the model fails | Any chain, e.g. Claude then a free model, switching mid-run | Only Claude's own server-side refusal fallback |
| Process model | One Python process, synchronous | Python plus the bundled Claude Code CLI as a subprocess, async |
| Testing without an API key | HTTP-level: a scripted in-process API (`httpx.MockTransport`) | Stream-level: a scripted replacement for `query()` |
| Visibility | Every request and response is ours to log | Messages the SDK chooses to emit |

## Lines of code by layer

Measured by `benchmark/loc.py` (non-blank lines that are not comments or
docstrings), and regenerated into `benchmark/results/results.md` on every
benchmark run.

| Implementation | Layer | Lines |
|---|---|---|
| raw_agent | HTTP client, errors, retries (`client.py`) | 181 |
| raw_agent | Provider interface and neutral history (`providers/base.py`) | 50 |
| raw_agent | Wire format for Anthropic (`providers/anthropic.py`) | 121 |
| raw_agent | Agent loop (`loop.py`) | 86 |
| **raw_agent** | **Total for the Claude agent** | **438** |
| raw_agent | Free models and fallback (`providers/openai_compat.py`, `providers/fallback.py`) | 252 |
| sdk_agent | SDK wiring and message handling (`agent.py`) | 149 |
| shared | Tools, sandbox, guards, grading, languages, harness (`core/`) | 904 |

About 60 of the 438 lines exist so that a run can move between providers (a
provider-neutral history, and replaying it in the Anthropic format). Without
the fallback feature the raw agent was 377 lines.

Two observations matter more than the totals:

1. **The SDK removes the plumbing, not the engineering.** 70% of the raw agent is
   HTTP, retries and wire format: work that is necessary, easy to get subtly
   wrong, and identical for every agent. The SDK removes exactly that.
2. **Most of the real work is shared.** The 902 shared lines (tools, path
   containment, test-file protection, budgets, independent grading, language
   detection) are needed by both. An SDK does not write your safety model for you.

## Side by side

### The loop

Without the SDK, the loop is ours. Every stop reason needs a decision:

```python
# raw_agent/loop.py (condensed)
conversation = provider.new_conversation(SYSTEM_PROMPT, toolbox.specs())
conversation.add_user_text(TASK_PROMPT)
while True:
    budget.record_iteration()
    turn = conversation.send()                  # one HTTP request
    budget.record_usage(turn.usage)
    if turn.stop_reason is StopReason.TOOL_USE:
        results = [(c, toolbox.dispatch(c.name, c.arguments)) for c in turn.tool_calls]
        conversation.add_tool_results(results)  # all results in ONE user message
    elif turn.stop_reason is StopReason.END_TURN:
        return EngineOutcome(RunStatus.UNRESOLVED, final_report=turn.text)
    elif turn.stop_reason is StopReason.MAX_TOKENS:
        ...  # answer cut-off tool calls without running them, or ask to continue
    elif turn.stop_reason is StopReason.REFUSAL:
        ...
```

With the SDK, the loop is one call, and we read what it streams back:

```python
# sdk_agent/agent.py (condensed)
async for message in query(prompt=TASK_PROMPT, options=options):
    if isinstance(message, AssistantMessage):
        on_assistant(message)      # count the turn, record usage
    elif isinstance(message, ResultMessage):
        result = message           # final text, total usage, why it stopped
```

### Defining tools

Both use the same `Toolbox.specs()` (name, description, JSON Schema) and the
same `Toolbox.dispatch()`. Only the adapter differs.

```python
# raw: the API's tool format, built by hand
{"name": s.name, "description": s.description, "input_schema": s.input_schema}

# raw: results returned by hand, matched by id, errors flagged
{"type": "tool_result", "tool_use_id": call.id, "content": output, "is_error": True}
```

```python
# SDK: the same spec, registered as an in-process MCP tool
tool(spec.name, spec.description, spec.input_schema)(handler)
create_sdk_mcp_server("bugfix", tools=tools)
```

### Making the comparison fair

The SDK ships with powerful built-in tools (Bash, Read, Edit, Grep, web access).
Left on, the SDK agent would have capabilities the raw agent lacks, and the
comparison would measure tools rather than orchestration. So:

```python
ClaudeAgentOptions(
    tools=[],                    # every built-in tool off
    allowed_tools=[...],         # only our five MCP tools
    permission_mode="dontAsk",   # anything else is denied
    strict_mcp_config=True,      # no MCP servers from the host machine
    setting_sources=[],          # no user/project settings or CLAUDE.md
    system_prompt=SYSTEM_PROMPT, # replaces the SDK's default prompt
)
```

### Errors and retries

Without the SDK we had to classify every failure ourselves (`raw_agent/client.py`):
429/5xx/529 and connection errors are retried with exponential backoff and full
jitter; `retry-after` and `x-should-retry` are honored; 400/401/403/404 are not
retried; and a 429 caused by the account's spend cap is not retried, because
waiting cannot fix it. With the SDK, all of this happens inside the CLI, and we
handle only `ClaudeSDKError` and the result's `subtype`.

### Backup models: what owning the loop buys

This is the one feature the SDK version cannot have. Because the raw agent's
loop talks to a `ModelProvider` and not to Claude directly, it can chain
providers. If Claude fails in a way retries cannot fix (no credit left, a spend
cap, an invalid key, an outage that outlasts the retries), the run continues on
the next model in the chain, from the same point:

```bash
bugfix fix --fallback groq:<model> --fallback ollama:<model>
```

```python
# raw_agent/providers/fallback.py (condensed)
try:
    turn = self._current.send()
except APIError:
    self._index += 1                       # next provider in the chain
    self._current = self.provider.new_conversation(
        self._system, self._tools, self._history)   # replay what was said so far
```

Three things made this possible, and each is a line count the SDK version does
not pay but also cannot use: a second wire format (OpenAI-compatible, which most
free hosts speak), a provider-neutral record of the conversation, and cost
accounting per call, because one run can now be billed at two prices (free
tokens cost nothing). The SDK offers only server-side fallback between Claude
models when a request is refused.

### Where the `anthropic` client library fits

There is a middle layer between the two that this project deliberately does not
build: the official `anthropic` Python client. It would replace most of
`raw_agent/client.py` (HTTP, auth headers, typed errors, retries) but not the
loop, the wire format of tool results, or the stop-reason handling. So the three
options sit on one line:

| Layer | You write | It provides |
|---|---|---|
| Raw HTTP (`raw_agent`) | Transport, retries, wire format, loop | Nothing beyond `httpx` |
| `anthropic` client | Wire format, loop | Transport, typed errors, retries |
| Claude Agent SDK (`sdk_agent`) | Tool handlers and message handling | Everything above, plus the loop, caching and context management |

The raw agent sits at the bottom on purpose: it shows every piece the other two
hide.

## Results on the benchmark

The benchmark has 13 cases in Python, JavaScript, Go and Rust, from easy to hard,
including two cross-file bugs and one rounding trap where the obvious fix breaks
other tests. Every case is validated by the test suite: it must fail as shipped,
and its reference fix must make it pass.

**Run on 2026-11-24** with `claude-haiku-5-5`, effort `medium`, 3 repeats per case
(78 runs in all, total cost about $0.08). Full tables: `benchmark/results/results.md`.

| Agent | Resolved | Mean iterations | Mean input tokens | Mean output tokens | Mean cost (USD) | Median time (s) | Max time (s) | Tampered | Budget hit | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | 39/39 (100%) | 4.9 | 13,456 | 1,063 | 0.0009 | 6.4 | 9.1 | 0 | 0 | 0 |
| sdk | 39/39 (100%) | 5.1 | 15,848 | 1,119 | 0.0011 | 7.7 | 10.9 | 0 | 0 | 0 |

Both agents fixed every case in every repeat. Time is the agent's own solve time
(model calls plus the tests it runs), not the grader's check. The raw agent finished
every run within 10 seconds; the SDK agent did so in 36 of 39 runs, the three slower
ones all on `10_rounding_trap` (10.2 to 10.9 s). The SDK agent is about 1.3 s slower
and uses about 18% more input tokens per run, most likely from starting the bundled
CLI and its larger system prompt.

To rerun:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
BUGFIX_MODEL=claude-haiku-5-5 bugfix benchmark --repeats 3
```

## Trade-offs

| Concern | Raw API | Claude Agent SDK |
|---|---|---|
| Time to a working agent | Slower: you build and test the plumbing | Faster: configuration plus message handling |
| Control over each request | Total: every field, header and retry is yours | Limited to the options the SDK exposes |
| Debugging | Full request/response visibility | Through the SDK's messages and logs |
| Switching model providers | Built: free models and a fallback chain, 252 lines | Not possible; Claude only |
| Dependencies and footprint | `httpx` | The SDK plus a bundled CLI binary, run as a subprocess |
| Keeping up with API changes | Your job (headers, new fields, new stop reasons) | The SDK's job |
| Built-in capabilities | Only what you write | File, shell, web tools, subagents, sessions, hooks (deliberately unused here) |

## When to choose which

**Choose the SDK** when the agent runs on Claude, the built-in harness fits the
job, and time to a reliable result matters most. You inherit a mature loop,
retries, caching and context management, and you write only what is specific to
your problem: here, 149 lines.

**Build on the raw API** when you need to own the loop: a provider-independent
agent (this project's raw agent runs on free models and falls back to them when
Claude fails), unusual control flow, a minimal dependency footprint, or full
visibility into every request for audit or debugging. The price is roughly 290
more lines of plumbing that must be written, tested and maintained.

In both cases, the parts that decide whether the agent can be trusted (what it
may touch, how much it may spend, and who decides whether it succeeded) are
yours to build. That is why they live in `core/` and are shared.
