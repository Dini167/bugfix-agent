"""Reviewing one file without tests: findings, locating the code, and fixing only on request."""

from __future__ import annotations

from typing import Any

import pytest

from core.config import Settings
from core.tools import ToolSpec
from core.types import ToolResult, Usage
from raw_agent import review
from raw_agent.providers.base import History, ModelTurn, StopReason, ToolCall

CODE = """def average(grades):
    total = 0
    for i in range(1, len(grades)):
        total += grades[i]
    return total / len(grades)


def highest(grades):
    return max(grades)
"""

BUG = {
    "title": "הציון הראשון לא נספר",
    "severity": "major",
    "explanation": "average([90, 80]) מחזיר 40 במקום 85.",
    "original": "    for i in range(1, len(grades)):",
    "fixed": "    for i in range(len(grades)):",
    "why_fix_works": "הלולאה עוברת עכשיו על כל האיברים.",
}


class ScriptedConversation:
    def __init__(self, turns: list[ModelTurn], seen: list[str]) -> None:
        self.turns = turns
        self.seen = seen

    def add_user_text(self, text: str) -> None:
        self.seen.append(text)

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        raise AssertionError("a review needs no tool results")

    def send(self) -> ModelTurn:
        return self.turns.pop(0)


class ScriptedProvider:
    name = "scripted"

    def __init__(self, *turns: ModelTurn) -> None:
        self.turns = list(turns)
        self.seen: list[str] = []
        self.system = ""
        self.tools: list[ToolSpec] = []

    def new_conversation(
        self, system: str, tools: list[ToolSpec], history: History = ()
    ) -> ScriptedConversation:
        self.system, self.tools = system, tools
        return ScriptedConversation(self.turns, self.seen)


def _report(summary: str, findings: list[dict[str, Any]]) -> ModelTurn:
    call = ToolCall("t1", "report_findings", {"summary": summary, "findings": findings})
    return ModelTurn("", [call], StopReason.TOOL_USE, Usage(1000, 200), model="claude-sonnet-5-5")


def test_finds_the_bug_and_where_it_is() -> None:
    provider = ScriptedProvider(_report("ממוצע של ציונים.", [BUG]))
    result = review.review_file(provider, "grades.py", CODE, Settings())
    (finding,) = result.findings
    assert finding.applicable and (finding.start_line, finding.end_line) == (3, 3)
    assert result.language == "python" and result.cost_usd > 0
    assert "<file>\n" + CODE in provider.seen[0]
    assert provider.tools == [review.REPORT_TOOL]
    assert "inventing a problem" in provider.system and "in Hebrew" in provider.system


def test_no_bugs_means_no_findings() -> None:
    provider = ScriptedProvider(_report("הקוד תקין.", []))
    result = review.review_file(provider, "grades.py", CODE, Settings())
    assert result.findings == [] and result.summary == "הקוד תקין."


def test_reminds_once_when_the_model_answers_in_prose() -> None:
    prose = ModelTurn("Looks fine to me.", [], StopReason.END_TURN, Usage(10, 5))
    provider = ScriptedProvider(prose, _report("ok", []))
    review.review_file(provider, "a.py", CODE, Settings())
    assert "report_findings" in provider.seen[-1]

    silent = ScriptedProvider(prose, prose)
    with pytest.raises(review.ReviewError):
        review.review_file(silent, "a.py", CODE, Settings())


def test_code_with_different_trailing_spaces_is_still_found() -> None:
    code = CODE.replace("range(1, len(grades)):", "range(1, len(grades)):   ")
    provider = ScriptedProvider(_report("", [BUG | {"original": BUG["original"] + "\n"}]))
    (finding,) = review.review_file(provider, "g.py", code, Settings()).findings
    assert finding.start_line == 3
    fixed = review.apply_finding(code, finding)
    assert "    for i in range(len(grades)):\n        total" in fixed


def test_code_that_is_not_in_the_file_cannot_be_applied() -> None:
    ghost = BUG | {"original": "    for g in grades:"}
    provider = ScriptedProvider(_report("", [ghost]))
    (finding,) = review.review_file(provider, "g.py", CODE, Settings()).findings
    assert not finding.applicable
    with pytest.raises(review.ReviewError):
        review.apply_finding(CODE, finding)


def test_apply_changes_only_that_piece() -> None:
    provider = ScriptedProvider(_report("", [BUG]))
    (finding,) = review.review_file(provider, "g.py", CODE, Settings()).findings
    fixed = review.apply_finding(CODE, finding)
    assert fixed == CODE.replace("range(1, len(grades))", "range(len(grades))")
    with pytest.raises(review.ReviewError):  # already applied: the old code is gone
        review.apply_finding(fixed, finding)


def test_syntax_check_and_languages() -> None:
    assert review.syntax_problem("a.py", "def f(:\n") is not None
    assert review.syntax_problem("a.py", CODE) is None
    assert review.syntax_problem("a.js", "function (") is None  # not checked
    assert review.language_of("Main.CS") == "csharp" and review.language_of("README") == ""


def test_too_large_a_file_is_refused() -> None:
    with pytest.raises(review.ReviewError):
        review.review_file(ScriptedProvider(), "big.py", "x" * 200_000, Settings())
