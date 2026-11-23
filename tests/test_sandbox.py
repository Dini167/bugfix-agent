from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.sandbox import PathEscapeError, Workspace


def test_workspace_is_a_copy(sample_repo: Path, workspace: Workspace) -> None:
    (workspace.root / "pkg" / "calc.py").write_text("changed")
    assert "return a - b" in (sample_repo / "pkg" / "calc.py").read_text()


def test_cleanup_removes_workspace(sample_repo: Path) -> None:
    ws = Workspace.from_source(sample_repo)
    root = ws.root
    ws.cleanup()
    assert not root.exists()


def test_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Workspace.from_source(tmp_path / "nope")


@pytest.mark.parametrize("bad", ["../outside.txt", "pkg/../../x", "/etc/passwd"])
def test_resolve_rejects_escapes(workspace: Workspace, bad: str) -> None:
    with pytest.raises(PathEscapeError):
        workspace.resolve(bad)


def test_resolve_rejects_symlink_escape(workspace: Workspace, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    (workspace.root / "link.txt").symlink_to(secret)
    with pytest.raises(PathEscapeError):
        workspace.resolve("link.txt")


def test_resolve_allows_inside(workspace: Workspace) -> None:
    assert workspace.resolve("pkg/calc.py") == workspace.root / "pkg" / "calc.py"


def test_run_times_out(workspace: Workspace) -> None:
    result = workspace.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)
    assert result.timed_out


def test_run_does_not_leak_secrets(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    result = workspace.run(
        [sys.executable, "-c", "import os; print(os.environ.get('ANTHROPIC_API_KEY'))"],
        timeout_s=10,
    )
    assert result.stdout.strip() == "None"
