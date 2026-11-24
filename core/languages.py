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
import os
import re
import shlex
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from core.sandbox import CommandResult
from core.types import TestFailure

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

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        """Each failing test with its message and location, as far as the output tells."""
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


# ------------------------------------------------------------------ failure details

# 📘 לא מספיק לדעת כמה טסטים נכשלו: מתכנת רוצה לדעת איזה טסט, מה הוא ציפה לקבל ומה קיבל,
# 📘 ובאיזו שורה. כל פונקציה כאן מחלצת את זה מהפלט של מריץ טסטים אחר.
MAX_MESSAGE_CHARS = 600
_LOCATION = re.compile(r"([\w./\\-]+\.[A-Za-z]{1,6}):(\d+)")
_GENERIC_MESSAGES = {"", "failed", "failure", "error"}


def _clip(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    clipped = "\n".join(lines)
    return clipped if len(clipped) <= MAX_MESSAGE_CHARS else clipped[:MAX_MESSAGE_CHARS] + " ..."


def _first_location(text: str) -> str:
    match = _LOCATION.search(text)
    return f"{match.group(1)}:{match.group(2)}" if match else ""


def _telling_lines(text: str, limit: int = 8) -> str:
    """The lines of an output that explain a failure, for runners with no structure."""
    keys = re.compile(r"error|fail|assert|expect|exception|panic|abort", re.IGNORECASE)
    lines = [line for line in text.splitlines() if keys.search(line)]
    return _clip("\n".join((lines or text.strip().splitlines())[-limit:]))


def junit_failures(paths: list[Path]) -> list[TestFailure]:
    """Failing test cases from JUnit XML reports (pytest, Maven, Gradle, CTest)."""
    found: list[TestFailure] = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for case in root.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            owner = (case.get("classname") or "").rsplit(".", 1)[-1]
            name = case.get("name", "?")
            text = (problem.text or "") + "\n" + (case.findtext("system-out") or "")
            message = problem.get("message") or ""
            if message.strip().lower() in _GENERIC_MESSAGES:
                message = _telling_lines(text)
            label = f"{owner}.{name}" if owner and owner != name else name
            found.append(TestFailure(label, _clip(message), _first_location(text)))
    return found


def tap_failures(output: str) -> list[TestFailure]:
    """`not ok` entries from Node's TAP reporter, with the YAML block that follows each."""
    lines = output.splitlines()
    found: list[TestFailure] = []
    for i, line in enumerate(lines):
        match = re.match(r"^\s*not ok \d+ - (.*)$", line)
        if not match:
            continue
        block: list[str] = []
        j = i + 1
        if j < len(lines) and lines[j].strip() == "---":
            j += 1
            while j < len(lines) and lines[j].strip() != "...":
                block.append(lines[j])
                j += 1
        details = "\n".join(block)
        if "subtestsFailed" in details:
            continue  # a suite whose children failed; the children are listed themselves
        message = ""
        for k, entry in enumerate(block):
            key = re.match(r"^(\s*)error: ?(.*)$", entry)
            if not key:
                continue
            value = key.group(2).strip()
            if value.startswith("|"):
                indent = len(key.group(1))
                body = []
                for more in block[k + 1 :]:
                    if more.strip() and len(more) - len(more.lstrip()) <= indent:
                        break
                    body.append(more.strip())
                message = "\n".join(body)
            else:
                message = value.strip("'\"")
            break
        location = re.search(r"location: '([^']+)'", details)
        found.append(
            TestFailure(
                match.group(1).strip(),
                _clip(message) or "failed",
                location.group(1) if location else "",
            )
        )
    return found


def go_failures(output: str) -> list[TestFailure]:
    """`--- FAIL:` tests from `go test -v`, with the `file_test.go:12: ...` lines they logged."""
    logs: dict[str, list[str]] = {}
    current = ""
    found: list[TestFailure] = []
    for line in output.splitlines():
        if run := re.match(r"^=== (?:RUN|CONT)\s+(\S+)", line):
            current = run.group(1)
        elif fail := re.match(r"^\s*--- FAIL: (\S+)", line):
            name = fail.group(1)
            messages = logs.get(name, [])
            if not messages and any(t.startswith(name + "/") for t in logs):
                continue  # a parent test that failed only because a subtest did
            text = "\n".join(messages)
            message = re.sub(r"^\S+\.go:\d+: ", "", text, flags=re.MULTILINE)
            found.append(TestFailure(name, _clip(message) or "failed", _first_location(text)))
        elif current and re.match(r"^\s+\S+\.go:\d+: ", line):
            logs.setdefault(current, []).append(line.strip())
    return found


def cargo_failures(output: str) -> list[TestFailure]:
    """The `---- name stdout ----` sections cargo prints for each failing test."""
    found: list[TestFailure] = []
    sections = re.split(r"^---- (\S+) stdout ----$", output, flags=re.MULTILINE)
    for name, body in zip(sections[1::2], sections[2::2], strict=True):
        body = body.split("\nfailures:", 1)[0]
        panic = re.search(r"panicked at ([^\s]+?):(\d+):\d+:\n(.*?)(?:\nnote:|\Z)", body, re.S)
        if panic:
            location, message = f"{panic.group(1)}:{panic.group(2)}", panic.group(3)
        else:
            location, message = _first_location(body), body
        found.append(TestFailure(name, _clip(message), location))
    return found


def trx_failures(paths: list[Path]) -> list[TestFailure]:
    """Failed tests from Visual Studio TRX reports, which `dotnet test` writes."""
    found: list[TestFailure] = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for result in root.iter(f"{_TRX}UnitTestResult"):
            if result.get("outcome") not in ("Failed", "Error", "Timeout", "Aborted"):
                continue
            message = result.findtext(f"{_TRX}Output/{_TRX}ErrorInfo/{_TRX}Message") or ""
            stack = result.findtext(f"{_TRX}Output/{_TRX}ErrorInfo/{_TRX}StackTrace") or ""
            line = re.search(r" in (.+?):line (\d+)", stack)
            location = f"{line.group(1)}:{line.group(2)}" if line else ""
            found.append(TestFailure(result.get("testName", "?"), _clip(message), location))
    return found


_TRX = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


def parse_trx(paths: list[Path]) -> Counts | None:
    """Sum the counters of TRX reports from `dotnet test`."""
    total = Counts()
    found = False
    for path in paths:
        try:
            counters = ET.parse(path).getroot().find(f"{_TRX}ResultSummary/{_TRX}Counters")
        except (ET.ParseError, OSError):
            continue
        if counters is None:
            continue
        found = True
        n = {key: int(value) for key, value in counters.attrib.items() if value.isdigit()}
        total += Counts(
            n.get("passed", 0),
            n.get("failed", 0),
            n.get("error", 0) + n.get("timeout", 0) + n.get("aborted", 0),
            n.get("notExecuted", 0),
        )
    return total if found else None


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

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return junit_failures([report_dir / "pytest.xml"])


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

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return tap_failures(result.stdout)


class GoTest:
    name = "go/go-test"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        return ["go", "test", "-v", "-count=1", target or "./..."]

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_go_verbose(result.stdout)

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return go_failures(result.stdout)


class CargoTest:
    name = "rust/cargo"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        argv = ["cargo", "test", "--color", "never"]
        return [*argv, target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_cargo(result.stdout + "\n" + result.stderr)

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return cargo_failures(result.stdout + "\n" + result.stderr)


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

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return junit_failures(sorted((root / "target" / "surefire-reports").glob("TEST-*.xml")))


class Gradle:
    name = "java/gradle"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        gradle = "./gradlew" if (root / "gradlew").is_file() else "gradle"
        argv = [gradle, "test", "--console=plain"]
        return [*argv, "--tests", target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        reports = sorted((root / "build" / "test-results" / "test").glob("TEST-*.xml"))
        return parse_junit_xml(reports)

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return junit_failures(sorted((root / "build" / "test-results" / "test").glob("TEST-*.xml")))


class CMakeTest:
    """C and C++ with CMake: configure, build, then CTest, in one step (see cmake_steps.py).

    The build folder sits next to the workspace copy, outside the project, so it is
    neither shown to the agent nor reported as a change, and it stays warm between runs.
    """

    name = "cpp/cmake"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        script = Path(__file__).with_name("cmake_steps.py")
        build = root.parent / f"{root.name}-cmake-build"
        argv = [sys.executable, str(script), str(root), str(build), str(report_dir / "ctest.xml")]
        return [*argv, target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_junit_xml([report_dir / "ctest.xml"])

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return junit_failures([report_dir / "ctest.xml"])


def _dotnet_target(root: Path) -> list[str]:
    """What to hand `dotnet test`: the solution or project at the root, else the test project."""
    if len(solutions := sorted(root.glob("*.sln"))) == 1:
        return [solutions[0].name]
    if sorted(root.glob("*.csproj")) or len(solutions) > 1:
        return []
    projects = sorted(p for p in root.glob("*/**/*.csproj") if "test" in p.name.lower())
    return [projects[0].relative_to(root).as_posix()] if projects else []


class DotnetTest:
    """C# and the rest of .NET, through `dotnet test` with a TRX report."""

    name = "csharp/dotnet"

    def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
        argv = [
            "dotnet",
            "test",
            *_dotnet_target(root),
            "--nologo",
            "--results-directory",
            str(report_dir),
            "--logger",
            "trx;LogFileName=results.trx",
        ]
        return [*argv, "--filter", target] if target else argv

    def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
        return parse_trx(sorted(report_dir.rglob("*.trx")))

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return trx_failures(sorted(report_dir.rglob("*.trx")))


def _is_dotnet(root: Path) -> bool:
    patterns = ("*.sln", "*.csproj", "*.fsproj", "*/*.csproj", "*/*/*.csproj")
    return any(next(root.glob(pattern), None) for pattern in patterns)


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

    def failures(self, result: CommandResult, root: Path, report_dir: Path) -> list[TestFailure]:
        return tap_failures(result.stdout)


# ------------------------------------------------------------------ detection

_MARKERS: list[tuple[tuple[str, ...], TestRunner]] = [
    (("go.mod",), GoTest()),
    (("Cargo.toml",), CargoTest()),
    (("package.json",), NodeTest()),
    (("pom.xml",), Maven()),
    (("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"), Gradle()),
    (("CMakeLists.txt",), CMakeTest()),
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
    if _is_dotnet(root):
        return DotnetTest()
    if any(root.rglob("*.py")):
        return Pytest()
    raise UnsupportedRepository(
        "Could not detect how to run this repository's tests. Supported automatically: "
        "Python (pytest), JavaScript/TypeScript (npm test / node --test), Go, Rust (cargo), "
        "Java (Maven, Gradle), C and C++ (CMake), C# (dotnet). "
        f"For anything else, add a {CONFIG_FILE} with a test_command."
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


def find_failures(
    runner: TestRunner, result: CommandResult, root: Path, report: Path, counts: Counts
) -> list[TestFailure]:
    """Each failing test with its message, or one entry for a run that broke before testing.

    Locations are made relative to the project, so they read `src/a.py:12`, not a
    path inside the temporary copy.
    """
    if counts.failed == 0 and counts.errors == 0:
        return []
    found = runner.failures(result, root, report)
    if not found:
        output = result.stdout + "\n" + result.stderr
        if result.timed_out:
            return [TestFailure("test run", "The test run timed out.")]
        found = [TestFailure("build or test run", _telling_lines(output), _first_location(output))]
    prefixes = [f"file://{root.as_posix()}/", f"{root.as_posix()}/", f"{root}{os.sep}"]

    def relative(location: str) -> str:
        for prefix in prefixes:
            location = location.replace(prefix, "")
        return location

    cleaned = []
    for failure in found:
        location, message = relative(failure.location), relative(failure.message)
        if location and message.startswith(f"{location}: "):
            message = message[len(location) + 2 :]  # the location is shown on its own
        cleaned.append(TestFailure(failure.name, message, location))
    return cleaned
