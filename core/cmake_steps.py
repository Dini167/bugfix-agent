"""Configure, build and test a CMake project in one command, for `core.languages.CMakeTest`.

Run as a script: `python cmake_steps.py SOURCE BUILD JUNIT_XML [TEST_REGEX]`. Each
step's output streams through; the first step that fails ends the run with its
exit code, so a compile error is reported as an error, never as a pass.
"""

# 📘 ב-C++ אין פקודה אחת שמריצה טסטים: צריך להגדיר (cmake), לבנות (קומפילציה) ורק אז להריץ (ctest).
# 📘 הסקריפט הקטן הזה עושה את שלושת השלבים ברצף, כדי שמבחינת שאר הקוד זו עדיין פקודה אחת.

from __future__ import annotations

import subprocess
import sys


def main(argv: list[str]) -> int:
    source, build, junit, *target = argv
    steps = [
        ["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Debug"],
        ["cmake", "--build", build, "--config", "Debug", "--parallel"],
        [
            "ctest",
            "--test-dir",
            build,
            "-C",
            "Debug",
            "--output-on-failure",
            "--no-tests=error",
            "--output-junit",
            junit,
            *(["-R", target[0]] if target else []),
        ],
    ]
    for step in steps:
        sys.stdout.flush()
        code = subprocess.run(step, check=False).returncode
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
