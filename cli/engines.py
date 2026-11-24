"""Building either agent by name, so every entry point treats them the same way."""

# 📘 מקום אחד שיודע לבנות כל אחד משני האיגנטים לפי שם ("raw" או "sdk"). גם הפקודה bugfix וגם
# 📘 סקריפט ההשוואה משתמשים בו, כך ששניהם מריצים בדיוק את אותו דבר.

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

from core.config import Settings, is_free_model
from core.harness import Engine

if TYPE_CHECKING:
    from raw_agent.providers.base import ModelProvider

ENGINES = ("raw", "sdk")


class EngineSetupError(ValueError):
    """The requested engine cannot run with these settings."""


@contextmanager
def open_engine(name: str, settings: Settings) -> Iterator[Engine]:
    """Yield a ready engine; closes its resources (e.g. the HTTP clients) afterwards."""
    if name == "raw":
        from raw_agent.loop import RawEngine

        with open_provider(settings) as provider:
            yield RawEngine(provider)
    elif name == "sdk":
        # 📘 [עם SDK] ה-SDK עובד רק עם Claude, ולכן מודל חינמי או שרשרת גיבוי לא אפשריים בו.
        if is_free_model(settings.model):
            raise EngineSetupError(
                f"The SDK agent runs on Claude only; {settings.model!r} needs --engine raw."
            )
        from sdk_agent.agent import SdkEngine

        yield SdkEngine()
    else:
        raise EngineSetupError(f"unknown engine {name!r}; choose one of: {', '.join(ENGINES)}")


@contextmanager
def open_provider(settings: Settings) -> Iterator[ModelProvider]:
    """The raw agent's model chain on its own: the main model, then any backups."""
    from raw_agent.providers.fallback import FallbackProvider

    with ExitStack() as stack:
        # 📘 המודל הראשי ואחריו מודלי הגיבוי, לפי הסדר. אם יש רק מודל אחד, אין צורך בשרשרת.
        models = [settings.model, *settings.fallbacks]
        chain = [_provider(model, settings, stack) for model in models]
        yield chain[0] if len(chain) == 1 else FallbackProvider(chain)


def _provider(model: str, settings: Settings, stack: ExitStack) -> ModelProvider:
    from dataclasses import replace

    if is_free_model(model):
        from raw_agent.providers.openai_compat import (
            ChatCompletionsClient,
            OpenAICompatProvider,
            split_model,
        )

        host, _ = split_model(model)
        client = stack.enter_context(ChatCompletionsClient(settings, host))
        return OpenAICompatProvider(client, settings, model)

    from raw_agent.client import MessagesClient
    from raw_agent.providers.anthropic import AnthropicProvider

    claude = replace(settings, model=model)
    return AnthropicProvider(stack.enter_context(MessagesClient(claude)), claude)
