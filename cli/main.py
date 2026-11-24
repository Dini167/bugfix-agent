"""`bugfix`: one command for everything.

    bugfix check  [PATH]                 see what is failing; no API key needed
    bugfix fix    [PATH] [--engine raw|sdk] [--yes | --dry-run]
    bugfix benchmark [--engine raw|sdk|both] [--cases 01 11] [--repeats 3]

`fix` never touches your project until it has shown you the exact change and
you said yes (or passed --yes).
"""

# 📘 זו הפקודה שהמשתמש מפעיל. שלוש פקודות בלבד: check (מה נכשל), fix (לתקן) ו-benchmark (להשוות).
# 📘 העיקרון: הכלי לא משנה כלום בפרויקט שלך עד שהוא הראה לך את השינוי המדויק ואישרת.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from core.changes import apply_changes
from core.config import Settings
from core.languages import UnsupportedRepository, detect_runner
from core.sandbox import Workspace
from core.tools import run_test_suite
from core.types import RunResult, RunStatus


class Display:
    """Prints progress as short, plain-language lines. Colors only on a real terminal."""

    def __init__(self, stream: TextIO | None = None, verbose: bool = False) -> None:
        self.stream: TextIO = stream or sys.stdout
        self.verbose = verbose
        self.color = self.stream.isatty() and "NO_COLOR" not in os.environ

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def line(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def info(self, text: str) -> None:
        self.line(self._paint(text, "2"))

    def good(self, text: str) -> None:
        self.line(self._paint(text, "32"))

    def bad(self, text: str) -> None:
        self.line(self._paint(text, "31"))

    def title(self, text: str) -> None:
        self.line(self._paint(text, "1"))

    # 📘 המאזין הזה מקבל כל אירוע מהאיגנט (קריאה למודל, הפעלת כלי) ומתרגם אותו לשורה קצרה וברורה,
    # 📘 כך שרואים בזמן אמת מה האיגנט עושה ולמה.
    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "baseline":
            failing, passed = event.get("failing", 0), event.get("passed", 0)
            self.info(f"Runner: {event.get('runner')}  |  {failing} failing, {passed} passing")
        elif kind == "model_text":
            text = " ".join(str(event.get("text", "")).split())
            if text and not text.startswith("## Result"):
                limit = None if self.verbose else 220
                shown = text if limit is None or len(text) <= limit else text[:limit] + "..."
                self.line(f"  thinking: {shown}")
        elif kind == "tool_call":
            self.line("  " + describe_tool_call(event))


def describe_tool_call(event: dict[str, Any]) -> str:
    args: dict[str, Any] = event.get("arguments") or {}
    tool = event.get("tool")
    failed = " (failed)" if event.get("is_error") else ""
    if tool == "run_tests":
        target = args.get("path")
        return f"> running tests{' in ' + target if target else ''}{failed}"
    if tool == "list_files":
        return f"> listing files{' in ' + args['path'] if args.get('path') else ''}{failed}"
    if tool == "read_file":
        span = ""
        if args.get("start_line"):
            span = f" (lines {args['start_line']}-{args.get('end_line', '')})"
        return f"> reading {args.get('path')}{span}{failed}"
    if tool == "search_code":
        return f"> searching for {args.get('pattern')!r}{failed}"
    if tool == "edit_file":
        return f"> editing {args.get('path')}{' - refused' if failed else ''}"
    return f"> {tool}{failed}"


# ------------------------------------------------------------------ commands


def cmd_check(args: argparse.Namespace, display: Display) -> int:
    repo = args.path.resolve()
    settings = Settings.from_env()
    try:
        runner = detect_runner(repo)
    except UnsupportedRepository as exc:
        display.bad(str(exc))
        return 2
    display.title(f"Project: {repo}")
    display.info(f"Test runner: {runner.name}")
    with Workspace.from_source(repo) as ws:
        outcome = run_test_suite(ws, settings)
    if outcome.all_passed:
        display.good(f"All {outcome.passed} tests pass. Nothing to fix.")
    else:
        display.bad(f"{outcome.failed + outcome.errors} failing, {outcome.passed} passing.")
        if args.verbose:
            display.line(outcome.output)
        display.line("Run `bugfix fix` to let the agent find and fix the cause.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        display.info("Note: ANTHROPIC_API_KEY is not set; `bugfix fix` will need it.")
    return 0 if outcome.all_passed else 1


def cmd_fix(args: argparse.Namespace, display: Display) -> int:
    from cli.engines import open_engine
    from core.harness import run_case

    repo = args.path.resolve()
    settings = Settings.from_env()
    if args.model:
        settings = _with(settings, model=args.model)
    if args.max_cost is not None:
        settings = _with(settings, max_cost_usd=args.max_cost)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        display.bad("ANTHROPIC_API_KEY is not set.")
        display.line("Create a key at https://console.anthropic.com, then run:")
        display.line("  export ANTHROPIC_API_KEY=sk-ant-...     (macOS / Linux)")
        display.line(
            '  setx ANTHROPIC_API_KEY "sk-ant-..."     (Windows, then reopen the terminal)'
        )
        return 2
    try:
        detect_runner(repo)
    except UnsupportedRepository as exc:
        display.bad(str(exc))
        return 2

    display.title(f"Fixing {repo.name} with the {args.engine} agent ({settings.model})")
    display.info("Working on a private copy; your files are not touched yet.")
    with open_engine(args.engine, settings) as engine:
        result = run_case(
            repo.name, repo, engine, settings, trace_path=args.trace, listener=display.on_event
        )
    return finish(result, repo, args, display)


def finish(result: RunResult, repo: Path, args: argparse.Namespace, display: Display) -> int:
    display.line()
    if result.status is RunStatus.ALREADY_PASSING:
        display.good(f"All {result.tests_passed} tests already pass. Nothing to fix.")
        return 0
    if result.final_report:
        display.line(result.final_report.strip())
        display.line()
    cost = f"${result.cost_usd:.4f}"
    display.info(f"{result.iterations} steps, {result.wall_time_s:.0f}s, about {cost}")

    if result.status is not RunStatus.RESOLVED:
        reason = {
            RunStatus.UNRESOLVED: "The tests still fail after the agent's changes.",
            RunStatus.BUDGET_EXCEEDED: f"Stopped at the budget limit ({result.error}).",
            RunStatus.TESTS_TAMPERED: "Rejected: the agent changed test files.",
            RunStatus.ERROR: f"Stopped by an error: {result.error}",
        }.get(result.status, result.status.value)
        display.bad(f"Not fixed. {reason}")
        display.line("Your project was not changed.")
        return 1

    display.good(f"Fixed: all {result.tests_passed} tests pass on the agent's copy.")
    display.line()
    display.title("Proposed change:")
    display.line(result.patch.rstrip())
    display.line()
    if args.dry_run:
        display.info("Dry run: your project was not changed.")
        return 0
    if not args.yes and not _confirm("Apply this change to your project?", display):
        display.info("Not applied. Your project was not changed.")
        return 0
    try:
        changed = apply_changes(repo, result.changes)
    except RuntimeError as exc:
        display.bad(str(exc))
        return 1
    display.good(f"Applied to {', '.join(changed)}.")
    return 0


def cmd_benchmark(args: argparse.Namespace, display: Display) -> int:
    from benchmark.catalog import load_cases
    from benchmark.run import RESULTS_DIR, run_benchmark

    settings = Settings.from_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        display.bad("ANTHROPIC_API_KEY is not set; the benchmark calls the model.")
        return 2
    engines = ["raw", "sdk"] if args.engine == "both" else [args.engine]
    cases = load_cases(only=args.cases)
    total = len(engines) * len(cases) * args.repeats
    display.title(
        f"Benchmark: {', '.join(engines)} x {len(cases)} cases x {args.repeats} = {total}"
    )

    def report(result: RunResult) -> None:
        mark = "ok  " if result.resolved else "FAIL"
        display.line(
            f"  {mark} {result.agent:<9} {result.case_id:<28} {result.iterations:>2} steps  "
            f"${result.cost_usd:.4f}  {result.status.value}"
        )

    run_benchmark(engines, cases, args.repeats, settings, on_result=report)
    display.good(f"Results written to {RESULTS_DIR / 'results.md'}")
    return 0


# ------------------------------------------------------------------ plumbing


def _with(settings: Settings, **changes: Any) -> Settings:
    from dataclasses import replace

    return replace(settings, **changes)


def _confirm(question: str, display: Display) -> bool:
    if not sys.stdin.isatty():
        display.info("No terminal to ask for confirmation; use --yes to apply.")
        return False
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bugfix",
        description="An agent that finds and fixes the bug behind your failing tests.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Show which tests fail (no API key needed).")
    check.add_argument("path", nargs="?", type=Path, default=Path())
    check.add_argument("-v", "--verbose", action="store_true", help="Show the test output.")

    fix = sub.add_parser("fix", help="Find and fix the bug, then ask before applying it.")
    fix.add_argument("path", nargs="?", type=Path, default=Path())
    fix.add_argument("--engine", choices=["raw", "sdk"], default="raw")
    fix.add_argument("--model", help="Model to use (default from settings).")
    fix.add_argument("--max-cost", type=float, help="Stop if a run would cost more (USD).")
    fix.add_argument("--trace", type=Path, help="Write a step-by-step JSONL trace here.")
    fix.add_argument("-v", "--verbose", action="store_true", help="Show the agent's full notes.")
    mode = fix.add_mutually_exclusive_group()
    mode.add_argument("-y", "--yes", action="store_true", help="Apply without asking.")
    mode.add_argument("--dry-run", action="store_true", help="Show the fix, never apply it.")

    bench = sub.add_parser("benchmark", help="Compare both agents on the bug set.")
    bench.add_argument("--engine", choices=["raw", "sdk", "both"], default="both")
    bench.add_argument("--cases", nargs="*", help="Case id prefixes, e.g. 01 11.")
    bench.add_argument("--repeats", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    display = Display(verbose=getattr(args, "verbose", False))
    commands = {"check": cmd_check, "fix": cmd_fix, "benchmark": cmd_benchmark}
    try:
        return commands[args.command](args, display)
    except KeyboardInterrupt:
        display.bad("Stopped. Your project was not changed.")
        return 130
    except BrokenPipeError:  # output piped into e.g. `head`
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
