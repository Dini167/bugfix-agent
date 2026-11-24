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


REPORT = """## Result
RESOLVED

## Failing tests
`test_average_of_several_grades`: ציפה ל-85.0 וקיבל 55.0.

## Root cause
הלולאה ב-`average()` מתחילה מאינדקס 1, ולכן הציון הראשון לא נספר.

## Location
`gradebook/stats.py:9`

## Change
`range(1, len(grades))` הוחלף ב-`range(len(grades))`.

## Why it is correct
הסכום כולל עכשיו את כל האיברים, והמכנה נשאר `len(grades)`.

## Risk
נמוכה: הפונקציה לא משנה את הקלט, וכל 4 הטסטים עוברים.

## Tests
4 passed, 0 failed.

## Prevention
לולאה על כל האיברים נכתבת כ-`sum(grades)` או `for g in grades`, בלי אינדקסים.
"""


class ReferenceEngine:
    name = "reference"

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        fix = CASE.reference_fix
        tracer.event("model_text", text="The loop starts at index 1.")
        toolbox.dispatch("edit_file", {"path": fix.file, "old": fix.old, "new": fix.new})
        tracer.event("tool_call", tool="edit_file", arguments={"path": fix.file}, is_error=False)
        return EngineOutcome(RunStatus.UNRESOLVED, final_report=REPORT)


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
    named = logic.to_step({"kind": "baseline", "failing": 1, "passed": 3, "failures": ["test_a"]})
    assert named == logic.Step("info", "מצב התחלתי: 1 טסטים נכשלים (test_a), 3 עוברים")
    warn = logic.to_step({"kind": "fallback", "from": "a", "to": "b", "error": "X: no | status=1"})
    assert warn is not None and warn.kind == "warn" and "ממשיכים עם b" in warn.text


def test_check_and_samples() -> None:
    assert len(logic.sample_cases()) == 15
    assert logic.sample_label(CASE) == "01. ממוצע ציונים מדלג על הציון הראשון · Python\u200e · קל"
    assert all(c.id[:2] in texts.CASE_NAMES for c in logic.sample_cases())
    runner, outcome = logic.check_project(CASE.repo, Settings())
    assert runner == "python/pytest" and outcome.failed == 2
    assert [f.location for f in outcome.failures] == [
        "tests/test_stats.py:7",
        "tests/test_stats.py:11",
    ]
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
    cards = " ".join(m.value for m in page.markdown)
    assert "test_average_of_single_grade" in cards and "tests/test_stats.py:11" in cards

    next(b for b in page.button if "מצא ותקן" in b.label).click().run()
    assert not page.exception
    shown = " ".join(m.value for m in page.markdown)
    assert "תוקן" in shown
    assert "מה היה הבאג" in shown and "מתחילה מאינדקס 1" in shown  # the report, in sections
    assert page.table  # the side-by-side comparison
    assert len(page.tabs) == 4  # the two views, then one tab per agent
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


def test_report_sections_in_hebrew_order() -> None:
    sections = logic.report_sections(REPORT + "\n## Extra\nmore\n")
    titles = [title for title, _ in sections]
    assert titles[:3] == ["🐞 מה היה הבאג", "🧪 אילו טסטים נכשלו", "📍 איפה"]
    assert titles[-1] == "Extra" and "Result" not in " ".join(titles)
    assert logic.report_sections("no headings at all") == []


def test_file_helpers() -> None:
    code = "a\nb\nc\nd\ne\nf\n"
    assert logic.snippet(code, 3, 3, context=1) == [
        (2, "b", False),
        (3, "c", True),
        (4, "d", False),
    ]
    assert logic.decode("שלום".encode()) == "שלום"
    assert logic.decode("שלום".encode("cp1255")) == "שלום"
    assert logic.decode(b"\x00\x01binary") is None
    diff = logic.file_diff("a.py", "x = 1\n", "x = 2\n")
    assert "-x = 1" in diff and "+x = 2" in diff
