from __future__ import annotations

from pathlib import Path

import pytest

from core.guards import is_protected
from core.languages import (
    CargoTest,
    Counts,
    CustomCommand,
    GoTest,
    Gradle,
    Maven,
    NodeTest,
    Pytest,
    UnsupportedRepository,
    detect_runner,
    parse_cargo,
    parse_go_verbose,
    parse_junit_xml,
    parse_tap,
    resolve_counts,
)
from core.sandbox import CommandResult


@pytest.mark.parametrize(
    ("marker", "runner"),
    [
        ("go.mod", GoTest),
        ("Cargo.toml", CargoTest),
        ("package.json", NodeTest),
        ("pom.xml", Maven),
        ("build.gradle.kts", Gradle),
        ("pyproject.toml", Pytest),
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
    ],
)
def test_protected_in_every_language(path: str) -> None:
    assert is_protected(path)


@pytest.mark.parametrize(
    "path", ["src/app.ts", "pager.go", "src/lib.rs", "src/main/java/App.java", "lib/user.rb"]
)
def test_source_files_are_editable(path: str) -> None:
    assert not is_protected(path)
