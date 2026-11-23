"""Aggregate logged hours into weekly totals."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from timesheet.dates import week_start


@dataclass(frozen=True)
class Entry:
    day: date
    hours: float


def weekly_totals(entries: list[Entry]) -> dict[date, float]:
    """Total hours per week, keyed by the week's Monday, in chronological order."""
    totals: dict[date, float] = defaultdict(float)
    for entry in entries:
        totals[week_start(entry.day)] += entry.hours
    return dict(sorted(totals.items()))
