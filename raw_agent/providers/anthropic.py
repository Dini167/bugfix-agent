"""Anthropic Messages API provider, built on the hand-written `MessagesClient`.

Wire-format responsibilities handled here, by hand:

* tool definitions in the API's `{name, description, input_schema}` shape;
* appending the assistant's full `content` back to the transcript unchanged
  (thinking blocks included), which the API requires;
* returning every `tool_result` for a turn in a single user message, with
  `is_error` set on failures;
* prompt caching via top-level `cache_control`, so each iteration re-reads the
  growing transcript at cache prices instead of full input prices;
* normalizing `stop_reason` and `usage`.
"""

# 📘 [בלי SDK] כאן מתרגמים בין הפורמט האחיד שלנו לבין הפורמט המדויק ש-Anthropic מצפה לו.
# 📘 [עם SDK] ה-SDK מכיר את הפורמט הזה בעצמו, ואנחנו לא כותבים ממנו אף שורה.

from __future__ import annotations

import json
from typing import Any

from core.config import Settings
from core.tools import ToolSpec
from core.types import ToolResult, Usage
from raw_agent.client import MessagesClient
from raw_agent.providers.base import ModelTurn, StopReason, ToolCall

_STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}


# 📘 ממירים את הגדרת הכלי הכללית שלנו למבנה שה-API מקבל: שם, תיאור ו-JSON Schema של הפרמטרים.
def to_api_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"name": s.name, "description": s.description, "input_schema": s.input_schema}
        for s in specs
    ]


class AnthropicConversation:
    def __init__(
        self, client: MessagesClient, settings: Settings, system: str, tools: list[ToolSpec]
    ) -> None:
        self._client = client
        self._settings = settings
        self._system = system
        self._tools = to_api_tools(tools)
        self.messages: list[dict[str, Any]] = []

    def add_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": [{"type": "text", "text": text}]})

    # 📘 חוק של ה-API: כל תוצאות הכלים של אותו סבב חוזרות בהודעה אחת, וכל תוצאה מצביעה על הבקשה
    # 📘 שלה לפי tool_use_id.
    # 📘 אם כלי נכשל, מסמנים is_error, כדי שהמודל ידע שזו שגיאה ולא פלט רגיל.
    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        blocks: list[dict[str, Any]] = []
        for call, result in results:
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result.output or "(no output)",
            }
            if result.is_error:
                block["is_error"] = True
            blocks.append(block)
        # All results for one turn go in one user message.
        self.messages.append({"role": "user", "content": blocks})

    # 📘 גוף הבקשה. effort קובע כמה המודל "חושב" לפני שהוא עונה. cache_control מפעיל מטמון, כך
    # 📘 שבכל סבב השיחה הקודמת נקראת במחיר זול בהרבה.
    # 📘 fallbacks: אם המודל מסרב מסיבות בטיחות, השרת מנסה מודל חלופי במקום להחזיר סירוב.
    def request_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._settings.model,
            "max_tokens": self._settings.max_tokens_per_response,
            "system": self._system,
            "tools": self._tools,
            "messages": self.messages,
            "output_config": {"effort": self._settings.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if self._settings.refusal_fallback:
            body["fallbacks"] = "default"
        return body

    def send(self) -> ModelTurn:
        data = self._client.create_message(self.request_body())
        content: list[dict[str, Any]] = data.get("content", [])
        # Return the assistant content exactly as received.
        # 📘 חוק חשוב: את מה שהמודל ענה מחזירים לשיחה בדיוק כמו שהוא, כולל בלוקים של "חשיבה".
        # 📘 שינוי שלהם יגרום לשגיאה.
        self.messages.append({"role": "assistant", "content": content})

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text", ""))
            elif kind == "tool_use":
                arguments = block.get("input", {})
                # 📘 תמיד מפענחים JSON בעזרת json.loads, ולא בהשוואת טקסט.
                if isinstance(arguments, str):  # defensive: never string-match JSON
                    arguments = json.loads(arguments)
                calls.append(ToolCall(id=block["id"], name=block["name"], arguments=arguments))

        raw_stop = data.get("stop_reason")
        usage = data.get("usage", {})
        return ModelTurn(
            text="\n".join(t for t in texts if t).strip(),
            tool_calls=calls,
            stop_reason=_STOP_REASONS.get(raw_stop or "", StopReason.OTHER),
            usage=Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
                cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            ),
            model=str(data.get("model", "")),
            raw_stop_reason=raw_stop,
            extra={"stop_details": data.get("stop_details"), "id": data.get("id")},
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: MessagesClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def new_conversation(self, system: str, tools: list[ToolSpec]) -> AnthropicConversation:
        return AnthropicConversation(self._client, self._settings, system, tools)
