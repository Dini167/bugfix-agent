"""Everything the web interface does, without the web part, so it can be tested.

The page in `ui/app.py` only draws; it calls these functions to list projects,
check them, run an agent and turn its events into readable steps.
"""

# 📘 ההיגיון של הממשק הגרפי, בלי הממשק עצמו. ככה אפשר לבדוק אותו בטסטים רגילים, והקובץ app.py רק
# 📘 מצייר את המסך וקורא לפונקציות כאן.

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.catalog import Case, load_cases
from cli.engines import open_engine
from core.config import Settings, is_free_model
from core.harness import run_case
from core.languages import UnsupportedRepository, detect_runner
from core.sandbox import Workspace
from core.tools import run_test_suite
from core.types import FileChange, RunResult, TestOutcome
from ui import texts

CLAUDE_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]

STATUS_TEXT = texts.STATUS


@dataclass(frozen=True, slots=True)
class Step:
    """One line of the live view: what kind of step it is, and what to show."""

    kind: str  # "info", "think", "tool", "warn"
    text: str


def sample_cases() -> list[Case]:
    return load_cases()


def sample_label(case: Case) -> str:
    number = case.id[:2]
    name = texts.CASE_NAMES.get(number, case.title)
    language = texts.LANGUAGES.get(number, "Python")
    return f"{number}. {name} · {language} · {texts.DIFFICULTY.get(case.difficulty, '')}"


def check_project(repo: Path, settings: Settings) -> tuple[str, TestOutcome]:
    """Detect the runner and run the tests once, on a private copy."""
    runner = detect_runner(repo)
    with Workspace.from_source(repo) as ws:
        return runner.name, run_test_suite(ws, settings)


def missing_key(settings: Settings) -> str | None:
    """The environment variable a run needs but does not have, if any."""
    from raw_agent.providers.openai_compat import HOSTS, split_model

    for model in [settings.model, *settings.fallbacks]:
        needed = (
            HOSTS[split_model(model)[0]].key_env if is_free_model(model) else "ANTHROPIC_API_KEY"
        )
        if needed and not os.environ.get(needed):
            return needed
    return None


# 📘 מתרגמים כל אירוע של האיגנט (הרצת כלי, מחשבה, מעבר לגיבוי) לשורה קצרה וברורה למסך.
def to_step(event: dict[str, Any]) -> Step | None:
    kind = event.get("kind")
    if kind == "baseline":
        return Step(
            "info",
            f"מצב התחלתי: {event.get('failing', 0)} טסטים נכשלים, {event.get('passed', 0)} עוברים",
        )
    if kind == "model_text":
        text = " ".join(str(event.get("text", "")).split())
        if text and not text.startswith("## Result"):
            return Step("think", text if len(text) <= 300 else text[:300] + "...")
        return None
    if kind == "tool_call":
        return Step("tool", texts.tool_step(event))
    if kind == "fallback":
        return Step("warn", f"המודל {event.get('from')} נכשל, ממשיכים עם {event.get('to')}")
    if kind == "budget_exceeded":
        return Step("warn", "נעצר: הגיע לתקרת התקציב")
    return None


def run_agent(
    engine: str, repo: Path, settings: Settings, on_step: Callable[[Step], None]
) -> RunResult:
    """Run one agent on a private copy of `repo`, reporting each step as it happens."""

    def listener(event: dict[str, Any]) -> None:
        step = to_step(event)
        if step:
            on_step(step)

    with open_engine(engine, settings) as agent:
        return run_case(repo.name, repo, agent, settings, listener=listener)


def comparison_rows(results: dict[str, RunResult]) -> list[dict[str, str]]:
    """One row per measure, one column per agent, for the side-by-side table."""
    measures: list[tuple[str, Callable[[RunResult], str]]] = [
        ("תוצאה", lambda r: STATUS_TEXT.get(r.status, r.status.value)),
        ("טסטים שעוברים", lambda r: f"{r.tests_passed} מתוך {r.tests_total}"),
        ("צעדים", lambda r: str(r.iterations)),
        ("זמן", lambda r: f"{r.wall_time_s:.1f} שניות"),
        ("עלות", lambda r: f"${r.cost_usd:.4f}"),
        ("טוקנים", lambda r: f"{r.usage.total_tokens:,}"),
    ]
    return [
        {"": name, **{agent: show(result) for agent, result in results.items()}}
        for name, show in measures
    ]


def is_supported(repo: Path) -> str | None:
    """None if the folder can be checked, otherwise a message saying why not."""
    if not repo.is_dir():
        return f"לא מצאתי את התיקייה: {repo}"
    try:
        detect_runner(repo)
    except UnsupportedRepository:
        return (
            "לא הצלחתי לזהות איך מריצים את הטסטים בתיקייה הזאת. נתמכות: Python, JavaScript, Go, "
            "Rust ו-Java. לשפה אחרת, מוסיפים לתיקייה קובץ bugfix.yaml עם הפקודה שמריצה את הטסטים."
        )
    return None


# 📘 העלאת תיקייה מהדפדפן: הדפדפן שולח כל קובץ עם הנתיב שלו בתוך התיקייה (למשל
# 📘 "my-project/src/app.py"). שומרים אותם בתיקייה זמנית, ובודקים שאף נתיב לא יוצא החוצה.
SKIPPED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv"}


def _safe_parts(name: str) -> list[str] | None:
    name = name.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if name.startswith("/") or not parts or ".." in parts or ":" in parts[0]:
        return None
    return parts


def save_upload(files: Iterable[tuple[str, bytes]], dest: Path) -> Path:
    """Write uploaded files under `dest` and return the project folder.

    Each name is the file's path inside the folder the user picked, starting with
    that folder's name. Paths that would leave `dest` are refused.
    """
    items = [(parts, data) for name, data in files if (parts := _safe_parts(name))]
    if not items:
        raise ValueError("לא התקבלו קבצים מהתיקייה.")
    tops = {parts[0] for parts, _ in items}
    strip = len(tops) == 1 and all(len(parts) > 1 for parts, _ in items)
    project = dest / (tops.pop() if strip else "project")
    for parts, data in items:
        relative = parts[1:] if strip else parts
        if SKIPPED_PARTS.intersection(relative):
            continue
        target = project.joinpath(*relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return project


def fixed_project_zip(repo: Path, changes: list[FileChange]) -> bytes:
    """The whole project as a zip file, with the agent's fix applied."""
    fixed = {c.path: c.after for c in changes}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(repo.rglob("*")):
            relative = path.relative_to(repo)
            if not path.is_file() or SKIPPED_PARTS.intersection(relative.parts):
                continue
            name = relative.as_posix()
            if name in fixed:
                archive.writestr(f"{repo.name}/{name}", fixed.pop(name).encode("utf-8"))
            else:
                archive.write(path, f"{repo.name}/{name}")
        for name, after in fixed.items():  # files the fix created
            archive.writestr(f"{repo.name}/{name}", after.encode("utf-8"))
    return buffer.getvalue()
