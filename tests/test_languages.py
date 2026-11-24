from __future__ import annotations

from pathlib import Path

import pytest

from core.guards import is_protected
from core.languages import (
    CargoTest,
    CMakeTest,
    Counts,
    CustomCommand,
    DotnetTest,
    GoTest,
    Gradle,
    Maven,
    NodeTest,
    Pytest,
    UnsupportedRepository,
    cargo_failures,
    detect_runner,
    find_failures,
    go_failures,
    junit_failures,
    parse_cargo,
    parse_go_verbose,
    parse_junit_xml,
    parse_tap,
    parse_trx,
    resolve_counts,
    tap_failures,
    trx_failures,
)
from core.sandbox import CommandResult
from core.types import TestFailure


@pytest.mark.parametrize(
    ("marker", "runner"),
    [
        ("go.mod", GoTest),
        ("Cargo.toml", CargoTest),
        ("package.json", NodeTest),
        ("pom.xml", Maven),
        ("build.gradle.kts", Gradle),
        ("pyproject.toml", Pytest),
        ("CMakeLists.txt", CMakeTest),
        ("App.sln", DotnetTest),
        ("App.csproj", DotnetTest),
    ],
)
def test_detects_runner_from_marker(tmp_path: Path, marker: str, runner: type) -> None:
    (tmp_path / marker).write_text("")
    assert isinstance(detect_runner(tmp_path), runner)


def test_python_without_marker_files(tmp_path: Path) -> None:
    (tmp_path / "lib.py").write_text("x = 1\n")
    assert isinstance(detect_runner(tmp_path), Pytest)


def test_config_file_wins(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "bugfix.yaml").write_text('test_command: "bundle exec rake test"\nlanguage: ruby\n')
    runner = detect_runner(tmp_path)
    assert isinstance(runner, CustomCommand)
    assert runner.name == "ruby/custom"
    assert runner.command(tmp_path, None, tmp_path) == ["bundle", "exec", "rake", "test"]


def test_config_file_needs_a_command(tmp_path: Path) -> None:
    (tmp_path / "bugfix.yaml").write_text("language: ruby\n")
    with pytest.raises(UnsupportedRepository, match="test_command"):
        detect_runner(tmp_path)


def test_unknown_repository_explains_what_to_do(tmp_path: Path) -> None:
    (tmp_path / "main.zig").write_text("")
    with pytest.raises(UnsupportedRepository, match=r"bugfix\.yaml"):
        detect_runner(tmp_path)


def test_node_prefers_npm_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    assert NodeTest().command(tmp_path, None, tmp_path) == ["npm", "test", "--silent"]
    assert NodeTest().command(tmp_path, "a.test.ts", tmp_path)[-2:] == ["--", "a.test.ts"]
    (tmp_path / "package.json").write_text("{}")
    assert NodeTest().command(tmp_path, None, tmp_path)[:2] == ["node", "--test"]


def test_parse_tap() -> None:
    out = (
        "TAP version 13\nok 1 - a\nnot ok 2 - b\n1..2\n# tests 2\n# pass 1\n# fail 1\n# skipped 0\n"
    )
    assert parse_tap(out) == Counts(passed=1, failed=1)
    assert parse_tap("no summary here") is None


def test_parse_go_verbose() -> None:
    out = "=== RUN   TestA\n--- PASS: TestA (0.00s)\n=== RUN   TestB\n--- FAIL: TestB (0.00s)\n"
    out += "    --- PASS: TestB/sub (0.00s)\n--- SKIP: TestC (0.00s)\nFAIL\n"
    assert parse_go_verbose(out) == Counts(passed=2, failed=1, skipped=1)


def test_parse_cargo_sums_all_targets() -> None:
    out = (
        "test result: ok. 3 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out\n"
        "test result: FAILED. 2 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out\n"
    )
    assert parse_cargo(out) == Counts(passed=5, failed=2, skipped=1)


