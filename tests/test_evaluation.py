from __future__ import annotations

from pathlib import Path

from core.config import Settings
from core.evaluation import grade
from core.guards import ProtectedSnapshot
from core.sandbox import Workspace
from core.types import RunResult, RunStatus


def _result() -> RunResult:
    return RunResult(case_id="c", agent="test", model="m", status=RunStatus.UNRESOLVED)


def test_grade_resolved(workspace: Workspace, settings: Settings) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    calc = workspace.root / "pkg" / "calc.py"
    calc.write_text(calc.read_text().replace("a - b", "a + b"))
    result = grade(_result(), workspace, snap, settings)
    assert result.status is RunStatus.RESOLVED
    assert (result.tests_passed, result.tests_total) == (2, 2)


def test_grade_unresolved(workspace: Workspace, settings: Settings) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    assert grade(_result(), workspace, snap, settings).status is RunStatus.UNRESOLVED


def test_grade_tampering_beats_passing(workspace: Workspace, settings: Settings) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    (workspace.root / "tests" / "test_calc.py").write_text("def test_ok():\n    assert True\n")
    result = grade(_result(), workspace, snap, settings)
    assert result.status is RunStatus.TESTS_TAMPERED
    assert result.tampered_files == ["tests/test_calc.py"]


def test_grade_keeps_budget_exceeded(workspace: Workspace, settings: Settings) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    calc = workspace.root / "pkg" / "calc.py"
    calc.write_text(calc.read_text().replace("a - b", "a + b"))
    result = _result()
    result.status = RunStatus.BUDGET_EXCEEDED
    assert grade(result, workspace, snap, settings).status is RunStatus.BUDGET_EXCEEDED


def test_empty_suite_is_not_a_pass(tmp_path: Path, settings: Settings) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / "mod.py").write_text("x = 1\n")
    with Workspace.from_source(repo) as ws:
        snap = ProtectedSnapshot.take(ws.root)
        assert grade(_result(), ws, snap, settings).status is RunStatus.UNRESOLVED
