"""Building either agent by name, so every entry point treats them the same way."""

# 📘 מקום אחד שיודע לבנות כל אחד משני האיגנטים לפי שם ("raw" או "sdk"). גם הפקודה bugfix וגם
# 📘 סקריפט ההשוואה משתמשים בו, כך ששניהם מריצים בדיוק את אותו דבר.

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from core.config import Settings
from core.harness import Engine

ENGINES = ("raw", "sdk")


@contextmanager
def open_engine(name: str, settings: Settings) -> Iterator[Engine]:
    """Yield a ready engine; closes its resources (e.g. the HTTP client) afterwards."""
    if name == "raw":
        from raw_agent.client import MessagesClient
        from raw_agent.loop import RawEngine
        from raw_agent.providers.anthropic import AnthropicProvider

        with MessagesClient(settings) as client:
            yield RawEngine(AnthropicProvider(client, settings))
    elif name == "sdk":
        from sdk_agent.agent import SdkEngine

        yield SdkEngine()
    else:
        raise ValueError(f"unknown engine {name!r}; choose one of: {', '.join(ENGINES)}")
