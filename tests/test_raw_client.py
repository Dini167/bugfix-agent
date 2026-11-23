from __future__ import annotations

import json

import httpx
import pytest

from core.config import Settings
from raw_agent.client import (
    APIConnectionError,
    AuthenticationError,
    InvalidRequestError,
    MessagesClient,
    OverloadedError,
    RateLimitError,
    SpendLimitError,
)

OK_BODY = {
    "id": "msg_1",
    "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": "hi"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 1},
}


def _error(
    status: int, kind: str, headers: dict[str, str] | None = None, **details: str
) -> httpx.Response:
    error: dict[str, object] = {"type": kind, "message": f"{kind} happened"}
    if details:
        error["details"] = details
    return httpx.Response(status, json={"type": "error", "error": error}, headers=headers)


def _client(
    responses: list[httpx.Response | Exception],
    sleeps: list[float],
    seen: list[httpx.Request] | None = None,
    **overrides: object,
) -> MessagesClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    settings = Settings(api_max_retries=3, **overrides)  # type: ignore[arg-type]
    return MessagesClient(
        settings, api_key="sk-test", transport=httpx.MockTransport(handler), sleep=sleeps.append
    )


def test_success_and_headers() -> None:
    seen: list[httpx.Request] = []
    client = _client([httpx.Response(200, json=OK_BODY)], [], seen)
    assert client.create_message({"model": "m"})["id"] == "msg_1"
    request = seen[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-test"
    assert request.headers["anthropic-version"] == "2026-10-21"
    assert request.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert json.loads(request.content) == {"model": "m"}


def test_fallback_header_can_be_disabled() -> None:
    seen: list[httpx.Request] = []
    client = _client([httpx.Response(200, json=OK_BODY)], [], seen, refusal_fallback=False)
    client.create_message({})
    assert "anthropic-beta" not in seen[0].headers


@pytest.mark.parametrize(
    "first",
    [
        _error(529, "overloaded_error"),
        _error(500, "api_error"),
        _error(429, "rate_limit_error"),
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_transient_failures_are_retried(first: httpx.Response | Exception) -> None:
    sleeps: list[float] = []
    client = _client([first, httpx.Response(200, json=OK_BODY)], sleeps)
    assert client.create_message({})["id"] == "msg_1"
    assert len(sleeps) == 1


def test_retry_after_is_honored() -> None:
    sleeps: list[float] = []
    response = _error(429, "rate_limit_error", headers={"retry-after": "7"})
    client = _client([response, httpx.Response(200, json=OK_BODY)], sleeps)
    client.create_message({})
    assert sleeps == [pytest.approx(7.0, abs=1.0)]
    assert sleeps[0] >= 7.0


def test_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    client = _client([_error(529, "overloaded_error")] * 4, sleeps)
    with pytest.raises(OverloadedError):
        client.create_message({})
    assert len(sleeps) == 3


def test_backoff_grows_and_is_capped() -> None:
    sleeps: list[float] = []
    client = _client(
        [_error(500, "api_error")] * 4, sleeps, retry_base_delay_s=1.0, retry_max_delay_s=2.5
    )
    with pytest.raises(Exception, match="api_error"):
        client.create_message({})
    assert all(0 <= s <= 2.5 for s in sleeps)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (_error(400, "invalid_request_error"), InvalidRequestError),
        (_error(401, "authentication_error"), AuthenticationError),
        (
            _error(429, "rate_limit_error", error_code="enforced_spend_limit_reached"),
            SpendLimitError,
        ),
    ],
)
def test_fatal_errors_are_not_retried(response: httpx.Response, error: type[Exception]) -> None:
    sleeps: list[float] = []
    client = _client([response], sleeps)
    with pytest.raises(error):
        client.create_message({})
    assert sleeps == []


def test_x_should_retry_false_overrides() -> None:
    sleeps: list[float] = []
    client = _client([_error(529, "overloaded_error", headers={"x-should-retry": "false"})], sleeps)
    with pytest.raises(OverloadedError):
        client.create_message({})
    assert sleeps == []


def test_x_should_retry_true_overrides() -> None:
    sleeps: list[float] = []
    response = _error(409, "conflict", headers={"x-should-retry": "true"})
    client = _client([response, httpx.Response(200, json=OK_BODY)], sleeps)
    assert client.create_message({})["id"] == "msg_1"


def test_connection_error_after_retries() -> None:
    client = _client([httpx.ConnectError("down")] * 4, [])
    with pytest.raises(APIConnectionError):
        client.create_message({})


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthenticationError):
        MessagesClient(Settings())


def test_error_message_has_request_id_but_not_key() -> None:
    response = _error(400, "invalid_request_error", headers={"request-id": "req_42"})
    client = _client([response], [])
    with pytest.raises(InvalidRequestError) as info:
        client.create_message({})
    assert "req_42" in str(info.value)
    assert "sk-test" not in str(info.value)


def test_rate_limit_error_is_retryable_class() -> None:
    assert RateLimitError.retryable and not SpendLimitError.retryable
