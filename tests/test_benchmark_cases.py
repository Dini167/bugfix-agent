"""Validates the benchmark itself: every case must be broken as shipped and
fixed by its reference fix, without touching any protected file."""

from __future__ import annotations

import pytest

from benchmark.catalog import Case, load_cases
from core.config import Settings
from core.guards import ProtectedSnapshot, is_protected
from core.sandbox import Workspace
from core.tools import Toolbox, run_test_suite

CASES = load_cases()


def test_there_are_ten_cases() -> None:
    assert len(CASES) == 10
    assert len({c.id for c in CASES}) == 10


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_fails_as_shipped(case: Case) -> None:
    with Workspace.from_source(case.repo) as ws:
        outcome = run_test_suite(ws, Settings())
    assert outcome.failed + outcome.errors > 0, "a benchmark case must start broken"
    assert outcome.passed > 0, "a case should also have passing tests that a bad fix can break"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_reference_fix_resolves_case(case: Case) -> None:
    fix = case.reference_fix
    assert not is_protected(fix.file)
    with Workspace.from_source(case.repo) as ws:
        snapshot = ProtectedSnapshot.take(ws.root)
        result = Toolbox(ws, Settings()).dispatch(
            "edit_file", {"path": fix.file, "old": fix.old, "new": fix.new}
        )
        assert not result.is_error, result.output
        outcome = run_test_suite(ws, Settings())
        assert outcome.all_passed, outcome.output
        assert snapshot.changed_files(ws.root) == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_ground_truth_is_not_in_workspace(case: Case) -> None:
    with Workspace.from_source(case.repo) as ws:
        assert not (ws.root / "case.yaml").exists()
        assert not any(p.name == "case.yaml" for p in ws.root.rglob("*"))
