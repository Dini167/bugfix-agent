"""Remove the Hebrew learning comments (lines starting with `# 📘`) from the code.

    python scripts/strip_explanations.py          # strip in place
    python scripts/strip_explanations.py --check  # list files that still have them

Only whole comment lines that start with the marker are removed; code and the
regular English comments and docstrings are untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "# 📘"
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "cases"}


def strip_text(text: str) -> str:
    """Return `text` without marker lines, and without the blank line a removed block leaves."""
    lines = text.splitlines()
    kept: list[str] = []
    removed_previous = False
    for line in lines:
        if line.lstrip().startswith(MARKER):
            removed_previous = True
            continue
        if removed_previous and not line.strip() and kept and not kept[-1].strip():
            removed_previous = False
            continue
        removed_previous = False
        kept.append(line)
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def python_files(root: Path) -> list[Path]:
    return [
        p
        for p in sorted(root.rglob("*.py"))
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
        and p.name != "strip_explanations.py"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Only report; do not modify.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    touched = []
    for path in python_files(args.root):
        text = path.read_text(encoding="utf-8")
        stripped = strip_text(text)
        if stripped != text:
            touched.append(path.relative_to(args.root).as_posix())
            if not args.check:
                path.write_text(stripped, encoding="utf-8")

    verb = "contain explanations" if args.check else "cleaned"
    print(f"{len(touched)} file(s) {verb}" + "".join(f"\n  {t}" for t in touched))
    return 1 if args.check and touched else 0


if __name__ == "__main__":
    raise SystemExit(main())
