from __future__ import annotations

import pytest

from core.config import Settings
from core.guards import Budget, BudgetExceeded, ProtectedSnapshot, estimate_cost, is_protected
from core.sandbox import Workspace
from core.types import Usage


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_calc.py",
        "tests/helpers.py",
        "pkg/test_inline.py",
        "pkg/calc_test.py",
        "conftest.py",
        "pkg/conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
    ],
)
def test_protected_paths(path: str) -> None:
    assert is_protected(path)


@pytest.mark.parametrize("path", ["pkg/calc.py", "pkg/testing_utils.py", "README.md", "contest.py"])
def test_unprotected_paths(path: str) -> None:
    assert not is_protected(path)


def test_snapshot_detects_modification(workspace: Workspace) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    (workspace.root / "tests" / "test_calc.py").write_text("def test_x():\n    pass\n")
    assert snap.changed_files(workspace.root) == ["tests/test_calc.py"]


def test_snapshot_detects_added_and_deleted(workspace: Workspace) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    (workspace.root / "conftest.py").write_text("collect_ignore = ['tests']\n")
    (workspace.root / "tests" / "test_calc.py").unlink()
    assert snap.changed_files(workspace.root) == ["conftest.py", "tests/test_calc.py"]


def test_snapshot_ignores_source_changes(workspace: Workspace) -> None:
    snap = ProtectedSnapshot.take(workspace.root)
    (workspace.root / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    assert snap.changed_files(workspace.root) == []


def test_budget_iteration_cap() -> None:
    budget = Budget(Settings(max_iterations=2))
    budget.record_iteration()
    budget.record_iteration()
    with pytest.raises(BudgetExceeded, match="iteration"):
        budget.record_iteration()


def test_budget_token_cap() -> None:
    budget = Budget(Settings(max_total_tokens=1000))
    budget.record_usage(Usage(input_tokens=600))
    with pytest.raises(BudgetExceeded, match="token"):
        budget.record_usage(Usage(input_tokens=500))


def test_budget_cost_cap() -> None:
    budget = Budget(Settings(model="claude-sonnet-5-5", max_cost_usd=0.01))
    with pytest.raises(BudgetExceeded, match="cost"):
        budget.record_usage(Usage(output_tokens=2_000))  # $0.02 at $10/MTok


def test_estimate_cost() -> None:
    pricing = Settings(model="claude-sonnet-5-5").pricing
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000)
    assert estimate_cost(usage, pricing) == pytest.approx(2.0 + 1.0)


def test_unknown_model_pricing_raises() -> None:
    with pytest.raises(ValueError, match="No pricing"):
        _ = Settings(model="not-a-model").pricing


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUGFIX_MAX_ITERATIONS", "7")
    monkeypatch.setenv("BUGFIX_MAX_COST_USD", "0.5")
    monkeypatch.setenv("BUGFIX_MODEL", "claude-opus-5-5")
    s = Settings.from_env()
    assert (s.max_iterations, s.max_cost_usd, s.model) == (7, 0.5, "claude-opus-5-5")


def test_report_language_in_the_shared_prompt() -> None:
    from core.prompts import SYSTEM_PROMPT, system_prompt

    hebrew = system_prompt(Settings())
    assert hebrew.startswith(SYSTEM_PROMPT) and "in Hebrew" in hebrew
    assert "## Root cause" in hebrew and "## Prevention" in hebrew
    assert system_prompt(Settings(report_language="English")) == SYSTEM_PROMPT
