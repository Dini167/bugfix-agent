from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from benchmark.catalog import Case, load_cases
from benchmark.run import render_markdown, run_benchmark, summarize
from cli import main as cli_main
from cli.main import describe_tool_call
from core.config import Settings
from core.guards import Budget
from core.harness import Engine, EngineOutcome
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import RunStatus

CASES = {c.id[:2]: c for c in load_cases()}


class ReferenceEngine:
    name = "reference"

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        case = next(
            c
            for c in CASES.values()
            if (toolbox.workspace_root / c.reference_fix.file).exists()
            and c.reference_fix.old in (toolbox.workspace_root / c.reference_fix.file).read_text()
        )
        fix = case.reference_fix
        tracer.event("model_text", text="Hypothesis: the reference bug.")
        toolbox.dispatch("run_tests", {})
        toolbox.dispatch("edit_file", {"path": fix.file, "old": fix.old, "new": fix.new})
        tracer.event("tool_call", tool="edit_file", arguments={"path": fix.file}, is_error=False)
        return EngineOutcome(RunStatus.UNRESOLVED, final_report="## Result\nRESOLVED")


@contextmanager
def fake_engine(name: str, settings: Settings) -> Iterator[Engine]:
    yield ReferenceEngine()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(CASES["11"].repo, target)
    return target


@pytest.fixture(autouse=True)
def _engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cli.engines.open_engine", fake_engine)
    monkeypatch.setattr("benchmark.run.open_engine", fake_engine)


def test_check_reports_failures(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main.main(["check", str(project)]) == 1
    out = capsys.readouterr().out
    assert "javascript/node" in out and "1 failing, 3 passing" in out


def test_fix_requires_api_key(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli_main.main(["fix", str(project)]) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().out


def test_fix_dry_run_shows_patch_and_changes_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    before = (project / "src/leaderboard.js").read_text()
    assert cli_main.main(["fix", str(project), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 failing, 3 passing" in out  # live baseline line
    assert "thinking: Hypothesis" in out
    assert "> editing src/leaderboard.js" in out
    assert "+  return [...scores].sort((a, b) => b - a)" in out
    assert (project / "src/leaderboard.js").read_text() == before


def test_fix_yes_applies_the_change(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert cli_main.main(["fix", str(project), "--yes"]) == 0
    assert "b - a" in (project / "src/leaderboard.js").read_text()
    assert cli_main.main(["check", str(project)]) == 0


def test_fix_without_terminal_does_not_apply(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    before = (project / "src/leaderboard.js").read_text()
    assert cli_main.main(["fix", str(project)]) == 0
    assert "use --yes" in capsys.readouterr().out
    assert (project / "src/leaderboard.js").read_text() == before


def test_unsupported_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "main.zig").write_text("")
    assert cli_main.main(["check", str(tmp_path)]) == 2
    assert "bugfix.yaml" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("event", "text"),
    [
        ({"tool": "run_tests", "arguments": {}}, "> running tests"),
        (
            {"tool": "read_file", "arguments": {"path": "a.py", "start_line": 3, "end_line": 9}},
            "> reading a.py (lines 3-9)",
        ),
        ({"tool": "search_code", "arguments": {"pattern": "foo"}}, "> searching for 'foo'"),
        (
            {"tool": "edit_file", "arguments": {"path": "t.py"}, "is_error": True},
            "> editing t.py - refused",
        ),
    ],
)
def test_describe_tool_call(event: dict[str, object], text: str) -> None:
    assert describe_tool_call(event) == text


def test_benchmark_writes_reports(tmp_path: Path) -> None:
    cases: list[Case] = [CASES["01"], CASES["12"]]
    results = run_benchmark(["raw", "sdk"], cases, 1, Settings(), out_dir=tmp_path)
    assert all(r.resolved for runs in results.values() for r in runs)
    summary = summarize("raw", results["raw"])
    assert summary.resolve_rate == 1.0
    md = (tmp_path / "results.md").read_text()
    assert "| raw | 2/2 (100%)" in md and "12_go_pagination" in md
    assert "Lines of code" in md
    assert (tmp_path / "results.json").exists()
    assert render_markdown(results, cases, Settings()).startswith("# Benchmark results")


REVIEWED = "def average(grades):\n    return sum(grades[1:]) / len(grades)\n"


class _OneReview:
    """A provider whose only answer is one review with one finding."""

    name = "scripted"

    def new_conversation(self, system: str, tools: list[object], history: object = ()) -> object:
        from core.types import Usage
        from raw_agent.providers.base import ModelTurn, StopReason, ToolCall

        finding = {
            "title": "The first grade is skipped",
            "severity": "major",
            "explanation": "average([90, 80]) returns 40 instead of 85.",
            "original": "sum(grades[1:])",
            "fixed": "sum(grades)",
            "why_fix_works": "Every grade is summed.",
        }
        call = ToolCall("t1", "report_findings", {"summary": "ok", "findings": [finding]})
        turn = ModelTurn("", [call], StopReason.TOOL_USE, Usage(10, 5))

        class Conversation:
            def add_user_text(self, text: str) -> None:
                pass

            def send(self) -> ModelTurn:
                return turn

        return Conversation()


@pytest.fixture
def scripted_review(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def provider(settings: Settings) -> Iterator[_OneReview]:
        yield _OneReview()

    monkeypatch.setattr("cli.engines.open_provider", provider)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def test_review_dry_run_changes_nothing(
    tmp_path: Path, scripted_review: None, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "grades.py"
    source.write_text(REVIEWED, encoding="utf-8")
    assert cli_main.main(["review", str(source), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 bug(s) found" in out and "(line 2)" in out and "+ sum(grades)" in out
    assert source.read_text(encoding="utf-8") == REVIEWED


def test_review_yes_saves_the_fix(tmp_path: Path, scripted_review: None) -> None:
    source = tmp_path / "grades.py"
    source.write_text(REVIEWED, encoding="utf-8")
    assert cli_main.main(["review", str(source), "--yes"]) == 0
    assert "return sum(grades) / len(grades)" in source.read_text(encoding="utf-8")
