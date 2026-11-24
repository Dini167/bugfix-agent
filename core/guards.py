"""Policy enforcement that does not depend on the model's cooperation.

The system prompt tells the agent not to touch tests and to stay within budget.
This module is what actually guarantees it:

* `is_protected` blocks edits to test files and pytest configuration at the
  tool layer.
* `ProtectedSnapshot` hashes every protected file before a run and verifies
  them afterwards, catching tampering by any route (including ones we did not
  anticipate).
* `Budget` caps iterations, tokens and cost.
"""

# 📘 ההגנות שלא תלויות בכך שהמודל "יקשיב": גם אם ינסה, הקוד לא ייתן לו.

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Self

from core.config import ModelPricing, Settings
from core.types import Usage

# Files that define what "correct" means. Editing any of them could make a
# failing suite pass without fixing the bug, so they are off limits.
_PROTECTED_NAMES = frozenset(
    {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
)
_PROTECTED_DIRS = frozenset({"tests", "test"})


# 📘 אילו קבצים אסור לגעת בהם: טסטים וקבצי ההגדרות של pytest. שינוי שלהם היה מאפשר "לרמות".
def is_protected(relative_path: str) -> bool:
    """Return True if the agent must not create, edit or delete this path."""
    path = PurePosixPath(relative_path)
    name = path.name
    if name in _PROTECTED_NAMES:
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return any(part in _PROTECTED_DIRS for part in path.parts[:-1])


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# 📘 לפני הריצה מחשבים hash לכל קובץ מוגן, ובסוף משווים. כך נתפס כל שינוי, גם בדרך שלא חשבנו
# 📘 עליה.
@dataclass(frozen=True, slots=True)
class ProtectedSnapshot:
    """Content hashes of every protected file in a workspace."""

    hashes: dict[str, str]

    @classmethod
    def take(cls, root: Path) -> Self:
        return cls(_scan(root))

    def changed_files(self, root: Path) -> list[str]:
        """Protected files that were modified, added or deleted since the snapshot."""
        current = _scan(root)
        changed = {
            name
            for name in self.hashes.keys() | current.keys()
            if self.hashes.get(name) != current.get(name)
        }
        return sorted(changed)


def _scan(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if is_protected(rel):
            result[rel] = _hash(path)
    return result


class BudgetExceeded(RuntimeError):
    """Raised when a run crosses one of its hard limits."""


def estimate_cost(usage: Usage, pricing: ModelPricing) -> float:
    """Estimated USD cost of the given usage at list prices."""
    per_token = 1 / 1_000_000
    return (
        usage.input_tokens * pricing.input_per_mtok
        + usage.output_tokens * pricing.output_per_mtok
        + usage.cache_read_input_tokens * pricing.cache_read_per_mtok
        + usage.cache_creation_input_tokens * pricing.cache_write_per_mtok
    ) * per_token


# 📘 שלוש תקרות: מספר סבבים, מספר טוקנים ועלות בדולרים.
@dataclass(slots=True)
class Budget:
    """Tracks spend for one run and enforces the configured caps."""

    settings: Settings
    iterations: int = 0
    usage: Usage = field(default_factory=Usage)

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.usage, self.settings.pricing)

    def record_iteration(self) -> None:
        """Count one model turn. Raises before a turn that would exceed the cap."""
        if self.iterations >= self.settings.max_iterations:
            raise BudgetExceeded(f"iteration limit reached ({self.settings.max_iterations})")
        self.iterations += 1

    def record_usage(self, usage: Usage) -> None:
        """Add a model call's usage, then raise if a token or cost cap is crossed."""
        self.usage.add(usage)
        if self.usage.total_tokens > self.settings.max_total_tokens:
            raise BudgetExceeded(
                f"token limit exceeded ({self.usage.total_tokens:,} > "
                f"{self.settings.max_total_tokens:,})"
            )
        if self.cost_usd > self.settings.max_cost_usd:
            raise BudgetExceeded(
                f"cost limit exceeded (${self.cost_usd:.4f} > ${self.settings.max_cost_usd:.2f})"
            )
