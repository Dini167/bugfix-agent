"""Data types shared by both agents, the guards, and the benchmark.

Both agent implementations return the same `RunResult`, which is what lets the
benchmark treat them interchangeably and compare them fairly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool hands back to the model.

    Tools never raise into the agent loop. A failure is reported as
    `is_error=True` with a message the model can act on, so one bad call costs
    one iteration instead of crashing the run.
    """

    output: str
    is_error: bool = False


@dataclass(slots=True)
class Usage:
    """Token usage accumulated across model calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass(frozen=True, slots=True)
class TestOutcome:
    """The result of one full run of a repository's test suite."""

    passed: int
    failed: int
    errors: int
    skipped: int
    exit_code: int
    timed_out: bool
    output: str

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def all_passed(self) -> bool:
        """True only for a clean, non-empty, fully passing run.

        An empty suite or a run where pytest itself failed is never a pass:
        otherwise deleting every test would count as a fix.
        """
        return (
            not self.timed_out
            and self.exit_code == 0
            and self.failed == 0
            and self.errors == 0
            and self.passed > 0
        )


class RunStatus(StrEnum):
    """How a single agent run ended."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    BUDGET_EXCEEDED = "budget_exceeded"
    TESTS_TAMPERED = "tests_tampered"
    ERROR = "error"


@dataclass(slots=True)
class RunResult:
    """The outcome of one agent run on one benchmark case."""

    case_id: str
    agent: str
    model: str
    status: RunStatus
    iterations: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    tests_passed: int = 0
    tests_total: int = 0
    tampered_files: list[str] = field(default_factory=list)
    final_report: str = ""
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status is RunStatus.RESOLVED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data
