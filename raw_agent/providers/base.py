"""The provider interface: the only thing the raw loop knows about a model.

The loop speaks in neutral terms (a turn has text, tool calls, a stop reason
and usage). Each provider translates that to and from its own wire format and
owns its own transcript, because providers differ in what must be sent back
(Anthropic, for example, requires assistant content, including thinking blocks,
to be returned unchanged).

Adding a provider (for instance an OpenAI-compatible endpoint serving a free
model) means one new file implementing these two protocols, plus a config value.
The loop does not change.
"""

# 📘 [בלי SDK] זו "שכבת המודל המוחלפת". הלולאה מכירה רק את הממשק הזה, ולא יודעת איזה מודל עומד
# 📘 מאחוריו.
# 📘 בזכות זה אפשר בעתיד לחבר מודל חינמי: כותבים קובץ אחד שמממש את שני הממשקים כאן, והלולאה לא
# 📘 משתנה.
# 📘 [עם SDK] ב-Claude Agent SDK אין שכבה כזו, כי הוא עובד רק עם Claude.

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from core.tools import ToolSpec
from core.types import ToolResult, Usage


# 📘 למה המודל הפסיק לכתוב: סיים (END_TURN), מבקש להפעיל כלי (TOOL_USE), נגמר לו המקום
# 📘 (MAX_TOKENS) או סירב (REFUSAL).
class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


# 📘 תשובה אחת של המודל, בפורמט אחיד שלא תלוי בספק.
@dataclass(slots=True)
class ModelTurn:
    """One model response, normalized."""

    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: Usage
    model: str = ""
    raw_stop_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# 📘 Protocol ב-Python הוא "חוזה": כל מחלקה שיש לה את המתודות האלה מתאימה, גם בלי לרשת ממנה.
class Conversation(Protocol):
    """A provider-owned transcript for one run."""

    def add_user_text(self, text: str) -> None: ...

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None: ...

    def send(self) -> ModelTurn:
        """Call the model on the current transcript and append its reply to it."""
        ...


class ModelProvider(Protocol):
    name: str

    def new_conversation(self, system: str, tools: list[ToolSpec]) -> Conversation: ...
