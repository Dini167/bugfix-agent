"""`bugfix`: one command for everything.

    bugfix check  [PATH]                 see what is failing; no API key needed
    bugfix fix    [PATH] [--engine raw|sdk] [--fallback MODEL] [--yes | --dry-run]
    bugfix ui                            the same, in your browser
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
from core.config import Settings, is_free_model
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
        if self.color and os.name == "nt":
            os.system("")  # turns on color support in the Windows console

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
        elif kind == "fallback":
            reason = str(event.get("error", "")).split(" | ")[0]
            self.bad(f"  ! {event.get('from')} failed ({reason})")
            self.bad(f"    continuing on {event.get('to')}")


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
        for failure in outcome.failures:
            where = f"  ({failure.location})" if failure.location else ""
            display.line(f"  x {failure.name}{where}")
            for text in failure.message.splitlines()[:6]:
                display.line(f"      {text}")
        if args.verbose:
            display.line(outcome.output)
        display.line("Run `bugfix fix` to let the agent find and fix the cause.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        display.info("Note: ANTHROPIC_API_KEY is not set; `bugfix fix` will need it.")
    return 0 if outcome.all_passed else 1


def cmd_fix(args: argparse.Namespace, display: Display) -> int:
    from cli.engines import EngineSetupError, open_engine
    from core.harness import run_case

    repo = args.path.resolve()
    settings = Settings.from_env()
    if args.model:
        settings = _with(settings, model=args.model)
    if args.max_cost is not None:
        settings = _with(settings, max_cost_usd=args.max_cost)
    if args.fallback:
        settings = _with(settings, fallback_models=",".join(args.fallback))
    if args.engine == "sdk" and settings.fallbacks:
        display.info("Note: backup models work with the raw agent only; the SDK runs on Claude.")
        settings = _with(settings, fallback_models="")
    if not _keys_present(settings, display):
        return 2
    try:
        detect_runner(repo)
    except UnsupportedRepository as exc:
        display.bad(str(exc))
        return 2

    models = " -> ".join([settings.model, *settings.fallbacks])
    display.title(f"Fixing {repo.name} with the {args.engine} agent ({models})")
    display.info("Working on a private copy; your files are not touched yet.")
    try:
        with open_engine(args.engine, settings) as engine:
            result = run_case(
                repo.name, repo, engine, settings, trace_path=args.trace, listener=display.on_event
            )
    except EngineSetupError as exc:
        display.bad(str(exc))
        return 2
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


# 📘 פותח את הממשק הגרפי בדפדפן. Streamlit מריץ שרת קטן על המחשב שלך, ורק את רואה אותו.
def cmd_ui(args: argparse.Namespace, display: Display) -> int:
    import subprocess

    app = Path(__file__).resolve().parent.parent / "ui" / "app.py"
    display.title("Opening the bugfix agent in your browser. Press Ctrl+C here to stop it.")
    argv = [sys.executable, "-m", "streamlit", "run", str(app)]
    argv += ["--browser.gatherUsageStats", "false", "--server.port", str(args.port)]
    argv += ["--client.toolbarMode", "minimal"]
    return subprocess.call(argv)


def cmd_benchmark(args: argparse.Namespace, display: Display) -> int:
    from benchmark.catalog import load_cases
    from benchmark.run import RESULTS_DIR, run_benchmark
    from cli.engines import EngineSetupError

    settings = Settings.from_env()
    if not _keys_present(settings, display):
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

    try:
        run_benchmark(engines, cases, args.repeats, settings, on_result=report)
    except EngineSetupError as exc:
        display.bad(str(exc))
        return 2
    display.good(f"Results written to {RESULTS_DIR / 'results.md'}")
    return 0


# ------------------------------------------------------------------ plumbing


def _with(settings: Settings, **changes: Any) -> Settings:
    from dataclasses import replace

    return replace(settings, **changes)


# 📘 בודקים מראש שיש מפתח לכל מודל בשרשרת, ומסבירים בדיוק מה חסר, במקום להיכשל באמצע הריצה.
def _keys_present(settings: Settings, display: Display) -> bool:
    from raw_agent.providers.openai_compat import HOSTS, split_model

    missing: list[str] = []
    for model in [settings.model, *settings.fallbacks]:
        if not is_free_model(model):
            needed: str | None = "ANTHROPIC_API_KEY"
        else:
            try:
                needed = HOSTS[split_model(model)[0]].key_env
            except ValueError as exc:
                display.bad(str(exc))
                return False
        if needed and not os.environ.get(needed) and needed not in missing:
            missing.append(needed)
    for name in missing:
        display.bad(f"{name} is not set.")
        if name == "ANTHROPIC_API_KEY":
            display.line("Create a key at https://console.anthropic.com, then run:")
        else:
            display.line("Create a free key on that service's website, then run:")
        display.line(f"  export {name}=...     (macOS / Linux)")
        display.line(f'  setx {name} "..."     (Windows, then reopen the terminal)')
    return not missing


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
    fix.add_argument(
        "--model", help="Model to use: a Claude model, or a free one such as ollama:<model>."
    )
    fix.add_argument(
        "--fallback",
        action="append",
        metavar="MODEL",
        help="Backup model if the main one fails, e.g. groq:<model>. Repeat for more.",
    )
    fix.add_argument("--max-cost", type=float, help="Stop if a run would cost more (USD).")
    fix.add_argument("--trace", type=Path, help="Write a step-by-step JSONL trace here.")
    fix.add_argument("-v", "--verbose", action="store_true", help="Show the agent's full notes.")
    mode = fix.add_mutually_exclusive_group()
    mode.add_argument("-y", "--yes", action="store_true", help="Apply without asking.")
    mode.add_argument("--dry-run", action="store_true", help="Show the fix, never apply it.")

    ui = sub.add_parser("ui", help="Open the agent in your browser.")
    ui.add_argument("--port", type=int, default=8501)

    bench = sub.add_parser("benchmark", help="Compare both agents on the bug set.")
    bench.add_argument("--engine", choices=["raw", "sdk", "both"], default="both")
    bench.add_argument("--cases", nargs="*", help="Case id prefixes, e.g. 01 11.")
    bench.add_argument("--repeats", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    # A Windows console may not be able to show every character in a patch or report.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    display = Display(verbose=getattr(args, "verbose", False))
    commands = {"check": cmd_check, "fix": cmd_fix, "ui": cmd_ui, "benchmark": cmd_benchmark}
    try:
        return commands[args.command](args, display)
    except KeyboardInterrupt:
        display.bad("Stopped. Your project was not changed.")
        return 130
    except BrokenPipeError:  # output piped into e.g. `head`
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
