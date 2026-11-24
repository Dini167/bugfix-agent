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

# 📘 כל ריצה עובדת על עותק זמני של הפרויקט, כך שהמקור לא נפגע לעולם.

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

    # 📘 הגנה מפני "בריחה": כל נתיב שהמודל נותן נבדק שהוא נשאר בתוך התיקייה, גם אם ניסה ../ או
    # 📘 קישור (symlink) החוצה.
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

    @property
    def cache_dir(self) -> Path:
        """Per-run scratch space for toolchain caches, outside the repository."""
        path = self._root.parent / "cache"
        path.mkdir(exist_ok=True)
        return path

    # 📘 כל שפה צריכה מקום לקבצים זמניים (Go, Rust, npm). שמים אותם בתיקייה נפרדת, מחוץ לפרויקט,
    # 📘 כדי שהאיגנט לא יראה אותם ושהם לא ישפיעו על הבדיקה.
    def _environment(self) -> dict[str, str]:
        """A minimal environment: toolchain paths and caches, and no secrets."""
        cache = self.cache_dir
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(cache / "home"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            # Go: keep build caches out of the repo and never download a toolchain.
            "GOCACHE": str(cache / "go-build"),
            "GOPATH": str(cache / "gopath"),
            "GOTOOLCHAIN": "local",
            "GOFLAGS": "-mod=mod",
            # Rust: build output outside the repo; reuse the installed toolchain.
            "CARGO_TARGET_DIR": str(cache / "cargo-target"),
            # npm: cache outside the repo, no update checks or funding banners.
            "npm_config_cache": str(cache / "npm"),
            "npm_config_update_notifier": "false",
            "npm_config_fund": "false",
            "NO_COLOR": "1",
        }
        # Toolchain locations that live under the real home directory.
        defaults = {
            "RUSTUP_HOME": "~/.rustup",
            "CARGO_HOME": "~/.cargo",
            "JAVA_HOME": None,
            "GOROOT": None,
        }
        for name, fallback in defaults.items():
            value = os.environ.get(name) or (str(Path(fallback).expanduser()) if fallback else None)
            if value and Path(value).exists():
                env[name] = value
        (cache / "home").mkdir(exist_ok=True)
        return env

    def run(self, argv: list[str], timeout_s: float) -> CommandResult:
        """Run a command inside the workspace with a wall-clock timeout.

        The child gets a minimal environment (no API keys or other secrets) and
        its own process group, so a timeout kills everything it spawned.
        """
        # 📘 התהליך שרץ מקבל סביבה מינימלית בלבד, בלי מפתח ה-API ובלי סודות אחרים.
        env = self._environment()
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
        # 📘 אם תהליך נתקע, הורגים אותו ואת כל התהליכים שהוא פתח.
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            return CommandResult(-signal.SIGKILL, stdout, stderr, timed_out=True)
