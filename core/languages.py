"""Language support: how to run a repository's tests, whatever it is written in.

The agent itself is language-agnostic: it reads code, forms hypotheses and
makes edits the same way in any language. Only two things depend on the
ecosystem, and both live here:

1. How to run the test suite and count results (`TestRunner`).
2. Which files define "correct" and must stay read-only (`core.guards`).

Runners are detected from marker files. A repository can also pin its own
command in `bugfix.yaml`, which makes any language with a command-line test
runner work:

    test_command: "bundle exec rspec"
    language: ruby          # optional, for reports
"""

# 📘 האיגנט עצמו לא תלוי בשפה: הוא קורא קוד, משער ומתקן באותה צורה בכל שפה. רק שני דברים תלויים
# 📘 בשפה, ושניהם כאן: איך מריצים את הטסטים ואיך סופרים את התוצאות.
# 📘 (אילו קבצים מוגנים בכל שפה מוגדר ב-guards.py.)
# 📘 לכל שפה שאין לה תמיכה מובנית, מוסיפים לפרויקט קובץ bugfix.yaml עם הפקודה שמריצה את הטסטים.

from __future__ import annotations

import contextlib
import json
import re
import shlex
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from core.sandbox import CommandResult

CONFIG_FILE = "bugfix.yaml"


@dataclass(frozen=True, slots=True)
class Counts:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            self.passed + other.passed,
            self.failed + other.failed,
            self.errors + other.errors,
            self.skipped + other.skipped,
        )

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped


class UnsupportedRepository(RuntimeError):
    """No test runner could be detected and none is configured."""


# 📘 החוזה של "מריץ טסטים": איזו פקודה להריץ, ואיך לחלץ ממנה כמה טסטים עברו ונכשלו.
class TestRunner(Protocol):
    @property
    def name(self) -> str: ...

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]: ...

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        """Parse per-test counts, or None when this runner cannot report them."""
        ...


# ------------------------------------------------------------------ parsers


def parse_junit_xml(paths: list[Path]) -> Counts | None:
    """Sum counts across JUnit XML reports (pytest, Maven Surefire, Gradle)."""
    total = Counts()
    found = False
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for suite in suites:
            found = True
            tests = int(suite.get("tests", 0))
            f = int(suite.get("failures", 0))
            e = int(suite.get("errors", 0))
            s = int(suite.get("skipped", 0))
            total += Counts(tests - f - e - s, f, e, s)
    return total if found else None


def parse_tap(output: str) -> Counts | None:
    """Node's TAP reporter ends with `# pass N`, `# fail N`, `# skipped N` lines."""

    def grab(key: str) -> int | None:
        match = re.search(rf"^# {key} (\d+)\s*$", output, re.MULTILINE)
        return int(match.group(1)) if match else None

    passed, failed = grab("pass"), grab("fail")
    if passed is None and failed is None:
        return None
    return Counts(passed or 0, failed or 0, 0, (grab("skipped") or 0) + (grab("todo") or 0))


def parse_go_verbose(output: str) -> Counts | None:
    """Count `--- PASS/FAIL/SKIP:` lines from `go test -v` (subtests included)."""
    passed = len(re.findall(r"^\s*--- PASS: ", output, re.MULTILINE))
    failed = len(re.findall(r"^\s*--- FAIL: ", output, re.MULTILINE))
    skipped = len(re.findall(r"^\s*--- SKIP: ", output, re.MULTILINE))
    if passed + failed + skipped == 0:
        return None
    return Counts(passed, failed, 0, skipped)


def parse_cargo(output: str) -> Counts | None:
    """Sum every `test result: ... N passed; M failed; K ignored` line from cargo."""
    pattern = r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored"
    matches = re.findall(pattern, output)
    if not matches:
        return None
    total = Counts()
    for passed, failed, ignored in matches:
        total += Counts(int(passed), int(failed), 0, int(ignored))
    return total


# ------------------------------------------------------------------ runners


