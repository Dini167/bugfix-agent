"""Aggregations over numeric series."""

from collections.abc import Sequence


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean. Raises ValueError("empty series") for empty input."""
    return sum(values) / len(values)


def spread(values: Sequence[float]) -> float:
    """Max minus min. Raises ValueError("empty series") for empty input."""
    if not values:
        raise ValueError("empty series")
    return max(values) - min(values)
