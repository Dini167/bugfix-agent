"""A chain of providers: when one fails, the run continues on the next.

The chain is tried in order. A provider is abandoned on any API error that its
client could not recover from by retrying: no credit left, a spend cap, an
invalid key, an outage that outlasted the retries, an unknown model. The
conversation so far is replayed to the next provider, so the run continues from
where it stopped instead of starting over. Once a run has moved on, it stays on
the new provider.
"""

# 📘 [בלי SDK] שרשרת גיבוי. אם Claude נכשל (נגמר הקרדיט, תקלה בשרת, מפתח לא תקין), הריצה ממשיכה
# 📘 אצל המודל הבא ברשימה, מאותה נקודה ולא מההתחלה. זה אפשרי רק כי את הלולאה כתבנו בעצמנו.
# 📘 [עם SDK] ב-SDK יש רק גיבוי בתוך Claude עצמו (מודל Claude אחר במקרה של סירוב). אי אפשר לעבור
# 📘 ממנו לספק אחר.

from __future__ import annotations

from collections.abc import Sequence

from core.tools import ToolSpec
from core.types import ToolResult
from raw_agent.client import APIError
from raw_agent.providers.base import (
    AssistantReply,
    Conversation,
    History,
    ModelProvider,
    ModelTurn,
    ToolCall,
    ToolResults,
    UserText,
)


class FallbackConversation:
    def __init__(
        self,
        providers: Sequence[ModelProvider],
        system: str,
        tools: list[ToolSpec],
        history: History,
    ) -> None:
        self._providers = providers
        self._system = system
        self._tools = tools
        # 📘 שומרים עותק ניטרלי של כל השיחה, כדי שאפשר יהיה לבנות אותה מחדש אצל הספק הבא.
        self._history: list[UserText | AssistantReply | ToolResults] = list(history)
        self._index = 0
        self._current: Conversation = providers[0].new_conversation(system, tools, history)

    @property
    def provider(self) -> ModelProvider:
        return self._providers[self._index]

    def add_user_text(self, text: str) -> None:
        self._history.append(UserText(text))
        self._current.add_user_text(text)

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        self._history.append(ToolResults(tuple(results)))
        self._current.add_tool_results(results)

    def send(self) -> ModelTurn:
        skipped: list[dict[str, str]] = []
        while True:
            try:
                turn = self._current.send()
            except APIError as exc:
                if self._index + 1 >= len(self._providers):
                    raise
                # 📘 הספק הנוכחי נכשל. עוברים לבא בתור, ובונים אצלו את השיחה מחדש מההיסטוריה.
                skipped.append(
                    {
                        "from": self.provider.name,
                        "to": self._providers[self._index + 1].name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                self._index += 1
                self._current = self.provider.new_conversation(
                    self._system, self._tools, self._history
                )
                continue
            self._history.append(AssistantReply(turn.text, tuple(turn.tool_calls)))
            turn.extra["provider"] = self.provider.name
            if skipped:
                turn.extra["fallbacks"] = skipped
            return turn


class FallbackProvider:
    """Behaves like one provider; tries each of `providers` in order."""

    def __init__(self, providers: Sequence[ModelProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self._providers = list(providers)
        self.name = " -> ".join(p.name for p in self._providers)

    def new_conversation(
        self, system: str, tools: list[ToolSpec], history: History = ()
    ) -> FallbackConversation:
        return FallbackConversation(self._providers, system, tools, history)
