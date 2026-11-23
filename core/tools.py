"""The agent's tools: one implementation, used by both agents.

Each tool does one thing, returns compact text, and reports failures as an
actionable `ToolResult(is_error=True)` instead of raising. `Toolbox.specs()`
exposes provider-neutral definitions (name, description, JSON Schema); each
agent adapts them to its own wire format, which keeps the tools identical
across implementations.
"""

from __future__ import annotations

import difflib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import Settings
from core.guards import is_protected
from core.sandbox import PathEscapeError, Workspace
from core.types import TestOutcome, ToolResult

_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".venv", "venv"})

ToolHandler = Callable[..., ToolResult]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A provider-neutral tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]


def truncate(text: str, limit: int) -> str:
    """Trim `text` to `limit` characters, keeping head and tail, with a marker."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [truncated {omitted:,} characters] ...\n{text[-tail:]}"


def run_test_suite(
    workspace: Workspace, settings: Settings, target: str | None = None
) -> TestOutcome:
    """Run pytest in the workspace and return structured counts plus output.

    Counts come from a JUnit XML report written outside the workspace, which is
    more reliable than parsing pytest's console summary.
    """
    with tempfile.TemporaryDirectory(prefix="bugfix-junit-") as tmp:
        report = Path(tmp) / "report.xml"
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            f"--junitxml={report}",
        ]
        if target:
            argv.append(target)
        result = workspace.run(argv, timeout_s=settings.test_timeout_s)
        passed = failed = errors = skipped = 0
        if report.exists():
            root = ET.parse(report).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
            for suite in suites:
                tests = int(suite.get("tests", 0))
                f = int(suite.get("failures", 0))
                e = int(suite.get("errors", 0))
                s = int(suite.get("skipped", 0))
                failed += f
                errors += e
                skipped += s
                passed += tests - f - e - s
    output = (result.stdout + ("\n" + result.stderr if result.stderr.strip() else "")).strip()
    if result.timed_out:
        output += f"\n[test run timed out after {settings.test_timeout_s:.0f}s]"
    return TestOutcome(
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        output=output,
    )


class Toolbox:
    """The five tools, bound to one workspace."""

    def __init__(self, workspace: Workspace, settings: Settings) -> None:
        self._ws = workspace
        self._settings = settings
        self._handlers: dict[str, ToolHandler] = {
            "run_tests": self.run_tests,
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_code": self.search_code,
            "edit_file": self.edit_file,
        }

    # ------------------------------------------------------------------ specs

    @staticmethod
    def specs() -> list[ToolSpec]:
        return [
            ToolSpec(
                name="run_tests",
                description=(
                    "Run the repository's pytest suite (or one test file / node id) and "
                    "return pass/fail counts with short tracebacks for failures. Run the "
                    "full suite (no argument) to verify a fix."
                ),
                input_schema=_schema(
                    {"path": {"type": "string", "description": "Optional test file or node id."}},
                ),
            ),
            ToolSpec(
                name="list_files",
                description="List files in the repository (or a subdirectory), one per line.",
                input_schema=_schema(
                    {"path": {"type": "string", "description": "Directory; defaults to root."}},
                ),
            ),
            ToolSpec(
                name="read_file",
                description=(
                    "Read a file with line numbers. Optionally restrict to a 1-based, "
                    "inclusive line range."
                ),
                input_schema=_schema(
                    {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    required=["path"],
                ),
            ),
            ToolSpec(
                name="search_code",
                description=(
                    "Search all text files for a pattern and return `file:line: text` "
                    "matches. Literal by default; set regex=true for a Python regex."
                ),
                input_schema=_schema(
                    {
                        "pattern": {"type": "string"},
                        "regex": {"type": "boolean"},
                    },
                    required=["pattern"],
                ),
            ),
            ToolSpec(
                name="edit_file",
                description=(
                    "Replace exactly one occurrence of `old` with `new` in a file. `old` "
                    "must match the file exactly (including indentation) and be unique; "
                    "include surrounding lines if needed. Test files and pytest config "
                    "are read-only."
                ),
                input_schema=_schema(
                    {
                        "path": {"type": "string"},
                        "old": {"type": "string", "description": "Exact text to replace."},
                        "new": {"type": "string", "description": "Replacement text."},
                    },
                    required=["path", "old", "new"],
                ),
            ),
        ]

    # --------------------------------------------------------------- dispatch

    def dispatch(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Validate arguments and run a tool. Never raises."""
        handler = self._handlers.get(name)
        spec = next((s for s in self.specs() if s.name == name), None)
        if handler is None or spec is None:
            return ToolResult(f"Unknown tool {name!r}.", is_error=True)
        problem = _validate(arguments, spec.input_schema)
        if problem:
            return ToolResult(f"Invalid arguments for {name}: {problem}", is_error=True)
        try:
            result = handler(**arguments)
        except PathEscapeError as exc:
            result = ToolResult(str(exc), is_error=True)
        except Exception as exc:  # defensive: a tool bug must not kill the run
            result = ToolResult(f"{name} failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(
            truncate(result.output, self._settings.max_tool_output_chars), result.is_error
        )

    # ------------------------------------------------------------------ tools

    def run_tests(self, path: str | None = None) -> ToolResult:
        if path:
            self._ws.resolve(path.split("::", 1)[0])  # containment check only
        outcome = run_test_suite(self._ws, self._settings, target=path)
        summary = (
            f"passed={outcome.passed} failed={outcome.failed} errors={outcome.errors} "
            f"skipped={outcome.skipped} exit_code={outcome.exit_code}"
        )
        status = "ALL TESTS PASSED" if outcome.all_passed else "TESTS FAILING"
        return ToolResult(f"{status}\n{summary}\n\n{outcome.output}")

    def list_files(self, path: str = ".") -> ToolResult:
        base = self._ws.resolve(path)
        if not base.is_dir():
            return ToolResult(f"Not a directory: {path}", is_error=True)
        files: list[str] = []
        for item in sorted(base.rglob("*")):
            rel_parts = item.relative_to(self._ws.root).parts
            if any(part in _SKIP_DIRS for part in rel_parts) or not item.is_file():
                continue
            files.append(self._ws.relative(item))
        limit = self._settings.max_listed_files
        listing = "\n".join(files[:limit])
        if len(files) > limit:
            listing += f"\n... [{len(files) - limit} more files]"
        return ToolResult(listing or "(no files)")

    def read_file(
        self, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> ToolResult:
        target = self._ws.resolve(path)
        if not target.is_file():
            return ToolResult(f"File not found: {path}", is_error=True)
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return ToolResult(f"Not a UTF-8 text file: {path}", is_error=True)
        start = start_line or 1
        end = min(end_line or len(lines), len(lines))
        if start > max(end, 1):
            return ToolResult(
                f"Invalid range {start}-{end_line}; file has {len(lines)} lines.", is_error=True
            )
        width = len(str(end))
        body = "\n".join(f"{n:>{width}}  {lines[n - 1]}" for n in range(start, end + 1))
        return ToolResult(
            f"{self._ws.relative(target)} (lines {start}-{end} of {len(lines)})\n{body}"
        )

    def search_code(self, pattern: str, regex: bool = False) -> ToolResult:
        if not pattern:
            return ToolResult("Pattern must not be empty.", is_error=True)
        try:
            compiled = re.compile(pattern if regex else re.escape(pattern))
        except re.error as exc:
            return ToolResult(f"Invalid regex: {exc}", is_error=True)
        matches: list[str] = []
        limit = self._settings.max_search_matches
        total = 0
        for item in sorted(self._ws.root.rglob("*")):
            rel_parts = item.relative_to(self._ws.root).parts
            if any(part in _SKIP_DIRS for part in rel_parts) or not item.is_file():
                continue
            try:
                text = item.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    total += 1
                    if len(matches) < limit:
                        matches.append(f"{self._ws.relative(item)}:{number}: {line.strip()}")
        if not matches:
            return ToolResult(f"No matches for {pattern!r}.")
        suffix = f"\n... [{total - limit} more matches]" if total > limit else ""
        return ToolResult("\n".join(matches) + suffix)

    def edit_file(self, path: str, old: str, new: str) -> ToolResult:
        target = self._ws.resolve(path)
        rel = self._ws.relative(target)
        if is_protected(rel):
            return ToolResult(
                f"Refused: {rel} is a test or test-configuration file and is read-only. "
                "Fix the code under test instead.",
                is_error=True,
            )
        if not target.is_file():
            return ToolResult(f"File not found: {path}", is_error=True)
        if old == new:
            return ToolResult("`old` and `new` are identical; nothing to change.", is_error=True)
        if not old:
            return ToolResult("`old` must not be empty.", is_error=True)
        content = target.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            return ToolResult(
                f"`old` was not found in {rel}. Re-read the file and copy the text exactly, "
                "including indentation.",
                is_error=True,
            )
        if count > 1:
            return ToolResult(
                f"`old` matches {count} places in {rel}. Include more surrounding lines so "
                "it matches exactly one.",
                is_error=True,
            )
        updated = content.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=2,
            )
        )
        return ToolResult(f"Edited {rel}.\n{diff}")


# ---------------------------------------------------------------- helpers


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "boolean": (bool,),
}


def _validate(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> str | None:
    """Minimal JSON Schema check for the flat schemas used here."""
    if not isinstance(arguments, Mapping):
        return "arguments must be an object"
    properties: dict[str, Any] = schema["properties"]
    missing = [key for key in schema.get("required", []) if key not in arguments]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    unknown = [key for key in arguments if key not in properties]
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"
    for key, value in arguments.items():
        expected = properties[key]["type"]
        allowed = _JSON_TYPES[expected]
        # bool is a subclass of int in Python; reject it where an integer is expected.
        if not isinstance(value, allowed) or (expected == "integer" and isinstance(value, bool)):
            return f"{key} must be of type {expected}"
        minimum = properties[key].get("minimum")
        if minimum is not None and value < minimum:
            return f"{key} must be >= {minimum}"
    return None
