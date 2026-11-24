"""Free models (OpenAI-compatible hosts) and the fallback chain, against scripted APIs.

As in test_raw_agent.py, the real clients, providers, loop, harness and grader
run; only the HTTP transports are replaced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from benchmark.catalog import load_cases
from cli import main as cli_main
from cli.engines import EngineSetupError, open_engine
from core.config import FREE, PRICING, Settings
from core.guards import Budget
from core.harness import run_case
from core.types import RunStatus, ToolResult, Usage
from raw_agent.client import InvalidRequestError, MessagesClient
from raw_agent.loop import RawEngine
from raw_agent.providers.anthropic import AnthropicProvider
from raw_agent.providers.base import AssistantReply, ToolCall, ToolResults, UserText
from raw_agent.providers.fallback import FallbackProvider
from raw_agent.providers.openai_compat import (
    ChatCompletionsClient,
    OpenAICompatProvider,
    split_model,
)

CASE = next(c for c in load_cases() if c.id == "01_off_by_one")
FIX = CASE.reference_fix
FREE_MODEL = "groq:test-model"


class ScriptedAPI:
    def __init__(self, responses: list[dict[str, Any] | httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        item = self.responses.pop(0)
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


# --- scripted replies in the OpenAI-compatible format


def _chat_tools(
    *calls: tuple[str | None, str, dict[str, Any]], finish: str = "tool_calls"
) -> dict[str, Any]:
    tool_calls = []
    for cid, name, args in calls:
        call: dict[str, Any] = {
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }
        if cid:
            call["id"] = cid
        tool_calls.append(call)
    return {
        "model": "test-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _chat_final(text: str) -> dict[str, Any]:
    return {
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


FIX_SCRIPT = [
    _chat_tools(("c1", "run_tests", {})),
    _chat_tools(("c2", "edit_file", {"path": FIX.file, "old": FIX.old, "new": FIX.new})),
    _chat_tools(("c3", "run_tests", {})),
    _chat_final("## Result\nRESOLVED"),
]


# --- scripted replies in the Anthropic format


def _claude_tools(cid: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "msg",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "tool_use", "id": cid, "name": name, "input": args}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1000, "output_tokens": 100},
    }


def _claude_error(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
    )


@pytest.fixture(autouse=True)
def _groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")


def _free(api: ScriptedAPI, settings: Settings) -> OpenAICompatProvider:
    client = ChatCompletionsClient(
        settings, "groq", transport=httpx.MockTransport(api), sleep=lambda _s: None
    )
    return OpenAICompatProvider(client, settings, FREE_MODEL)


def _claude(api: ScriptedAPI, settings: Settings) -> AnthropicProvider:
    client = MessagesClient(
        settings, api_key="sk-test", transport=httpx.MockTransport(api), sleep=lambda _s: None
    )
    return AnthropicProvider(client, settings)


def _events(trace: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in trace.read_text().splitlines()]


# --- the free provider on its own


def test_free_model_fixes_the_bug_at_zero_cost(tmp_path: Path) -> None:
    settings = Settings(model=FREE_MODEL)
    api = ScriptedAPI(list(FIX_SCRIPT))
    engine = RawEngine(_free(api, settings))
    result = run_case(CASE.id, CASE.repo, engine, settings, trace_path=tmp_path / "t.jsonl")

    assert result.status is RunStatus.RESOLVED
    assert result.cost_usd == 0
    assert result.usage.input_tokens == 400

    first = api.requests[0]
    assert first["model"] == "test-model"
    assert first["messages"][0]["role"] == "system"
    assert first["messages"][1] == {"role": "user", "content": first["messages"][1]["content"]}
    assert [t["function"]["name"] for t in first["tools"]][:1] == ["run_tests"]
    assert "parameters" in first["tools"][0]["function"]
    assert api.headers[0]["authorization"] == "Bearer gsk-test"

    second = api.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"


def test_missing_ids_bad_json_and_stop_with_tool_calls(tmp_path: Path) -> None:
    settings = Settings(model=FREE_MODEL)
    broken = _chat_tools((None, "read_file", {}), finish="stop")
    broken["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
    api = ScriptedAPI([broken, _chat_final("done")])
    conversation = _free(api, settings).new_conversation("sys", [])
    conversation.add_user_text("go")

    turn = conversation.send()
    assert turn.stop_reason.value == "tool_use"
    assert turn.tool_calls[0].id == "call_1"
    assert turn.tool_calls[0].arguments == {}
    assert turn.free

    conversation.add_tool_results([(turn.tool_calls[0], ToolResult("bad args", is_error=True))])
    conversation.send()
    assert api.requests[1]["messages"][-1]["content"] == "ERROR: bad args"


def test_bare_string_error_is_mapped() -> None:
    settings = Settings(model=FREE_MODEL, api_max_retries=0)
    api = ScriptedAPI([httpx.Response(400, json={"error": "model does not support tools"})])
    conversation = _free(api, settings).new_conversation("sys", [])
    conversation.add_user_text("go")
    with pytest.raises(InvalidRequestError, match="does not support tools"):
        conversation.send()


def test_history_replay_in_both_formats() -> None:
    call = ToolCall("x1", "run_tests", {})
    history: list[UserText | AssistantReply | ToolResults] = [
        UserText("task"),
        AssistantReply("looking", (call,)),
        ToolResults(((call, ToolResult("2 failed", is_error=True)),)),
    ]
    settings = Settings()
    chat = _free(ScriptedAPI([]), settings).new_conversation("sys", [], history)
    assert [m["role"] for m in chat.messages] == ["system", "user", "assistant", "tool"]

    claude = _claude(ScriptedAPI([]), settings).new_conversation("sys", [], history)
    assert [m["role"] for m in claude.messages] == ["user", "assistant", "user"]
    assert claude.messages[1]["content"][1]["type"] == "tool_use"
    assert claude.messages[2]["content"][0]["is_error"] is True


# --- the fallback chain


def test_no_credit_falls_back_before_the_first_turn(tmp_path: Path) -> None:
    settings = Settings(api_max_retries=0)
    claude_api = ScriptedAPI([_claude_error(400, "Your credit balance is too low")])
    free_api = ScriptedAPI(list(FIX_SCRIPT))
    engine = RawEngine(FallbackProvider([_claude(claude_api, settings), _free(free_api, settings)]))
    trace = tmp_path / "t.jsonl"
    result = run_case(CASE.id, CASE.repo, engine, settings, trace_path=trace)

    assert result.status is RunStatus.RESOLVED
    assert result.cost_usd == 0
    switches = [e for e in _events(trace) if e.get("kind") == "fallback"]
    assert len(switches) == 1
    assert switches[0]["from"] == "claude-sonnet-5-5" and switches[0]["to"] == FREE_MODEL
    assert "credit balance" in switches[0]["error"]


def test_outage_mid_run_continues_from_the_same_point(tmp_path: Path) -> None:
    settings = Settings(api_max_retries=0)
    claude_api = ScriptedAPI(
        [_claude_tools("t1", "run_tests", {}), httpx.Response(529, json={"error": {}})]
    )
    free_api = ScriptedAPI(list(FIX_SCRIPT[1:]))
    engine = RawEngine(FallbackProvider([_claude(claude_api, settings), _free(free_api, settings)]))
    result = run_case(CASE.id, CASE.repo, engine, settings, trace_path=tmp_path / "t.jsonl")

    assert result.status is RunStatus.RESOLVED
    # Only Claude's one turn is charged, at Claude's price.
    claude_turn = Usage(input_tokens=1000, output_tokens=100)
    from core.guards import estimate_cost

    assert result.cost_usd == pytest.approx(
        estimate_cost(claude_turn, PRICING["claude-sonnet-5-5"]), abs=1e-6
    )
    # The free model received the conversation so far: task, Claude's call, its result.
    replayed = free_api.requests[0]["messages"]
    assert [m["role"] for m in replayed] == ["system", "user", "assistant", "tool"]
    assert replayed[2]["tool_calls"][0]["id"] == "t1"
    assert "TESTS FAILING" in replayed[3]["content"]


def test_every_provider_failing_is_an_error(tmp_path: Path) -> None:
    settings = Settings(api_max_retries=0)
    claude_api = ScriptedAPI([_claude_error(401, "invalid x-api-key")])
    free_api = ScriptedAPI([httpx.Response(503, json={"error": {"message": "down"}})])
    engine = RawEngine(FallbackProvider([_claude(claude_api, settings), _free(free_api, settings)]))
    result = run_case(CASE.id, CASE.repo, engine, settings, trace_path=tmp_path / "t.jsonl")
    assert result.status is RunStatus.ERROR
    assert "ServerError" in (result.error or "")


# --- settings, pricing and wiring


def test_split_model() -> None:
    assert split_model("ollama:qwen2.5-coder:7b") == ("ollama", "qwen2.5-coder:7b")
    with pytest.raises(ValueError, match="unknown host"):
        split_model("nowhere:model")
    with pytest.raises(ValueError, match="expected"):
        split_model("groq:")


def test_settings_for_free_models() -> None:
    settings = Settings(model="ollama:llama", fallback_models=" groq:a , ,openrouter:b ")
    assert settings.pricing == FREE
    assert settings.fallbacks == ["groq:a", "openrouter:b"]


def test_budget_prices_each_call_by_its_own_model() -> None:
    budget = Budget(Settings(model="claude-sonnet-5-5", max_total_tokens=10**7, max_cost_usd=100))
    budget.record_usage(Usage(input_tokens=1_000_000))
    budget.record_usage(Usage(input_tokens=1_000_000), FREE)
    budget.record_usage(Usage(input_tokens=1_000_000), PRICING["claude-haiku-5-5"])
    assert budget.cost_usd == pytest.approx(2.0 + 0.0 + 0.10)
    assert budget.usage.input_tokens == 3_000_000


def test_sdk_engine_refuses_a_free_model() -> None:
    with (
        pytest.raises(EngineSetupError, match="Claude only"),
        open_engine("sdk", Settings(model=FREE_MODEL)),
    ):
        pass


def test_raw_engine_builds_a_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    settings = Settings(fallback_models="ollama:llama")
    with open_engine("raw", settings) as engine:
        assert isinstance(engine, RawEngine)
        assert isinstance(engine._provider, FallbackProvider)
        assert engine._provider.name == "claude-sonnet-5-5 -> ollama:llama"


def test_cli_names_the_missing_free_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    code = cli_main.main(["fix", str(CASE.repo), "--fallback", FREE_MODEL, "--dry-run"])
    assert code == 2
    assert "GROQ_API_KEY is not set" in capsys.readouterr().out


def test_cli_local_model_needs_no_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = cli_main.main(["fix", str(CASE.repo), "--model", "ollama:llama", "--engine", "sdk"])
    assert code == 2
    assert "Claude only" in capsys.readouterr().out
