"""What the agent changed: a diff between the original repository and the workspace.

Used to show the user the exact fix and, if they accept it, to apply it to
their real project. Only files the agent may edit are considered, so a change
can never touch tests or configuration.
"""

# 📘 אחרי שהאיגנט סיים, משווים את העותק למקור ומוציאים את השינויים המדויקים. כך המשתמש רואה בדיוק
# 📘 מה ישתנה, ומחליט אם להחיל את התיקון על הפרויקט האמיתי שלו.

from __future__ import annotations

import difflib
from pathlib import Path

from core.guards import is_protected
from core.types import FileChange

_SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "target",
        "build",
        "dist",
        ".gradle",
        ".next",
    }
)


def _text_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if not path.is_file() or any(p in _SKIP_DIRS for p in rel_parts):
            continue
        rel = path.relative_to(root).as_posix()
        if is_protected(rel):
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return files


def collect_changes(original: Path, workspace: Path) -> list[FileChange]:
    """Editable files whose content differs between the original and the workspace."""
    before, after = _text_files(original), _text_files(workspace)
    return [
        FileChange(path, before.get(path, ""), after.get(path, ""))
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
    ]


def unified_patch(changes: list[FileChange]) -> str:
    """A standard unified diff of all changes (the format `git apply` accepts)."""
    parts: list[str] = []
    for change in changes:
        parts.extend(
            difflib.unified_diff(
                change.before.splitlines(keepends=True),
                change.after.splitlines(keepends=True),
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
            )
        )
    return "".join(parts)


def apply_changes(repo: Path, changes: list[FileChange]) -> list[str]:
    """Write the changes into the user's repository, refusing if a file moved on since."""
    for change in changes:
        target = repo / change.path
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != change.before:
            raise RuntimeError(
                f"{change.path} changed since the agent started; not applying the fix."
            )
    for change in changes:
        target = repo / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.after, encoding="utf-8")
    return [c.path for c in changes]