class Pytest:
    name = "python/pytest"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            f"--junitxml={report_dir / 'pytest.xml'}",
        ]
        return [*argv, target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_junit_xml([report_dir / "pytest.xml"])


class NodeTest:
    """`npm test` when package.json defines it, otherwise Node's built-in runner."""

    name = "javascript/node"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        scripts: dict[str, Any] = {}
        with contextlib.suppress(OSError, ValueError):
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
        if "test" in scripts:
            argv = ["npm", "test", "--silent"]
            return [*argv, "--", target] if target else argv
        argv = ["node", "--test", "--test-reporter=tap"]
        return [*argv, target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_tap(result.stdout)


class GoTest:
    name = "go/go-test"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        return ["go", "test", "-v", "-count=1", target or "./..."]

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_go_verbose(result.stdout)


class CargoTest:
    name = "rust/cargo"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        argv = ["cargo", "test", "--color", "never"]
        return [*argv, target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_cargo(result.stdout + "\n" + result.stderr)


class Maven:
    name = "java/maven"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        argv = ["mvn", "-B", "-q", "test"]
        local_repo = Path("~/.m2/repository").expanduser()
        if local_repo.is_dir():
            argv.append(f"-Dmaven.repo.local={local_repo}")
        return [*argv, f"-Dtest={target}"] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_junit_xml(sorted((root / "target" / "surefire-reports").glob("TEST-*.xml")))


class Gradle:
    name = "java/gradle"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        gradle = "./gradlew" if (root / "gradlew").is_file() else "gradle"
        argv = [gradle, "test", "--console=plain"]
        return [*argv, "--tests", target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        reports = sorted((root / "build" / "test-results" / "test").glob("TEST-*.xml"))
        return parse_junit_xml(reports)


@dataclass(frozen=True, slots=True)
class CustomCommand:
    """A command pinned in bugfix.yaml. Counts are suite-level unless it emits TAP."""

    test_command: str
    language: str = "custom"

    @property
    def name(self) -> str:
        return f"{self.language}/custom"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        argv = shlex.split(self.test_command)
        return [*argv, target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_tap(result.stdout)


# ------------------------------------------------------------------ detection

_MARKERS: list[tuple[tuple[str, ...], TestRunner]] = [
    (("go.mod",), GoTest()),
    (("Cargo.toml",), CargoTest()),
    (("package.json",), NodeTest()),
    (("pom.xml",), Maven()),
    (("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"), Gradle()),
    (("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"), Pytest()),
]


# 📘 זיהוי אוטומטי: קודם בודקים אם יש bugfix.yaml, ואחר כך מחפשים קבצים שמסגירים את השפה
# 📘 (go.mod ל-Go, Cargo.toml ל-Rust, package.json ל-JavaScript וכן הלאה).
def detect_runner(root: Path) -> TestRunner:
    """Pick the test runner for a repository: bugfix.yaml first, then marker files."""
    config = root / CONFIG_FILE
    if config.is_file():
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        command = data.get("test_command")
        if not isinstance(command, str) or not command.strip():
            raise UnsupportedRepository(f"{CONFIG_FILE} must define a non-empty test_command")
        return CustomCommand(command, str(data.get("language", "custom")))
    for markers, runner in _MARKERS:
        if any((root / marker).is_file() for marker in markers):
            return runner
    if any(root.rglob("*.py")):
        return Pytest()
    raise UnsupportedRepository(
        "Could not detect how to run this repository's tests. Supported automatically: "
        "Python (pytest), JavaScript/TypeScript (npm test / node --test), Go, Rust (cargo), "
        f"Java (Maven, Gradle). For anything else, add a {CONFIG_FILE} with a test_command."
    )


# 📘 אם אי אפשר לספור טסטים בודדים, נופלים לתוצאה כללית לפי קוד היציאה. ושגיאת קומפילציה
# 📘 לעולם לא תיחשב כהצלחה.
def resolve_counts(runner: TestRunner, result: CommandResult, root: Path, report: Path) -> Counts:
    """Per-test counts when available; otherwise a suite-level result from the exit code.

    A non-zero exit with no failing tests counted (a compile error, a crash in
    collection) is reported as an error, so it can never look like a pass.
    """
    counts = runner.counts(result, root, report)
    if counts is None or counts.total == 0:
        if result.exit_code == 0 and not result.timed_out:
            return Counts(passed=1)
        return Counts(errors=1)
    if result.exit_code != 0 and counts.failed == 0 and counts.errors == 0:
        return counts + Counts(errors=1)
    return counts
