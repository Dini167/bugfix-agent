"""Everything the web interface does, without the web part, so it can be tested.

The page in `ui/app.py` only draws; it calls these functions to list projects,
check them, run an agent and turn its events into readable steps.
"""

# 📘 ההיגיון של הממשק הגרפי, בלי הממשק עצמו. ככה אפשר לבדוק אותו בטסטים רגילים, והקובץ app.py רק
# 📘 מצייר את המסך וקורא לפונקציות כאן.

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.catalog import Case, load_cases
from cli.engines import open_engine
from cli.main import describe_tool_call
from core.config import Settings, is_free_model
from core.harness import run_case
from core.languages import UnsupportedRepository, detect_runner
from core.sandbox import Workspace
from core.tools import run_test_suite
from core.types import RunResult, RunStatus, TestOutcome

CLAUDE_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]

STATUS_TEXT = {
    RunStatus.RESOLVED: "Fixed",
    RunStatus.UNRESOLVED: "Not fixed: the tests still fail",
    RunStatus.BUDGET_EXCEEDED: "Stopped at the budget limit",
    RunStatus.TESTS_TAMPERED: "Rejected: it changed test files",
    RunStatus.ALREADY_PASSING: "Nothing to fix: all tests already pass",
    RunStatus.ERROR: "Stopped by an error",
}


@dataclass(frozen=True, slots=True)
class Step:
    """One line of the live view: what kind of step it is, and what to show."""

    kind: str  # "info", "think", "tool", "warn"
    text: str


def sample_cases() -> list[Case]:
    return load_cases()


def sample_label(case: Case) -> str:
    return f"{case.id[:2]} · {case.title} ({case.difficulty})"


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
            f"{event.get('runner')}: {event.get('failing', 0)} failing, "
            f"{event.get('passed', 0)} passing",
        )
    if kind == "model_text":
        text = " ".join(str(event.get("text", "")).split())
        if text and not text.startswith("## Result"):
            return Step("think", text if len(text) <= 300 else text[:300] + "...")
        return None
    if kind == "tool_call":
        return Step("tool", describe_tool_call(event).lstrip("> "))
    if kind == "fallback":
        reason = str(event.get("error", "")).split(" | ")[0]
        return Step(
            "warn", f"{event.get('from')} failed ({reason}); continuing on {event.get('to')}"
        )
    if kind == "budget_exceeded":
        return Step("warn", f"Budget limit: {event.get('reason')}")
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
        ("Result", lambda r: STATUS_TEXT.get(r.status, r.status.value)),
        ("Tests passing", lambda r: f"{r.tests_passed}/{r.tests_total}"),
        ("Steps", lambda r: str(r.iterations)),
        ("Time", lambda r: f"{r.wall_time_s:.1f} s"),
        ("Cost", lambda r: f"${r.cost_usd:.4f}"),
        ("Tokens", lambda r: f"{r.usage.total_tokens:,}"),
    ]
    return [
        {"Measure": name, **{agent: show(result) for agent, result in results.items()}}
        for name, show in measures
    ]


def is_supported(repo: Path) -> str | None:
    """None if the folder can be checked, otherwise a message saying why not."""
    if not repo.is_dir():
        return f"Folder not found: {repo}"
    try:
        detect_runner(repo)
    except UnsupportedRepository as exc:
        return str(exc)
    return None
