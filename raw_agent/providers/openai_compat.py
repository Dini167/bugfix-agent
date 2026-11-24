"""Free models, through the OpenAI-compatible Chat Completions format.

Most free model hosts speak the same wire format, so one provider covers them
all. A model is named `<host>:<model>`, for example `ollama:qwen2.5-coder:7b`
or `groq:<model-id>`; the host decides the base URL and which environment
variable holds the key:

| Host | Base URL | Key |
|---|---|---|
| ollama | http://localhost:11434/v1 | none: runs on your own computer |
| groq | https://api.groq.com/openai/v1 | GROQ_API_KEY |
| openrouter | https://openrouter.ai/api/v1 | OPENROUTER_API_KEY |
| gemini | https://generativelanguage.googleapis.com/v1beta/openai | GEMINI_API_KEY |

The model must support tool calling. Tokens from these hosts are treated as
free: they count toward the token cap but not toward the cost cap.
"""

# 📘 [בלי SDK] כאן מתחברים למודלים חינמיים. רוב השירותים החינמיים מדברים באותו פורמט (הפורמט של
# 📘 OpenAI), ולכן קובץ אחד מכסה את כולם. מה שמשתנה בין שירות לשירות הוא רק הכתובת והמפתח.
# 📘 [עם SDK] ל-Claude Agent SDK אין אפשרות כזו בכלל: הוא עובד רק עם Claude.

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from core.config import Settings
from core.tools import ToolSpec
from core.types import ToolResult, Usage
from raw_agent.client import AuthenticationError, JsonHTTPClient
from raw_agent.providers.base import (
    AssistantReply,
    History,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolResults,
    UserText,
)


@dataclass(frozen=True, slots=True)
class Host:
    base_url: str
    key_env: str | None


# 📘 רשימת השירותים הנתמכים. כדי להוסיף שירות, מוסיפים כאן שורה אחת.
HOSTS: dict[str, Host] = {
    "ollama": Host("http://localhost:11434/v1", None),
    "groq": Host("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": Host("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "gemini": Host("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}

_FINISH_REASONS = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


def split_model(spec: str) -> tuple[str, str]:
    """`groq:some-model` -> ("groq", "some-model"). The model part may contain colons."""
    host, sep, model = spec.partition(":")
    if not sep or not model:
        raise ValueError(f"expected <host>:<model>, got {spec!r}")
    if host not in HOSTS:
        raise ValueError(f"unknown host {host!r}; choose one of: {', '.join(HOSTS)}")
    return host, model


class ChatCompletionsClient(JsonHTTPClient):
    """POST /chat/completions with the same retries and error types as the Anthropic client."""

    def __init__(
        self,
        settings: Settings,
        host: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        info = HOSTS[host]
        headers: dict[str, str] = {}
        if info.key_env:
            key = os.environ.get(info.key_env)
            if not key:
                raise AuthenticationError(f"{info.key_env} is not set (needed for {host})")
            headers["authorization"] = f"Bearer {key}"
        super().__init__(
            settings, info.base_url, headers, transport=transport, sleep=sleep, rng=rng
        )

    def create_completion(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.post_json("/chat/completions", body)


# 📘 אותם כלים בדיוק, בפורמט של OpenAI: כל כלי עטוף ב-"function", והסכמה נקראת parameters.
def to_function_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            },
        }
        for s in specs
    ]


class ChatConversation:
    def __init__(
        self,
        client: ChatCompletionsClient,
        settings: Settings,
        model: str,
        system: str,
        tools: list[ToolSpec],
    ) -> None:
        self._client = client
        self._settings = settings
        self._model = model
        self._tools = to_function_tools(tools)
        # 📘 כאן הוראות המערכת הן פשוט ההודעה הראשונה בשיחה, עם role "system".
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        self._calls = 0

    def replay(self, history: History) -> None:
        for entry in history:
            if isinstance(entry, UserText):
                self.add_user_text(entry.text)
            elif isinstance(entry, ToolResults):
                self.add_tool_results(list(entry.results))
            elif isinstance(entry, AssistantReply):
                self.messages.append(_assistant_message(entry.text, list(entry.tool_calls)))

    def add_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    # 📘 הבדל בפורמט: כאן כל תוצאת כלי היא הודעה נפרדת עם role "tool". ב-Anthropic כולן נכנסות
    # 📘 להודעה אחת. בפורמט הזה אין שדה is_error, ולכן מסמנים שגיאה בתחילת הטקסט.
    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        for call, result in results:
            content = result.output or "(no output)"
            if result.is_error:
                content = f"ERROR: {content}"
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

    def send(self) -> ModelTurn:
        data = self._client.create_completion(
            {
                "model": self._model,
                "messages": self.messages,
                "tools": self._tools,
                "max_tokens": self._settings.max_tokens_per_response,
            }
        )
        choices = data.get("choices") or [{}]
        message: dict[str, Any] = choices[0].get("message") or {}
        text = message.get("content") or ""
        calls = [self._parse_call(raw) for raw in message.get("tool_calls") or []]
        self.messages.append(_assistant_message(text, calls))

        raw_stop = choices[0].get("finish_reason")
        stop = _FINISH_REASONS.get(raw_stop or "", StopReason.OTHER)
        # Some servers report "stop" even when the reply carries tool calls.
        if calls and stop in (StopReason.END_TURN, StopReason.OTHER):
            stop = StopReason.TOOL_USE
        usage = data.get("usage") or {}
        return ModelTurn(
            text=text.strip(),
            tool_calls=calls,
            stop_reason=stop,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            model=str(data.get("model") or self._model),
            raw_stop_reason=raw_stop,
            free=True,
        )

    # 📘 בפורמט הזה הפרמטרים של הכלי מגיעים כמחרוזת JSON, ולא כאובייקט. מפענחים בזהירות: מודלים
    # 📘 קטנים כותבים לפעמים JSON שבור, ואז הכלי יחזיר שגיאה והמודל ינסה שוב.
    def _parse_call(self, raw: dict[str, Any]) -> ToolCall:
        self._calls += 1
        function = raw.get("function") or {}
        arguments: Any = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = raw.get("id") or f"call_{self._calls}"  # local servers may omit ids
        return ToolCall(id=call_id, name=str(function.get("name", "")), arguments=arguments)


def _assistant_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in calls
        ]
    return message


class OpenAICompatProvider:
    def __init__(self, client: ChatCompletionsClient, settings: Settings, spec: str) -> None:
        _, self._model = split_model(spec)
        self._client = client
        self._settings = settings
        self.name = spec

    def new_conversation(
        self, system: str, tools: list[ToolSpec], history: History = ()
    ) -> ChatConversation:
        conversation = ChatConversation(self._client, self._settings, self._model, system, tools)
        conversation.replay(history)
        return conversation