def test_parse_junit_xml(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite tests="5" failures="1" errors="1" skipped="1"/></testsuites>'
    )
    assert parse_junit_xml([report]) == Counts(passed=2, failed=1, errors=1, skipped=1)
    assert parse_junit_xml([tmp_path / "missing.xml"]) is None


@pytest.mark.parametrize(
    ("exit_code", "parsed", "expected"),
    [
        (0, None, Counts(passed=1)),  # suite-level pass
        (1, None, Counts(errors=1)),  # suite-level failure
        (1, Counts(passed=3), Counts(passed=3, errors=1)),  # crashed after passing tests
        (1, Counts(passed=3, failed=1), Counts(passed=3, failed=1)),
    ],
)
def test_resolve_counts(exit_code: int, parsed: Counts | None, expected: Counts) -> None:
    class Fake:
        name = "fake"

        def command(self, root: Path, target: str | None, report_dir: Path) -> list[str]:
            return []

        def counts(self, result: CommandResult, root: Path, report_dir: Path) -> Counts | None:
            return parsed

        def failures(
            self, result: CommandResult, root: Path, report_dir: Path
        ) -> list[TestFailure]:
            return []

    result = CommandResult(exit_code, "", "", timed_out=False)
    assert resolve_counts(Fake(), result, Path(), Path()) == expected


@pytest.mark.parametrize(
    "path",
    [
        "src/app.test.ts",
        "src/app.spec.js",
        "__tests__/a.js",
        "pager_test.go",
        "go.mod",
        "Cargo.toml",
        "Cargo.lock",
        "src/test/java/AppTest.java",
        "src/main/java/AppTest.java",
        "spec/user_spec.rb",
        "package.json",
        "jest.config.js",
        "bugfix.yaml",
        "CMakeLists.txt",
        "src/parser_test.cpp",
        "src/ParserTests.cpp",
        "tests/Shop.Tests/CartTests.cs",
        "Shop.Tests/Helpers.cs",
        "src/Shop/Shop.csproj",
        "Shop.sln",
        "Directory.Build.props",
    ],
)
def test_protected_in_every_language(path: str) -> None:
    assert is_protected(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/app.ts",
        "pager.go",
        "src/lib.rs",
        "src/main/java/App.java",
        "lib/user.rb",
        "src/stats.cpp",
        "include/stats.hpp",
        "src/Shop/Cart.cs",
    ],
)
def test_source_files_are_editable(path: str) -> None:
    assert not is_protected(path)


# ------------------------------------------------------------------ failure details


def test_dotnet_project_inside_a_folder(tmp_path: Path) -> None:
    project = tmp_path / "tests" / "App.Tests" / "App.Tests.csproj"
    project.parent.mkdir(parents=True)
    project.write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.csproj").write_text("")
    runner = detect_runner(tmp_path)
    assert isinstance(runner, DotnetTest)
    argv = runner.command(tmp_path, None, tmp_path / "r")
    assert argv[:3] == ["dotnet", "test", "tests/App.Tests/App.Tests.csproj"]


def test_cmake_builds_outside_the_project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    argv = CMakeTest().command(root, "average", tmp_path / "r")
    assert argv[1].endswith("cmake_steps.py")
    assert argv[3] == str(tmp_path / "repo-cmake-build") and argv[-1] == "average"


def test_junit_failures_with_messages_and_locations(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_stats" name="test_avg">'
        '<failure message="assert 55.0 == 85.0">tests/test_stats.py:7: in test_avg</failure>'
        "</testcase>"
        '<testcase classname="average" name="average">'
        '<failure message="Failed"/><system-out>src/x.cpp:3: expected 2, got 1</system-out>'
        "</testcase>"
        '<testcase classname="t" name="ok"/>'
        "</testsuite></testsuites>"
    )
    first, second = junit_failures([report])
    expected = TestFailure("test_stats.test_avg", "assert 55.0 == 85.0", "tests/test_stats.py:7")
    assert first == expected
    assert second.name == "average" and "expected 2, got 1" in second.message
    assert second.location == "src/x.cpp:3"


