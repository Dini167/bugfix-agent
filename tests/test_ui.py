"""The browser interface: its logic directly, and the page through Streamlit's test runner."""

from __future__ import annotations

import io
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from benchmark.catalog import load_cases
from core.config import Settings
from core.guards import Budget
from core.harness import EngineOutcome, run_case
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import FileChange, RunResult, RunStatus
from ui import logic, texts

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
        logic.Step("info", "מצב התחלתי: 2 טסטים נכשלים, 0 עוברים")
    )
    tool = logic.to_step({"kind": "tool_call", "tool": "read_file", "arguments": {"path": "a.py"}})
    assert tool == logic.Step("tool", "קורא את הקובץ a.py")
    assert logic.to_step({"kind": "model_text", "text": "## Result\nRESOLVED"}) is None
    assert logic.to_step({"kind": "model_call"}) is None
    warn = logic.to_step({"kind": "fallback", "from": "a", "to": "b", "error": "X: no | status=1"})
    assert warn is not None and warn.kind == "warn" and "ממשיכים עם b" in warn.text


def test_check_and_samples() -> None:
    assert len(logic.sample_cases()) == 13
    assert logic.sample_label(CASE) == "01. ממוצע ציונים מדלג על הציון הראשון · Python · קל"
    assert all(c.id[:2] in texts.CASE_NAMES for c in logic.sample_cases())
    runner, outcome = logic.check_project(CASE.repo, Settings())
    assert runner == "python/pytest" and outcome.failed == 2
    assert logic.is_supported(CASE.repo) is None
    assert "לא מצאתי" in (logic.is_supported(Path("/no/such/folder")) or "")


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
    assert rows[0] == {"": "תוצאה", "A": texts.STATUS[RunStatus.RESOLVED], "B": rows[0]["B"]}
    assert [s.kind for s in steps][:1] == ["info"]


def test_page_runs_a_sample_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(logic, "run_agent", fake_run_agent)

    page = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not page.exception
    assert "איגנט לתיקון באגים" in page.title[0].value
    assert not page.sidebar.children  # nothing for the user to reconfigure

    next(b for b in page.button if "הרץ את הטסטים" in b.label).click().run()
    assert not page.exception
    assert "2 טסטים נכשלים" in page.error[0].value

    next(b for b in page.button if "מצא ותקן" in b.label).click().run()
    assert not page.exception
    assert any("תוקן" in s.value for s in page.success)
    assert page.table  # the side-by-side comparison
    # Sample bugs are never modified, so no apply button is offered.
    assert not any("להחיל" in b.label for b in page.button)


def test_page_asks_for_a_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not page.exception
    assert page.text_input[0].label == "ANTHROPIC_API_KEY"
    assert next(b for b in page.button if "מצא ותקן" in b.label).disabled


def test_save_upload_keeps_the_folder_inside(tmp_path: Path) -> None:
    files = [
        ("shop/src/cart.py", b"x = 1\n"),
        ("shop/tests/test_cart.py", b"def test(): pass\n"),
        ("shop/.git/config", b"secret"),
        ("shop/../../evil.py", b"no"),
        ("/etc/passwd", b"no"),
    ]
    repo = logic.save_upload(files, tmp_path)
    assert repo == tmp_path / "shop"
    found = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file())
    assert found == ["src/cart.py", "tests/test_cart.py"]
    assert not (tmp_path.parent / "evil.py").exists()
    with pytest.raises(ValueError):
        logic.save_upload([("../x.py", b"")], tmp_path)


def test_fixed_project_zip_has_the_fix(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("old\n", encoding="utf-8")
    (repo / "b.txt").write_text("same\n", encoding="utf-8")
    change = FileChange("src/a.py", "old\n", "new\n")
    with zipfile.ZipFile(io.BytesIO(logic.fixed_project_zip(repo, [change]))) as archive:
        assert sorted(archive.namelist()) == ["proj/b.txt", "proj/src/a.py"]
        assert archive.read("proj/src/a.py") == b"new\n"
    assert (repo / "src" / "a.py").read_text(encoding="utf-8") == "old\n"  # untouched


def test_page_offers_to_apply_on_a_local_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(logic, "run_agent", fake_run_agent)
    project = tmp_path / "my-project"
    shutil.copytree(CASE.repo, project)

    page = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    page.radio[0].set_value("תיקייה לפי נתיב").run()
    page.text_input[0].set_value(str(project)).run()
    radio = next(r for r in page.radio if "עם SDK" in r.options)
    radio.set_value("בלי SDK").run()
    next(b for b in page.button if "מצא ותקן" in b.label).click().run()
    assert not page.exception
    apply = next(b for b in page.button if "להחיל" in b.label)
    apply.click().run()
    assert any("התיקון הוחל" in s.value for s in page.success)
    fix = CASE.reference_fix
    assert fix.new in (project / fix.file).read_text(encoding="utf-8")
