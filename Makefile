.PHONY: install check test lint typecheck strip-explanations

install:
	pip install -e ".[dev]"

test:
	python -m pytest

lint:
	ruff check .
	ruff format --check .

# Tests are checked against 3.13 because third-party stubs pulled in by pytest
# use 3.12+ syntax; the package itself is checked against its 3.11 floor.
typecheck:
	mypy core benchmark raw_agent sdk_agent scripts
	mypy --python-version 3.13 tests

check: lint typecheck test

# Remove the Hebrew learning comments (lines starting with "# 📘").
strip-explanations:
	python scripts/strip_explanations.py
