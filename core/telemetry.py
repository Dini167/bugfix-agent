"""Structured JSONL tracing of every model call and tool call.

One line per event, so traces can be grepped, diffed between the two agents,
and loaded into any analysis tool. Never pass secrets (API keys, headers) in.
"""

# 📘 לוג: כל קריאה למודל וכל הפעלת כלי נכתבות כשורת JSON, עם זמן, טוקנים ועלות.

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO


class Tracer:
    """Appends JSON events to a file and/or hands them to a listener (e.g. the CLI)."""

    def __init__(
        self,
        path: Path | None,
        listener: Callable[[dict[str, Any]], None] | None = None,
        **context: Any,
    ) -> None:
        self._context = context
        self._listener = listener
        self._file: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def event(self, kind: str, **fields: Any) -> None:
        if self._file is None and self._listener is None:
            return
        record = {"ts": time.time(), "kind": kind, **self._context, **fields}
        if self._listener is not None:
            self._listener(record)  # live progress display
        if self._file is not None:
            self._file.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
            self._file.flush()

    @contextmanager
    def span(self, kind: str, **fields: Any) -> Iterator[dict[str, Any]]:
        """Time a block; anything added to the yielded dict is recorded with it."""
        extra: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            yield extra
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            self.event(kind, latency_ms=latency_ms, **fields, **extra)
