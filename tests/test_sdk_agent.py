"""Tests for the SDK agent that never start the Claude Code CLI or call the API.

`SdkEngine` takes the SDK's `query` function as a dependency. Here it is
replaced by a scripted async generator that does what the CLI would do: call
our MCP tool handlers and stream SDK message objects back.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    Message,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolUseBlock,
)

from benchmark.catalog import load_cases
from core.config import Settings
from core.harness import run_case
from core.prompts import SYSTEM_PROMPT
from core.sandbox import Workspace
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import RunResult, RunStatus
from sdk_agent.agent import SdkEngine, build_options, build_tools, mcp_tool_name

CASE = next(c for c in load_cases() if c.id == "01_off_by_one")
FIX = CASE.reference_fix


def _assistant(mid: str, *blocks: Any, usage: dict[str, int] | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-sonnet-5-5",
        usage=usage or {"input_tokens": 100, "output_tokens": 20},
        message_id=mid,
    )


def _result(subtype: str = "success", is_error: bool = False, **kw: Any) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=5,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        **kw,
    )


Script = Callable[[SdkEngine, ClaudeAgentOptions], AsyncIterator[Message]]


def _run(script: Script, tmp_path: Path, **overrides: Any) -> tuple[RunResult, SdkEngine, Path]:
    engine = SdkEngine()

    def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        assert "failing" in prompt
        return script(engine, options)

    engine.query_fn = fake_query
    trace = tmp_path / "trace.jsonl"
    result = run_case(CASE.id, CASE.repo, engine, Settings(**overrides), trace_path=trace)
    return result, engine, trace


async def _invoke(t: SdkMcpTool[Any], args: dict[str, Any]) -> dict[str, Any]:
    return await t.handler(args)


async def _call(engine: SdkEngine, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return await engine.tools[name].handler(args)


# ------------------------------------------------------------- configuration


def test_options_match_raw_agent_capabilities(workspace: Any) -> None:
    settings = Settings(max_iterations=9, max_cost_usd=0.75, effort="high")
    toolbox = Toolbox(workspace, settings)
    tools = build_tools(toolbox, Tracer(None))
    options = build_options(settings, toolbox, tools)

    assert options.system_prompt == SYSTEM_PROMPT
    assert options.tools == []  # no Bash/Read/Edit or other built-ins
    assert options.allowed_tools == [mcp_tool_name(s.name) for s in Toolbox.specs()]
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.cwd == str(workspace.root)
    assert (options.model, options.effort) == ("claude-sonnet-5-5", "high")
    assert (options.max_turns, options.max_budget_usd) == (9, 0.75)
    assert list(options.mcp_servers) == ["bugfix"]  # type: ignore[arg-type]


def test_tools_are_identical_to_shared_specs(workspace: Workspace, settings: Settings) -> None:
    tools = build_tools(Toolbox(workspace, settings), Tracer(None))
    assert [(t.name, t.description, t.input_schema) for t in tools] == [
        (s.name, s.description, s.input_schema) for s in Toolbox.specs()
    ]


def test_tool_handler_returns_mcp_content(workspace: Workspace, settings: Settings) -> None:
    tools = {t.name: t for t in build_tools(Toolbox(workspace, settings), Tracer(None))}
    ok = asyncio.run(_invoke(tools["run_tests"], {}))
    assert ok["content"][0]["type"] == "text" and "TESTS FAILING" in ok["content"][0]["text"]
    assert "is_error" not in ok
    refused = asyncio.run(
        _invoke(tools["edit_file"], {"path": "tests/test_calc.py", "old": "== 5", "new": "== 0"})
    )
    assert refused["is_error"] is True and "read-only" in refused["content"][0]["text"]


# ------------------------------------------------------------- end to end


def test_full_fix_conversation(tmp_path: Path) -> None:
    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield _assistant("m1", ToolUseBlock("t1", mcp_tool_name("run_tests"), {}))
        await _call(engine, "run_tests", {})
        edit = {"path": FIX.file, "old": FIX.old, "new": FIX.new}
        yield _assistant("m2", TextBlock("Hypothesis: loop skips index 0."))
        yield _assistant("m2", ToolUseBlock("t2", mcp_tool_name("edit_file"), edit))  # same msg
        assert "is_error" not in await _call(engine, "edit_file", edit)
        yield _assistant("m3", ToolUseBlock("t3", mcp_tool_name("run_tests"), {}))
        assert "ALL TESTS PASSED" in (await _call(engine, "run_tests", {}))["content"][0]["text"]
        yield _assistant("m4", TextBlock("## Result\nRESOLVED"))
        yield _result(
            result="## Result\nRESOLVED",
            usage={"input_tokens": 900, "output_tokens": 150, "cache_read_input_tokens": 50},
        )

    result, _, trace = _run(script, tmp_path)
    assert result.status is RunStatus.RESOLVED
    assert result.iterations == 4  # m2 arrived in two parts but is one model call
    assert (result.usage.input_tokens, result.usage.output_tokens) == (900, 150)
    assert result.usage.cache_read_input_tokens == 50
    assert result.final_report.startswith("## Result")
    kinds = [json.loads(line)["kind"] for line in trace.read_text().splitlines()]
    assert kinds.count("model_call") == 4 and kinds.count("tool_call") == 3


def test_agent_claim_is_not_trusted(tmp_path: Path) -> None:
    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield _assistant("m1", TextBlock("RESOLVED, trust me"))
        yield _result(result="## Result\nRESOLVED")

    result, _, _ = _run(script, tmp_path)
    assert result.status is RunStatus.UNRESOLVED


def test_shared_budget_stops_stream_and_closes_it(tmp_path: Path) -> None:
    closed: list[bool] = []

    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        try:
            for i in range(10):
                yield _assistant(f"m{i}", ToolUseBlock(f"t{i}", mcp_tool_name("list_files"), {}))
        finally:
            closed.append(True)

    result, _, _ = _run(script, tmp_path, max_iterations=3)
    assert result.status is RunStatus.BUDGET_EXCEEDED
    assert result.iterations == 3
    assert closed == [True]


@pytest.mark.parametrize("subtype", ["error_max_turns", "error_max_budget_usd"])
def test_sdk_native_caps_map_to_budget_exceeded(tmp_path: Path, subtype: str) -> None:
    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield _assistant("m1", ToolUseBlock("t1", mcp_tool_name("list_files"), {}))
        yield _result(subtype=subtype, is_error=True)

    result, _, _ = _run(script, tmp_path)
    assert result.status is RunStatus.BUDGET_EXCEEDED
    assert result.error == subtype


def test_sdk_execution_error(tmp_path: Path) -> None:
    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield _result(subtype="error_during_execution", is_error=True, errors=["API 529"])

    result, _, _ = _run(script, tmp_path)
    assert result.status is RunStatus.ERROR
    assert "529" in (result.error or "")


def test_missing_cli_is_reported(tmp_path: Path) -> None:
    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        raise CLINotFoundError()
        yield  # pragma: no cover

    result, _, _ = _run(script, tmp_path)
    assert result.status is RunStatus.ERROR
    assert "CLINotFoundError" in (result.error or "")


def test_stream_without_result_is_an_error(tmp_path: Path) -> None:
    async def script(engine: SdkEngine, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield _assistant("m1", TextBlock("..."))

    result, _, _ = _run(script, tmp_path)
    assert result.status is RunStatus.ERROR
