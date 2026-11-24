"""Validates the benchmark itself: every case must be broken as shipped and
fixed by its reference fix, without touching any protected file."""

from __future__ import annotations

import shutil

import pytest

from benchmark.catalog import Case, load_cases
from core.config import Settings
from core.guards import Budget, is_protected
from core.harness import EngineOutcome, run_case
from core.languages import detect_runner
from core.sandbox import Workspace
from core.telemetry import Tracer
from core.tools import Toolbox, run_test_suite
from core.types import RunStatus

CASES = load_cases()


def test_case_inventory() -> None:
    assert len(CASES) == 15
    assert len({c.id for c in CASES}) == 15


def _needs_toolchain(case: Case) -> None:
    """Skip a case whose compiler or test runner is not installed on this machine."""
    tool = {"cpp/cmake": "cmake", "csharp/dotnet": "dotnet"}.get(detect_runner(case.repo).name)
    if tool and not shutil.which(tool):
        pytest.skip(f"{tool} is not installed")


@pytest.mark.parametrize(
    ("case", "runner"),
    [
        (next(c for c in CASES if c.id.startswith("01")), "python/pytest"),
        (next(c for c in CASES if c.id.startswith("11")), "javascript/node"),
        (next(c for c in CASES if c.id.startswith("12")), "go/go-test"),
        (next(c for c in CASES if c.id.startswith("13")), "rust/cargo"),
        (next(c for c in CASES if c.id.startswith("14")), "cpp/cmake"),
        (next(c for c in CASES if c.id.startswith("15")), "csharp/dotnet"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.id,
)
def test_runner_detection(case: Case, runner: str) -> None:
    assert detect_runner(case.repo).name == runner


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_fails_as_shipped(case: Case) -> None:
    _needs_toolchain(case)
    with Workspace.from_source(case.repo) as ws:
        outcome = run_test_suite(ws, Settings())
    assert outcome.failed + outcome.errors > 0, "a benchmark case must start broken"
    assert outcome.passed > 0, "a case should also have passing tests that a bad fix can break"
    assert outcome.failures, "each failing test should be reported with its message"
    assert all(f.message for f in outcome.failures)


class ReferenceEngine:
    """An "agent" that applies the case's known fix, to validate the case and the harness."""

    name = "reference"

    def __init__(self, case: Case) -> None:
        self.case = case

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        fix = self.case.reference_fix
        result = toolbox.dispatch("edit_file", {"path": fix.file, "old": fix.old, "new": fix.new})
        assert not result.is_error, result.output
        return EngineOutcome(RunStatus.UNRESOLVED, final_report="applied reference fix")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_reference_fix_resolves_case(case: Case) -> None:
    _needs_toolchain(case)
    assert not is_protected(case.reference_fix.file)
    result = run_case(case.id, case.repo, ReferenceEngine(case), Settings())
    assert result.status is RunStatus.RESOLVED, result
    assert result.tests_failing_before > 0
    assert result.tampered_files == []
    assert [c.path for c in result.changes] == [case.reference_fix.file]
    assert f"+++ b/{case.reference_fix.file}" in result.patch


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_ground_truth_is_not_in_workspace(case: Case) -> None:
    with Workspace.from_source(case.repo) as ws:
        assert not (ws.root / "case.yaml").exists()
        assert not any(p.name == "case.yaml" for p in ws.root.rglob("*"))
