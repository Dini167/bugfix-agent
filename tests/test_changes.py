from __future__ import annotations

from pathlib import Path

import pytest

from core.changes import apply_changes, collect_changes, unified_patch


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_collect_and_apply(tmp_path: Path) -> None:
    original = _tree(tmp_path / "a", {"src/x.py": "a = 1\n", "tests/test_x.py": "t\n"})
    work = _tree(tmp_path / "b", {"src/x.py": "a = 2\n", "tests/test_x.py": "CHANGED\n"})
    changes = collect_changes(original, work)
    assert [c.path for c in changes] == ["src/x.py"]  # protected files never included
    patch = unified_patch(changes)
    assert "-a = 1" in patch and "+a = 2" in patch
    assert apply_changes(original, changes) == ["src/x.py"]
    assert (original / "src/x.py").read_text() == "a = 2\n"
    assert (original / "tests/test_x.py").read_text() == "t\n"


def test_apply_refuses_if_file_changed_meanwhile(tmp_path: Path) -> None:
    original = _tree(tmp_path / "a", {"x.py": "a = 1\n"})
    work = _tree(tmp_path / "b", {"x.py": "a = 2\n"})
    changes = collect_changes(original, work)
    (original / "x.py").write_text("a = 99\n")  # the user kept working
    with pytest.raises(RuntimeError, match="changed since"):
        apply_changes(original, changes)
    assert (original / "x.py").read_text() == "a = 99\n"


def test_ignores_build_output(tmp_path: Path) -> None:
    original = _tree(tmp_path / "a", {"x.py": "a\n"})
    work = _tree(tmp_path / "b", {"x.py": "a\n", "node_modules/m.js": "m", "target/out": "o"})
    assert collect_changes(original, work) == []
