from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("strip", ROOT / "scripts" / "strip_explanations.py")
assert _spec and _spec.loader
strip = importlib.util.module_from_spec(_spec)
sys.modules["strip"] = strip
_spec.loader.exec_module(strip)


def test_strip_text_removes_only_marked_lines() -> None:
    src = (
        '"""Doc."""\n\n# 📘 הסבר\n\nimport os\n\n\n'
        "def f():\n    # 📘 עוד הסבר\n    return 1  # regular\n"
    )
    expected = '"""Doc."""\n\nimport os\n\n\ndef f():\n    return 1  # regular\n'
    assert strip.strip_text(src) == expected


def test_stripped_project_is_valid_python(tmp_path: Path) -> None:
    copy = tmp_path / "proj"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", "*_cache"))
    assert strip.main(["--root", str(copy)]) == 0
    for path in strip.python_files(copy):
        text = path.read_text(encoding="utf-8")
        assert not any(line.lstrip().startswith(strip.MARKER) for line in text.splitlines())
        ast.parse(text, filename=str(path))
    assert strip.main(["--root", str(copy), "--check"]) == 0
