"""The provider interface: the only thing the raw loop knows about a model.

The loop speaks in neutral terms (a turn has text, tool calls, a stop reason
and usage). Each provider translates that to and from its own wire format and
owns its own transcript, because providers differ in what must be sent back
(Anthropic, for example, requires assistant content, including thinking blocks,
to be returned unchanged).

Adding a provider means one new file implementing these two protocols. The
OpenAI-compatible provider (`openai_compat.py`) is the example: it serves free
models, and `fallback.py` chains providers so a run can continue on the next one
when the current one fails. The loop does not change.

Because a run can move between providers mid-way, every conversation can be
rebuilt from a provider-neutral `History` of what has been said so far.
"""

# 📘 [בלי SDK] זו "שכבת המודל המוחלפת". הלולאה מכירה רק את הממשק הזה, ולא יודעת איזה מודל עומד
# 📘 מאחוריו.
# 📘 בזכות זה חיברנו מודלים חינמיים (openai_compat.py) ושרשרת גיבוי (fallback.py), בלי לשנות שורה
# 📘 בלולאה.
# 📘 [עם SDK] ב-Claude Agent SDK אין שכבה כזו, כי הוא עובד רק עם Claude.

from __future__ import annotations

from collections.abc import Sequence
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
    # True when the model is not billed (a free tier or a local model), so the
    # budget does not charge for its tokens.
    free: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


# 📘 "היסטוריה ניטרלית": מה נאמר בשיחה, בלי קשר לפורמט של ספק מסוים. אם צריך לעבור לספק אחר באמצע
# 📘 ריצה, בונים ממנה את השיחה מחדש בפורמט של הספק החדש.
@dataclass(frozen=True, slots=True)
class UserText:
    text: str


@dataclass(frozen=True, slots=True)
class AssistantReply:
    text: str
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class ToolResults:
    results: tuple[tuple[ToolCall, ToolResult], ...]


History = Sequence[UserText | AssistantReply | ToolResults]


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

    def new_conversation(
        self, system: str, tools: list[ToolSpec], history: History = ()
    ) -> Conversation:
        """Start a conversation, optionally replaying what was said so far."""
        ...
