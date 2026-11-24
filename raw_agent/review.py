"""Review one source file, in any language, without tests.

The test-driven agent needs a failing suite to prove a fix. Often there is only
a single file. Here the model reads that file once and reports the defects it
is sure of, each with the exact code to replace and the replacement, through
one forced tool call. Nothing is changed: `apply_finding` makes the edit only
when the person asks for it, and refuses if the code is no longer there.

Runs on the raw provider layer, so it works with the same models and the same
free-model fallback as the raw agent.
"""

# 📘 בדיקה של קובץ אחד, בלי טסטים: המודל קורא את הקובץ ומחזיר רשימה של באגים שהוא בטוח בהם. לכל
# 📘 באג: הסבר, הקוד המדויק שצריך להחליף, ומה לשים במקומו. הקובץ לא משתנה עד שהמשתמש מאשר תיקון.
# 📘 אם אין באג, הרשימה ריקה, והמודל מתבקש במפורש לא להמציא בעיות.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.config import FREE, PRICING, Settings
from core.guards import Budget
from core.tools import ToolSpec
from core.types import Usage
from raw_agent.providers.base import ModelProvider

MAX_FILE_CHARS = 120_000

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sql": "sql",
    ".sh": "bash",
    ".ps1": "powershell",
    ".r": "r",
    ".dart": "dart",
    ".lua": "lua",
    ".html": "html",
    ".css": "css",
}

REVIEW_SYSTEM_PROMPT = """\
You are a senior software engineer reviewing one source file for real bugs. \
The file may be in any programming language.

Report a finding only when you are sure the code is wrong: it crashes, returns \
a wrong result, loses or corrupts data, leaks a resource, or is a security \
hole, for an input or situation that can really happen. For each finding you \
must be able to name that input or situation and say what happens versus what \
should happen.

Do NOT report style, naming, formatting, comments, missing documentation, \
performance tweaks, "could be cleaner" refactors, missing features, or things \
that depend on code you cannot see. If you are not sure, leave it out. Most \
files have no bugs or only one or two: an empty list is a normal, correct \
answer, and inventing a problem is the worst possible mistake here.

For each finding, give the smallest fix: `original` is a contiguous piece of \
the file copied exactly, character for character, with its indentation and \
nothing added (no line numbers, no "..."), long enough to occur only once in \
the file; `fixed` is what replaces it. Do not touch unrelated lines.

Answer by calling the report_findings tool exactly once.\
"""

_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "The bug in a few words."},
        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
        "explanation": {
            "type": "string",
            "description": "What is wrong, the concrete input or situation that triggers "
            "it, and what happens versus what should happen.",
        },
        "original": {"type": "string", "description": "Exact code to replace, verbatim."},
        "fixed": {"type": "string", "description": "The code that replaces it."},
        "why_fix_works": {"type": "string", "description": "Why the fix is correct."},
    },
    "required": ["title", "severity", "explanation", "original", "fixed", "why_fix_works"],
}

REPORT_TOOL = ToolSpec(
    name="report_findings",
    description="Report the review result: a short summary and the bugs found, if any.",
    input_schema={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One or two sentences: what the file does and whether it has bugs.",
            },
            "findings": {"type": "array", "items": _FINDING_SCHEMA},
        },
        "required": ["summary", "findings"],
    },
)


class ReviewError(RuntimeError):
    """The model did not return a usable review."""


@dataclass(frozen=True, slots=True)
class Finding:
    title: str
    severity: str
    explanation: str
    original: str
    fixed: str
    why_fix_works: str
    # Where `original` sits in the file (1-based, inclusive), or 0 when it could not
    # be found exactly once, in which case the fix cannot be applied automatically.
    start_line: int = 0
    end_line: int = 0

    @property
    def applicable(self) -> bool:
        return self.start_line > 0


@dataclass(slots=True)
class Review:
    filename: str
    language: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    model: str = ""


def language_of(filename: str) -> str:
    """The highlighting name for a file, from its extension ('' when unknown)."""
    dot = filename.rfind(".")
    return LANGUAGES.get(filename[dot:].lower(), "") if dot >= 0 else ""


