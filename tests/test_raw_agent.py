"""End-to-end tests of the raw agent against a scripted, in-process Messages API.

The real client, provider, loop, harness, tools and grader all run; only the
HTTP transport is replaced. Each scripted response is what the model "says";
each assertion checks what the agent sent back on the wire.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from benchmark.catalog import load_cases
from core.config import Settings
from core.harness import run_case
from core.types import RunResult, RunStatus
from raw_agent.client import MessagesClient
from raw_agent.loop import RawEngine
from raw_agent.providers.anthropic import AnthropicProvider

CASE = next(c for c in load_cases() if c.id == "01_off_by_one")
FIX = CASE.reference_fix


def _usage(i: int = 100, o: int = 20) -> dict[str, int]:
    return {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": 0}


def _tool_turn(*calls: tuple[str, str, dict[str, Any]], thinking: bool = False) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if thinking:
        content.append({"type": "thinking", "thinking": "", "signature": "sig-abc"})
    content += [
        {"type": "tool_use", "id": cid, "name": name, "input": args} for cid, name, args in calls
    ]
    return {
        "id": "msg",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": "tool_use",
        "usage": _usage(),
    }


def _final(text: str) -> dict[str, Any]:
    return {
        "id": "msg",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": _usage(),
    }


class ScriptedAPI:
    """Serves scripted responses and records every request body."""

    def __init__(self, responses: list[dict[str, Any] | httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        item = self.responses.pop(0)
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


def _run(api: ScriptedAPI, tmp_path: Path, **overrides: Any) -> tuple[RunResult, Path]:
    settings = Settings(**overrides)
    client = MessagesClient(
        settings, api_key="sk-test", transport=httpx.MockTransport(api), sleep=lambda _s: None
    )
    engine = RawEngine(AnthropicProvider(client, settings))
    trace = tmp_path / "trace.jsonl"
    return run_case(CASE.id, CASE.repo, engine, settings, trace_path=trace), trace


def test_full_fix_conversation(tmp_path: Path) -> None:
    api = ScriptedAPI(
        [
            _tool_turn(("t1", "run_tests", {}), thinking=True),
            _tool_turn(
                ("t2", "read_file", {"path": FIX.file}),
                ("t3", "search_code", {"pattern": "range(1"}),
            ),
            _tool_turn(
                ("t4", "edit_file", {"path": "tests/test_stats.py", "old": "85.0", "new": "68.0"})
            ),
            _tool_turn(("t5", "edit_file", {"path": FIX.file, "old": FIX.old, "new": FIX.new})),
            _tool_turn(("t6", "run_tests", {})),
            _final("## Result\nRESOLVED"),
        ]
    )
    result, trace = _run(api, tmp_path)

    assert result.status is RunStatus.RESOLVED
    assert result.iterations == 6
    assert result.tests_passed == result.tests_total == 4
    assert result.usage.input_tokens == 600 and result.usage.output_tokens == 120
    assert result.cost_usd > 0
    assert result.final_report.startswith("## Result")

    first = api.requests[0]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default"
    assert [t["name"] for t in first["tools"]] == [
        "run_tests",
        "list_files",
        "read_file",
        "search_code",
        "edit_file",
    ]
    assert "temperature" not in first

    # Thinking block is sent back unchanged in the assistant turn.
    second = api.requests[1]
    assert second["messages"][1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "sig-abc",
    }
    first_result = second["messages"][2]["content"][0]
    assert first_result["tool_use_id"] == "t1"
    assert "TESTS FAILING" in first_result["content"]

    # Two parallel calls -> both results in ONE user message.
    third = api.requests[2]["messages"][-1]
    assert third["role"] == "user"
    assert [b["tool_use_id"] for b in third["content"]] == ["t2", "t3"]

    # The attempt to edit a test was refused at the tool layer.
    refused = api.requests[3]["messages"][-1]["content"][0]
    assert refused["is_error"] is True and "read-only" in refused["content"]

    events = [json.loads(line)["kind"] for line in trace.read_text().splitlines()]
    assert events[:2] == ["baseline", "run_start"] and events[-1] == "run_end"
    assert events.count("model_call") == 6 and events.count("tool_call") == 6
    assert "sk-test" not in trace.read_text()


def test_agent_claim_is_not_trusted(tmp_path: Path) -> None:
    api = ScriptedAPI([_final("## Result\nRESOLVED (trust me)")])
    result, _ = _run(api, tmp_path)
    assert result.status is RunStatus.UNRESOLVED


def test_iteration_budget_stops_the_loop(tmp_path: Path) -> None:
    api = ScriptedAPI([_tool_turn((f"t{i}", "list_files", {})) for i in range(10)])
    result, _ = _run(api, tmp_path, max_iterations=3)
    assert result.status is RunStatus.BUDGET_EXCEEDED
    assert result.iterations == 3
    assert len(api.requests) == 3


def test_cost_budget_stops_the_loop(tmp_path: Path) -> None:
    expensive = _tool_turn(("t1", "list_files", {}))
    expensive["usage"] = _usage(i=10, o=50_000)  # $0.50 at $10/MTok
    api = ScriptedAPI([expensive, expensive])
    result, _ = _run(api, tmp_path, max_cost_usd=0.40)
    assert result.status is RunStatus.BUDGET_EXCEEDED
    assert "cost" in (result.error or "")


def test_max_tokens_without_tool_call_asks_to_continue(tmp_path: Path) -> None:
    cut = _final("I was explaining")
    cut["stop_reason"] = "max_tokens"
    api = ScriptedAPI([cut, _final("done")])
    result, _ = _run(api, tmp_path)
    last_user = api.requests[1]["messages"][-1]
    assert "cut off" in last_user["content"][0]["text"]
    assert result.status is RunStatus.UNRESOLVED


def test_max_tokens_with_truncated_tool_call_is_not_executed(tmp_path: Path) -> None:
    cut = _tool_turn(("t1", "edit_file", {"path": FIX.file, "old": FIX.old, "new": FIX.new}))
    cut["stop_reason"] = "max_tokens"
    api = ScriptedAPI([cut, _final("done")])
    result, _ = _run(api, tmp_path)
    answered = api.requests[1]["messages"][-1]["content"][0]
    assert answered["tool_use_id"] == "t1" and answered["is_error"] is True
    assert result.status is RunStatus.UNRESOLVED  # the fix was never applied


def test_refusal_ends_run_as_error(tmp_path: Path) -> None:
    refusal = _final("")
    refusal["stop_reason"] = "refusal"
    refusal["stop_details"] = {"type": "refusal", "category": None}
    result, _ = _run(ScriptedAPI([refusal]), tmp_path)
    assert result.status is RunStatus.ERROR
    assert "declined" in (result.error or "")


def test_fatal_api_error_ends_run_as_error(tmp_path: Path) -> None:
    bad = httpx.Response(
        400,
        json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad field"}},
    )
    result, _ = _run(ScriptedAPI([bad]), tmp_path)
    assert result.status is RunStatus.ERROR
    assert "InvalidRequestError" in (result.error or "")
