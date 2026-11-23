"""An isolated, disposable workspace for one agent run.

Every run gets a fresh temporary copy of the case repository, so the original is
never touched and runs cannot leak state into each other. All paths the model
supplies go through `Workspace.resolve`, which rejects anything that would
escape the workspace root.

Scope note: this is process-level isolation (separate directory, minimal
environment, wall-clock timeouts, killed process groups). It is not a security
boundary against hostile code; for untrusted repositories, run the whole
benchmark inside a container.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

_IGNORED_ON_COPY = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"
)


class PathEscapeError(ValueError):
    """Raised when a model-supplied path resolves outside the workspace."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


class Workspace:
    """A temporary copy of a repository, deleted on exit."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @classmethod
    def from_source(cls, source: Path) -> Self:
        """Copy `source` into a new temporary directory."""
        if not source.is_dir():
            raise FileNotFoundError(f"Case repository not found: {source}")
        tmp = Path(tempfile.mkdtemp(prefix="bugfix-ws-"))
        target = tmp / "repo"
        shutil.copytree(source, target, ignore=_IGNORED_ON_COPY, symlinks=True)
        return cls(target)

    @property
    def root(self) -> Path:
        return self._root

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self._root.parent, ignore_errors=True)

    def resolve(self, relative: str | os.PathLike[str] = ".") -> Path:
        """Resolve a model-supplied path, refusing anything outside the root.

        Absolute paths, `..` traversal and symlinks pointing outside the
        workspace are all rejected. Resolution follows symlinks, so a link that
        lands outside the root fails the containment check.
        """
        raw = Path(relative)
        if raw.is_absolute():
            raise PathEscapeError(f"Absolute paths are not allowed: {relative}")
        candidate = (self._root / raw).resolve()
        if not candidate.is_relative_to(self._root):
            raise PathEscapeError(f"Path escapes the workspace: {relative}")
        return candidate

    def relative(self, path: Path) -> str:
        """Render a path inside the workspace as a POSIX path relative to the root."""
        return path.resolve().relative_to(self._root).as_posix()

    def run(self, argv: list[str], timeout_s: float) -> CommandResult:
        """Run a command inside the workspace with a wall-clock timeout.

        The child gets a minimal environment (no API keys or other secrets) and
        its own process group, so a timeout kills everything it spawned.
        """
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self._root),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
        proc = subprocess.Popen(
            argv,
            cwd=self._root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            return CommandResult(proc.returncode, stdout, stderr, timed_out=False)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            return CommandResult(-signal.SIGKILL, stdout, stderr, timed_out=True)
