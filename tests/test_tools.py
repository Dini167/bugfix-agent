from __future__ import annotations

import pytest

from core.config import Settings
from core.sandbox import Workspace
from core.tools import Toolbox, truncate


@pytest.fixture
def tools(workspace: Workspace, settings: Settings) -> Toolbox:
    return Toolbox(workspace, settings)


def test_specs_are_well_formed() -> None:
    names = [s.name for s in Toolbox.specs()]
    assert names == ["run_tests", "list_files", "read_file", "search_code", "edit_file"]
    for spec in Toolbox.specs():
        assert spec.input_schema["type"] == "object"
        assert spec.input_schema["additionalProperties"] is False
        assert spec.description


def test_run_tests_reports_failures(tools: Toolbox) -> None:
    result = tools.dispatch("run_tests", {})
    assert not result.is_error
    assert "TESTS FAILING" in result.output
    assert "passed=1 failed=1" in result.output
    assert "test_add" in result.output


def test_run_tests_single_file(tools: Toolbox) -> None:
    result = tools.dispatch("run_tests", {"path": "tests/test_calc.py::test_double"})
    assert "ALL TESTS PASSED" in result.output


def test_list_files(tools: Toolbox) -> None:
    out = tools.dispatch("list_files", {}).output
    assert out.splitlines() == ["pkg/__init__.py", "pkg/calc.py", "tests/test_calc.py"]


def test_read_file_with_range(tools: Toolbox) -> None:
    out = tools.dispatch("read_file", {"path": "pkg/calc.py", "start_line": 2, "end_line": 2})
    assert "2  " in out.output and "return a - b" in out.output
    assert "def add" not in out.output


def test_read_missing_file(tools: Toolbox) -> None:
    result = tools.dispatch("read_file", {"path": "nope.py"})
    assert result.is_error


def test_read_outside_workspace(tools: Toolbox) -> None:
    result = tools.dispatch("read_file", {"path": "../../etc/passwd"})
    assert result.is_error
    assert "escapes" in result.output


def test_search_literal_and_regex(tools: Toolbox) -> None:
    assert "pkg/calc.py:2:" in tools.dispatch("search_code", {"pattern": "a - b"}).output
    out = tools.dispatch("search_code", {"pattern": r"def \w+\(x\)", "regex": True}).output
    assert "pkg/calc.py:5: def double(x):" in out


def test_search_invalid_regex(tools: Toolbox) -> None:
    assert tools.dispatch("search_code", {"pattern": "(", "regex": True}).is_error


def test_edit_fixes_bug(tools: Toolbox) -> None:
    result = tools.dispatch(
        "edit_file", {"path": "pkg/calc.py", "old": "return a - b", "new": "return a + b"}
    )
    assert not result.is_error
    assert "+    return a + b" in result.output
    assert "ALL TESTS PASSED" in tools.dispatch("run_tests", {}).output


def test_edit_requires_unique_match(tools: Toolbox) -> None:
    result = tools.dispatch("edit_file", {"path": "pkg/calc.py", "old": "return", "new": "yield"})
    assert result.is_error and "matches 2 places" in result.output


def test_edit_missing_text(tools: Toolbox) -> None:
    result = tools.dispatch("edit_file", {"path": "pkg/calc.py", "old": "nope", "new": "x"})
    assert result.is_error and "not found" in result.output


def test_edit_refuses_test_files(tools: Toolbox, workspace: Workspace) -> None:
    before = (workspace.root / "tests" / "test_calc.py").read_text()
    result = tools.dispatch(
        "edit_file", {"path": "tests/test_calc.py", "old": "== 5", "new": "== -1"}
    )
    assert result.is_error and "read-only" in result.output
    assert (workspace.root / "tests" / "test_calc.py").read_text() == before


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("read_file", {}, "missing required"),
        ("read_file", {"path": "a", "extra": 1}, "unknown field"),
        ("read_file", {"path": 5}, "must be of type string"),
        ("read_file", {"path": "a", "start_line": True}, "must be of type integer"),
        ("read_file", {"path": "a", "start_line": 0}, ">= 1"),
        ("no_such_tool", {}, "Unknown tool"),
    ],
)
def test_argument_validation(
    tools: Toolbox, name: str, args: dict[str, object], message: str
) -> None:
    result = tools.dispatch(name, args)
    assert result.is_error
    assert message in result.output


def test_tool_output_is_truncated(workspace: Workspace) -> None:
    (workspace.root / "big.txt").write_text("x" * 50_000)
    tools = Toolbox(workspace, Settings(max_tool_output_chars=1_000))
    out = tools.dispatch("read_file", {"path": "big.txt"}).output
    assert len(out) < 1_100
    assert "[truncated" in out


def test_truncate_keeps_head_and_tail() -> None:
    text = "HEAD" + "." * 1000 + "TAIL"
    out = truncate(text, 100)
    assert out.startswith("HEAD") and out.endswith("TAIL")
