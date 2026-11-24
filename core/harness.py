"""The run harness: everything about a run that is not the agent loop.

Both agents plug in as an `Engine`. The harness owns the workspace, the
protected-file snapshot, the budget, timing, tracing and grading, so the only
code that differs between the two implementations is the orchestration itself.
That is what makes the comparison controlled.
"""

# 📘 הקובץ שהופך את ההשוואה להוגנת: כל מה שקורה מסביב ללולאה (עותק הפרויקט, ההגנות, התקציב, הלוג
# 📘 והציון) נמצא כאן ומשותף לשני האיגנטים.
# 📘 ההבדל היחיד בין האיגנטים הוא ה-Engine שמועבר ל-run_case.

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from core.changes import collect_changes, unified_patch
from core.config import Settings
from core.evaluation import grade
from core.guards import Budget, ProtectedSnapshot
from core.sandbox import Workspace
from core.telemetry import Tracer
from core.tools import Toolbox, run_test_suite
from core.types import RunResult, RunStatus


@dataclass(slots=True)
class EngineOutcome:
    """What an engine reports when its loop ends."""

    status: RunStatus
    final_report: str = ""
    error: str | None = None


class Engine(Protocol):
    """An agent loop. Implementations must stay inside the given toolbox and budget."""

    name: str

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome: ...


# 📘 הסדר: יוצרים עותק של הפרויקט, מריצים פעם אחת את הטסטים כדי לראות מה נכשל, "מצלמים" את קבצי
# 📘 הטסט, מריצים את האיגנט, בודקים בעצמנו את התוצאה, ומוציאים את השינויים המדויקים.
def run_case(
    case_id: str,
    repo: Path,
    engine: Engine,
    settings: Settings,
    trace_path: Path | None = None,
    listener: Callable[[dict[str, Any]], None] | None = None,
) -> RunResult:
    """Run one engine on one repository and return the graded result.

    The repository itself is never modified; the result carries the changes so
    the caller can show them and apply them on request.
    """
    with (
        Workspace.from_source(repo) as workspace,
        Tracer(trace_path, listener, case_id=case_id, agent=engine.name) as tracer,
    ):
        # A baseline run shows what is failing, warms build caches, and lets the
        # runner create files it always creates (lock files) before the snapshot.
        baseline = run_test_suite(workspace, settings)
        tracer.event(
            "baseline",
            runner=baseline.runner,
            passed=baseline.passed,
            failing=baseline.failed + baseline.errors,
        )
        snapshot = ProtectedSnapshot.take(workspace.root)
        if baseline.all_passed:
            result = RunResult(
                case_id=case_id,
                agent=engine.name,
                model=settings.model,
                status=RunStatus.ALREADY_PASSING,
                tests_passed=baseline.passed,
                tests_total=baseline.total,
                runner=baseline.runner,
            )
            tracer.event("run_end", **result.to_dict())
            return result

        budget = Budget(settings)
        toolbox = Toolbox(workspace, settings)
        tracer.event("run_start", model=settings.model)

        started = time.perf_counter()
        try:
            outcome = engine.solve(toolbox, budget, tracer)
        # 📘 אם יש באג באיגנט עצמו, הריצה נחשבת כושלת, אבל שאר ההשוואה ממשיכה.
        except Exception as exc:  # an engine bug is a failed run, not a crashed benchmark
            outcome = EngineOutcome(RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started

        result = RunResult(
            case_id=case_id,
            agent=engine.name,
            model=settings.model,
            status=outcome.status,
            iterations=budget.iterations,
            usage=budget.usage,
            cost_usd=round(budget.cost_usd, 6),
            wall_time_s=round(elapsed, 2),
            final_report=outcome.final_report,
            error=outcome.error,
            tests_failing_before=baseline.failed + baseline.errors,
            runner=baseline.runner,
        )
        grade(result, workspace, snapshot, settings)
        result.changes = collect_changes(repo, workspace.root)
        result.patch = unified_patch(result.changes)
        tracer.event("run_end", **result.to_dict())
        return result
