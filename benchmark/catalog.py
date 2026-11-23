"""Loading benchmark cases.

Each case lives in `benchmark/cases/<id>/` with two parts:

* `repo/` — the buggy repository. This is the only thing copied into the
  agent's workspace.
* `case.yaml` — metadata and ground truth (the real bug and a reference fix).
  Used for grading and for validating the benchmark itself; never shown to the
  agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass(frozen=True, slots=True)
class ReferenceFix:
    """A single exact replacement that fixes the case. Used to validate the benchmark."""

    file: str
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    title: str
    difficulty: str
    category: str
    description: str
    reference_fix: ReferenceFix
    path: Path

    @property
    def repo(self) -> Path:
        return self.path / "repo"


def load_case(path: Path) -> Case:
    meta: dict[str, Any] = yaml.safe_load((path / "case.yaml").read_text(encoding="utf-8"))
    truth = meta["ground_truth"]
    fix = truth["fix"]
    return Case(
        id=meta["id"],
        title=meta["title"],
        difficulty=meta["difficulty"],
        category=meta["category"],
        description=truth["description"],
        reference_fix=ReferenceFix(file=fix["file"], old=fix["old"], new=fix["new"]),
        path=path,
    )


def load_cases(root: Path = CASES_DIR, only: list[str] | None = None) -> list[Case]:
    """All cases under `root`, sorted by id, optionally filtered by id prefix."""
    cases = [load_case(p) for p in sorted(root.iterdir()) if (p / "case.yaml").is_file()]
    if only:
        cases = [c for c in cases if any(c.id.startswith(prefix) for prefix in only)]
    return cases
