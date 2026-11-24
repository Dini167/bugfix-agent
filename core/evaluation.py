"""Grading a finished run, independently of anything the agent claims.

A case counts as resolved only if all of the following hold:

1. The run ended normally (not on a budget limit or an error).
2. No protected file (tests, conftest, pytest config) changed.
3. The full test suite passes, re-run here by the grader.

The agent's own "RESOLVED" in its report is recorded but never trusted.
"""

# 📘 הבודק העצמאי: הוא מריץ את הטסטים מחדש בעצמו. מה שהמודל כתב בדוח לא משפיע על הציון.

from __future__ import annotations

from core.config import Settings
from core.guards import ProtectedSnapshot
from core.sandbox import Workspace
from core.tools import run_test_suite
from core.types import RunResult, RunStatus


def grade(
    result: RunResult,
    workspace: Workspace,
    snapshot: ProtectedSnapshot,
    settings: Settings,
) -> RunResult:
    """Fill in test counts and the final status. Mutates and returns `result`."""
    result.tampered_files = snapshot.changed_files(workspace.root)
    outcome = run_test_suite(workspace, settings)
    result.tests_passed = outcome.passed
    result.tests_total = outcome.total

    if result.tampered_files:
        result.status = RunStatus.TESTS_TAMPERED
    elif result.status in (RunStatus.BUDGET_EXCEEDED, RunStatus.ERROR):
        pass  # keep the terminal status the agent loop recorded
    elif outcome.all_passed:
        result.status = RunStatus.RESOLVED
    else:
        result.status = RunStatus.UNRESOLVED
    return result
