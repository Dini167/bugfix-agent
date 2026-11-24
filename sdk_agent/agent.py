"""The same agent, on the Claude Agent SDK.

Compare with `raw_agent/`: there is no HTTP client, no wire format, no
transcript management and no loop here. The SDK owns all of that. This file
only wires the shared pieces into it:

* the shared tools are exposed as in-process MCP tools;
* every built-in SDK tool (Bash, Read, Edit, ...) is disabled, so both agents
  have exactly the same capabilities;
* user and project settings are not loaded, so nothing on the host machine
  changes the agent's behavior;
* the shared system prompt, model, effort and budgets are passed through.

The SDK's own caps (`max_turns`, `max_budget_usd`) are set from the same
settings, and the shared `Budget` is still updated from the message stream, so
budget accounting is identical across both implementations.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    Message,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from core.config import Settings
from core.guards import Budget, BudgetExceeded
from core.harness import EngineOutcome
from core.prompts import SYSTEM_PROMPT, TASK_PROMPT
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import RunStatus, Usage

SERVER_NAME = "bugfix"

QueryFn = Callable[..., AsyncIterator[Message]]

# ResultMessage subtypes that mean "a cap was hit", not "something broke".
_BUDGET_SUBTYPES = frozenset({"error_max_turns", "error_max_budget_usd"})


def mcp_tool_name(name: str) -> str:
    """The name the SDK exposes an in-process MCP tool under."""
    return f"mcp__{SERVER_NAME}__{name}"


def build_tools(toolbox: Toolbox, tracer: Tracer) -> list[SdkMcpTool[Any]]:
    """Wrap each shared tool as an SDK MCP tool. Same code, same schemas."""
    tools: list[SdkMcpTool[Any]] = []
    for spec in toolbox.specs():

        async def handler(arguments: dict[str, Any], _name: str = spec.name) -> dict[str, Any]:
            def run() -> tuple[str, bool]:
                with tracer.span("tool_call", tool=_name, arguments=arguments) as span:
                    result = toolbox.dispatch(_name, arguments)
                    span.update(is_error=result.is_error, output_chars=len(result.output))
                return result.output, result.is_error

            # Tools run subprocesses (pytest), so keep them off the event loop.
            output, is_error = await asyncio.to_thread(run)
            response: dict[str, Any] = {"content": [{"type": "text", "text": output}]}
            if is_error:
                response["is_error"] = True
            return response

        tools.append(tool(spec.name, spec.description, spec.input_schema)(handler))
    return tools


def build_options(
    settings: Settings, toolbox: Toolbox, tools: list[SdkMcpTool[Any]]
) -> ClaudeAgentOptions:
    """SDK configuration that matches the raw agent's capabilities exactly."""
    server = create_sdk_mcp_server(SERVER_NAME, tools=tools)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=settings.model,
        effort=settings.effort,  # type: ignore[arg-type]
        tools=[],  # disable every built-in tool
        mcp_servers={SERVER_NAME: server},
        strict_mcp_config=True,  # ignore any MCP servers configured on the host
        allowed_tools=[mcp_tool_name(t.name) for t in tools],
        permission_mode="dontAsk",  # anything not pre-approved is denied
        setting_sources=[],  # do not load user/project settings or CLAUDE.md
        cwd=str(toolbox.workspace_root),
        max_turns=settings.max_iterations,
        max_budget_usd=settings.max_cost_usd,
    )


def _usage(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cache_read_input_tokens=int(raw.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens") or 0),
    )


class SdkEngine:
    """Implements `core.harness.Engine` on top of `claude_agent_sdk.query`."""

    name = "sdk_agent"

    def __init__(self, query_fn: QueryFn = query) -> None:
        self.query_fn = query_fn
        self.tools: dict[str, SdkMcpTool[Any]] = {}

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        return asyncio.run(self._solve(toolbox, budget, tracer))

    async def _solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        tools = build_tools(toolbox, tracer)
        self.tools = {t.name: t for t in tools}
        options = build_options(budget.settings, toolbox, tools)

        seen_messages: set[str] = set()
        texts: list[str] = []
        result: ResultMessage | None = None
        try:
            stream = self.query_fn(prompt=TASK_PROMPT, options=options)
            try:
                async for message in stream:
                    if isinstance(message, AssistantMessage):
                        self._on_assistant(message, budget, tracer, seen_messages, texts)
                    elif isinstance(message, ResultMessage):
                        result = message
            finally:
                # Stopping early (e.g. a budget cap) must also stop the SDK's subprocess.
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()
        except BudgetExceeded as exc:
            tracer.event("budget_exceeded", reason=str(exc))
            return EngineOutcome(RunStatus.BUDGET_EXCEEDED, error=str(exc))
        except ClaudeSDKError as exc:
            tracer.event("sdk_error", error=str(exc), error_class=type(exc).__name__)
            return EngineOutcome(RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}")

        if result is None:
            return EngineOutcome(RunStatus.ERROR, error="SDK stream ended without a result")
        return self._outcome(result, budget, texts)

    @staticmethod
    def _on_assistant(
        message: AssistantMessage,
        budget: Budget,
        tracer: Tracer,
        seen: set[str],
        texts: list[str],
    ) -> None:
        """Count each model response once, even if the SDK streams it in several parts."""
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                texts.append(block.text)
        key = message.message_id or str(id(message))
        if key in seen:
            return
        seen.add(key)
        budget.record_iteration()
        usage = _usage(message.usage)
        tracer.event(
            "model_call",
            iteration=budget.iterations,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            tool_calls=[b.name for b in message.content if isinstance(b, ToolUseBlock)],
        )
        budget.record_usage(usage)

    @staticmethod
    def _outcome(result: ResultMessage, budget: Budget, texts: list[str]) -> EngineOutcome:
        # The result's usage is the SDK's authoritative total for the whole run.
        if result.usage:
            budget.usage = _usage(result.usage)
        report = result.result or (texts[-1] if texts else "")
        if result.subtype in _BUDGET_SUBTYPES:
            return EngineOutcome(
                RunStatus.BUDGET_EXCEEDED, final_report=report, error=result.subtype
            )
        if result.is_error:
            detail = "; ".join(result.errors or []) or result.subtype
            return EngineOutcome(RunStatus.ERROR, final_report=report, error=detail)
        return EngineOutcome(RunStatus.UNRESOLVED, final_report=report)