def _locate(code: str, original: str) -> tuple[int, str]:
    """Line where `original` occurs exactly once, and the exact text there; (0, '') if not.

    Tolerates trailing whitespace the model dropped or added at line ends.
    """
    if original and code.count(original) == 1:
        return code[: code.index(original)].count("\n") + 1, original
    wanted = [line.rstrip() for line in original.strip("\n").splitlines()]
    if not wanted or not any(wanted):
        return 0, ""
    lines = code.splitlines()
    hits = [
        i
        for i in range(len(lines) - len(wanted) + 1)
        if [line.rstrip() for line in lines[i : i + len(wanted)]] == wanted
    ]
    if len(hits) != 1:
        return 0, ""
    start = hits[0]
    return start + 1, "\n".join(lines[start : start + len(wanted)])


def _finding(raw: dict[str, Any], code: str) -> Finding:
    original = str(raw.get("original", ""))
    fixed = str(raw.get("fixed", ""))
    start, exact = _locate(code, original)
    if start and exact != original:
        fixed = fixed.strip("\n")  # matched line by line, without the surrounding newlines
    return Finding(
        title=str(raw.get("title", "")).strip(),
        severity=str(raw.get("severity", "major")),
        explanation=str(raw.get("explanation", "")).strip(),
        original=exact if start else original,
        fixed=fixed,
        why_fix_works=str(raw.get("why_fix_works", "")).strip(),
        start_line=start,
        end_line=start + exact.rstrip("\n").count("\n") if start else 0,
    )


def review_prompt(settings: Settings) -> str:
    language = settings.report_language.strip()
    if not language or language.lower() == "english":
        return REVIEW_SYSTEM_PROMPT
    return (
        REVIEW_SYSTEM_PROMPT + f"\n\nWrite summary, title, explanation and why_fix_works in "
        f"{language}. Keep code, identifiers and values exactly as they are, in backticks."
    )


def review_file(provider: ModelProvider, filename: str, code: str, settings: Settings) -> Review:
    """Ask the model for the bugs in one file. Raises ReviewError if it gives no answer."""
    if len(code) > MAX_FILE_CHARS:
        raise ReviewError(f"The file is too large to review ({len(code):,} characters).")
    language = language_of(filename)
    budget = Budget(settings)
    started = time.perf_counter()
    conversation = provider.new_conversation(review_prompt(settings), [REPORT_TOOL])
    conversation.add_user_text(
        f"Review this file for bugs.\n\nFile name: {filename}\n\n<file>\n{code}\n</file>"
    )
    arguments: dict[str, Any] | None = None
    model = ""
    for _ in range(2):  # one reminder if the model answers in prose instead of the tool
        turn = conversation.send()
        model = turn.model or model
        budget.record_usage(turn.usage, FREE if turn.free else PRICING.get(turn.model))
        call = next((c for c in turn.tool_calls if c.name == REPORT_TOOL.name), None)
        if call is not None:
            arguments = call.arguments
            break
        conversation.add_user_text("Answer by calling report_findings now, exactly once.")
    if arguments is None:
        raise ReviewError("The model did not return a review. Try again.")
    findings = [_finding(raw, code) for raw in arguments.get("findings") or [] if raw]
    return Review(
        filename=filename,
        language=language,
        summary=str(arguments.get("summary", "")).strip(),
        findings=[f for f in findings if f.original and f.title],
        usage=budget.usage,
        cost_usd=round(budget.cost_usd, 6),
        wall_time_s=round(time.perf_counter() - started, 2),
        model=model or settings.model,
    )


def apply_finding(code: str, finding: Finding) -> str:
    """The file with this one fix applied. Refuses if the code to replace is not there once."""
    if code.count(finding.original) != 1:
        raise ReviewError("The code this fix replaces is no longer in the file, or not only once.")
    return code.replace(finding.original, finding.fixed, 1)


def syntax_problem(filename: str, code: str) -> str | None:
    """A syntax error the fix would introduce, for languages Python can check itself."""
    if language_of(filename) != "python":
        return None
    try:
        compile(code, filename, "exec")
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    return None
