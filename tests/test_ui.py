"""The browser interface: its logic directly, and the page through Streamlit's test runner."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from benchmark.catalog import load_cases
from core.config import Settings
from core.guards import Budget
from core.harness import EngineOutcome, run_case
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import RunResult, RunStatus
from ui import logic

CASE = next(c for c in load_cases() if c.id == "01_off_by_one")
APP = Path(__file__).resolve().parent.parent / "ui" / "app.py"


class ReferenceEngine:
    name = "reference"

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        fix = CASE.reference_fix
        tracer.event("model_text", text="The loop starts at index 1.")
        toolbox.dispatch("edit_file", {"path": fix.file, "old": fix.old, "new": fix.new})
        tracer.event("tool_call", tool="edit_file", arguments={"path": fix.file}, is_error=False)
        return EngineOutcome(RunStatus.UNRESOLVED, final_report="## Result\nRESOLVED")


def fake_run_agent(
    engine: str, repo: Path, settings: Settings, on_step: Callable[[logic.Step], None]
) -> RunResult:
    def listener(event: dict[str, object]) -> None:
        step = logic.to_step(dict(event))
        if step:
            on_step(step)

    return run_case(repo.name, repo, ReferenceEngine(), settings, listener=listener)


def test_events_become_readable_steps() -> None:
    assert logic.to_step({"kind": "baseline", "runner": "python/pytest", "failing": 2}) == (
        logic.Step("info", "python/pytest: 2 failing, 0 passing")
    )
    tool = logic.to_step({"kind": "tool_call", "tool": "read_file", "arguments": {"path": "a.py"}})
    assert tool == logic.Step("tool", "reading a.py")
    assert logic.to_step({"kind": "model_text", "text": "## Result\nRESOLVED"}) is None
    assert logic.to_step({"kind": "model_call"}) is None
    warn = logic.to_step({"kind": "fallback", "from": "a", "to": "b", "error": "X: no | status=1"})
    assert warn is not None and warn.kind == "warn" and "continuing on b" in warn.text


def test_check_and_samples() -> None:
    assert len(logic.sample_cases()) == 13
    assert logic.sample_label(CASE).startswith("01 · ")
    runner, outcome = logic.check_project(CASE.repo, Settings())
    assert runner == "python/pytest" and outcome.failed == 2
    assert logic.is_supported(CASE.repo) is None
    assert "not found" in (logic.is_supported(Path("/no/such/folder")) or "")


def test_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert logic.missing_key(Settings()) == "ANTHROPIC_API_KEY"
    assert logic.missing_key(Settings(model="ollama:llama")) is None
    assert logic.missing_key(Settings(model="ollama:x", fallback_models="groq:y")) == "GROQ_API_KEY"


def test_comparison_rows() -> None:
    steps: list[logic.Step] = []
    result = fake_run_agent("raw", CASE.repo, Settings(), steps.append)
    rows = logic.comparison_rows({"A": result, "B": result})
    assert rows[0] == {"Measure": "Result", "A": "Fixed", "B": "Fixed"}
    assert [s.kind for s in steps][:1] == ["info"]


def test_page_runs_a_sample_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(logic, "run_agent", fake_run_agent)

    page = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not page.exception
    assert page.title[0].value.endswith("Bugfix agent")

    page.button[0].click().run()  # Check the tests
    assert not page.exception
    assert "2 failing" in page.error[0].value

    page.radio[1].set_value("Compare both").run()
    next(b for b in page.button if "Find and fix" in b.label).click().run()
    assert not page.exception
    assert any("Fixed" in s.value for s in page.success)
    assert page.table  # the side-by-side comparison
    # Sample bugs are never modified, so no Apply button is offered.
    assert not any("Apply" in b.label for b in page.button)
