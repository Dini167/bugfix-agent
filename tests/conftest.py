from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from core.config import Settings
from core.sandbox import Workspace


@pytest.fixture
def settings() -> Settings:
    return Settings(test_timeout_s=60, command_timeout_s=10)


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A minimal repository with one failing and one passing test."""
    repo = tmp_path / "sample"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef double(x):\n    return x * 2\n"
    )
    (repo / "tests" / "test_calc.py").write_text(
        "from pkg.calc import add, double\n\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_double():\n    assert double(4) == 8\n"
    )
    return repo


@pytest.fixture
def workspace(sample_repo: Path) -> Iterator[Workspace]:
    with Workspace.from_source(sample_repo) as ws:
        yield ws