def test_tap_failures_read_the_error_block() -> None:
    out = (
        "not ok 1 - sorts highest first\n"
        "  ---\n"
        "  location: '/w/repo/test/a.test.js:5:1'\n"
        "  error: |-\n"
        "    Expected values to be strictly deep-equal:\n"
        "    [40, 70]\n"
        "  code: 'ERR_ASSERTION'\n"
        "  ...\n"
        "not ok 2 - suite\n"
        "  ---\n"
        "  failureType: 'subtestsFailed'\n"
        "  error: '1 subtest failed'\n"
        "  ...\n"
        "ok 3 - fine\n"
    )
    (failure,) = tap_failures(out)
    assert failure.name == "sorts highest first"
    assert failure.message == "Expected values to be strictly deep-equal:\n[40, 70]"
    assert failure.location == "/w/repo/test/a.test.js:5:1"


def test_go_failures_keep_the_logged_lines() -> None:
    out = (
        "=== RUN   TestFirstPage\n    pager_test.go:12: got [a]\n--- FAIL: TestFirstPage (0.00s)\n"
        "=== RUN   TestOther\n--- PASS: TestOther (0.00s)\n"
    )
    assert go_failures(out) == [TestFailure("TestFirstPage", "got [a]", "pager_test.go:12")]


def test_cargo_failures_read_the_panic() -> None:
    out = (
        "---- hebrew stdout ----\n\n"
        "thread 'hebrew' (17) panicked at tests/preview.rs:15:5:\n"
        "assertion `left == right` failed\n  left: 1\n right: 2\n"
        "note: run with `RUST_BACKTRACE=1`\n\n\nfailures:\n    hebrew\n"
    )
    (failure,) = cargo_failures(out)
    assert failure.name == "hebrew" and failure.location == "tests/preview.rs:15"
    assert failure.message.startswith("assertion `left == right` failed")


TRX = """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testName="Shop.Tests.CartTests.Adds_up" outcome="Failed">
      <Output><ErrorInfo>
        <Message>Assert.Equal() Failure: Values differ
Expected: 3
Actual:   1</Message>
        <StackTrace> at Shop.CartTests.Adds_up() in /w/repo/tests/CartTests.cs:line 12</StackTrace>
      </ErrorInfo></Output>
    </UnitTestResult>
    <UnitTestResult testName="Shop.Tests.CartTests.Empty" outcome="Passed" />
  </Results>
  <ResultSummary outcome="Failed">
    <Counters total="5" executed="5" passed="3" failed="1" error="1" notExecuted="0" />
  </ResultSummary>
</TestRun>"""


def test_trx_counts_and_failures(tmp_path: Path) -> None:
    report = tmp_path / "results.trx"
    report.write_text(TRX)
    assert parse_trx([report]) == Counts(passed=3, failed=1, errors=1)
    (failure,) = trx_failures([report])
    assert failure.name == "Shop.Tests.CartTests.Adds_up"
    assert failure.message.startswith("Assert.Equal() Failure") and "Actual:   1" in failure.message
    assert failure.location == "/w/repo/tests/CartTests.cs:12"


def test_find_failures_relative_paths_and_build_errors(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    runner = CustomCommand("make test")
    tap = f"not ok 1 - a\n  ---\n  location: '{root}/t/a.js:3:1'\n  error: 'boom'\n  ...\n"
    result = CommandResult(1, tap, "", False)
    (failure,) = find_failures(runner, result, root, tmp_path, Counts(failed=1))
    assert failure.location == "t/a.js:3:1"

    broken = CommandResult(2, "compiling...\nsrc/a.c:4: error: expected ';'\n", "", False)
    (failure,) = find_failures(runner, broken, root, tmp_path, Counts(errors=1))
    assert failure.name == "build or test run"
    assert "expected ';'" in failure.message and failure.location == "src/a.c:4"
    assert find_failures(runner, broken, root, tmp_path, Counts(passed=3)) == []
