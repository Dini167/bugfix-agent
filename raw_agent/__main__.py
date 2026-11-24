"""Run the raw agent on one repository.

python -m raw_agent benchmark/cases/01_off_by_one/repo
python -m raw_agent path/to/repo --trace trace.jsonl
"""

# 📘 נקודת ההפעלה מהטרמינל. כאן מחברים את החלקים: לקוח HTTP, ספק Anthropic, הלולאה, ו-run_case
# 📘 שמריץ ובודק.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.config import Settings
from core.harness import run_case
from raw_agent.client import MessagesClient
from raw_agent.loop import RawEngine
from raw_agent.providers.anthropic import AnthropicProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="raw_agent", description=__doc__.splitlines()[0])
    parser.add_argument("repo", type=Path, help="Repository with a failing test suite.")
    parser.add_argument("--trace", type=Path, help="Write a JSONL trace to this file.")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    with MessagesClient(settings) as client:
        engine = RawEngine(AnthropicProvider(client, settings))
        result = run_case(args.repo.name, args.repo, engine, settings, trace_path=args.trace)

    print(result.final_report or "(no report)")
    summary = {k: v for k, v in result.to_dict().items() if k != "final_report"}
    print("\n" + json.dumps(summary, indent=2), file=sys.stderr)
    return 0 if result.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
