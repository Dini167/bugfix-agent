"""Counting lines of code per layer, for the comparison.

Counts non-blank lines that are not comments or docstrings, so the Hebrew
learning comments and the documentation do not inflate either side.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each implementation, broken down by what the code is for.
LAYERS: dict[str, dict[str, list[str]]] = {
    "raw_agent": {
        "HTTP client, errors, retries": ["raw_agent/client.py"],
        "Provider interface": ["raw_agent/providers/base.py"],
        "Wire format (Anthropic)": ["raw_agent/providers/anthropic.py"],
        "Agent loop": ["raw_agent/loop.py"],
        "Free models and fallback (no SDK equivalent)": [
            "raw_agent/providers/openai_compat.py",
            "raw_agent/providers/fallback.py",
        ],
    },
    "sdk_agent": {
        "SDK wiring and message handling": ["sdk_agent/agent.py"],
    },
    "shared (core)": {
        "Tools, sandbox, guards, grading, languages": [
            "core/tools.py",
            "core/sandbox.py",
            "core/guards.py",
            "core/evaluation.py",
            "core/languages.py",
            "core/changes.py",
            "core/harness.py",
        ],
    },
}


def code_lines(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    docstring_lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                end = body[0].end_lineno or body[0].lineno
                docstring_lines.update(range(body[0].lineno, end + 1))
    return sum(
        1
        for number, line in enumerate(source.splitlines(), start=1)
        if line.strip() and not line.strip().startswith("#") and number not in docstring_lines
    )


def layer_counts(root: Path = ROOT) -> dict[str, dict[str, int]]:
    return {
        impl: {layer: sum(code_lines(root / f) for f in files) for layer, files in layers.items()}
        for impl, layers in LAYERS.items()
    }
