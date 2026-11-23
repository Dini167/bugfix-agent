"""Every tunable in one place.

Values come from defaults below, overridable by environment variables with the
`BUGFIX_` prefix (e.g. `BUGFIX_MODEL`, `BUGFIX_MAX_ITERATIONS`). Both agents
read the same `Settings`, so a benchmark run compares like with like.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Self


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# Anthropic first-party list prices (USD / 1M tokens). Verify against the
# published pricing page before relying on cost figures; update here only.
PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-5-5": ModelPricing(2.00, 10.00, 0.10, 2.50),
    "claude-opus-5-5": ModelPricing(4.00, 20.00, 0.20, 5.00),
    "claude-haiku-5-5": ModelPricing(0.10, 0.50, 0.01, 0.125),
}


@dataclass(frozen=True, slots=True)
class Settings:
    """Run configuration shared by both agents and the benchmark."""

    model: str = "claude-sonnet-5-5"
    max_tokens_per_response: int = 16_000
    # Reasoning depth. "medium" is the recommended starting point for agentic
    # coding on current Sonnet models; raise it if the benchmark shows headroom.
    effort: str = "medium"

    # Budgets: hard caps enforced in code by `core.guards.Budget`.
    max_iterations: int = 15
    max_total_tokens: int = 1_500_000
    max_cost_usd: float = 2.00

    # Sandbox
    command_timeout_s: float = 60.0
    test_timeout_s: float = 120.0

    # Tool output limits, so one noisy traceback cannot flood the context.
    max_tool_output_chars: int = 12_000
    max_search_matches: int = 80
    max_listed_files: int = 400

    # Raw client
    api_base_url: str = "https://api.anthropic.com"
    api_version: str = "2026-10-21"
    api_timeout_s: float = 300.0
    api_max_retries: int = 4
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0
    # Server-side fallback: if a safety classifier declines a request, the API
    # retries it on a fallback model instead of returning a refusal.
    refusal_fallback: bool = True

    @classmethod
    def from_env(cls, prefix: str = "BUGFIX_") -> Self:
        """Build settings from defaults, overridden by `<prefix><FIELD>` env vars."""
        base = cls()
        overrides: dict[str, object] = {}
        for f in fields(cls):
            raw = os.environ.get(f"{prefix}{f.name.upper()}")
            if raw is None:
                continue
            current = getattr(base, f.name)
            if isinstance(current, bool):
                overrides[f.name] = raw.strip().lower() in {"1", "true", "yes"}
            elif isinstance(current, int):
                overrides[f.name] = int(raw)
            elif isinstance(current, float):
                overrides[f.name] = float(raw)
            else:
                overrides[f.name] = raw
        return replace(base, **overrides)  # type: ignore[arg-type]

    @property
    def pricing(self) -> ModelPricing:
        try:
            return PRICING[self.model]
        except KeyError as exc:
            known = ", ".join(sorted(PRICING))
            raise ValueError(
                f"No pricing for model {self.model!r}. Add it to core.config.PRICING "
                f"(known: {known})."
            ) from exc
