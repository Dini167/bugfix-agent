from datetime import date

from timesheet.report import Entry, weekly_totals


def test_single_week_keyed_by_monday():
    entries = [Entry(date(2026, 11, 17), 8), Entry(date(2026, 11, 19), 6)]  # Tue, Thu
    assert weekly_totals(entries) == {date(2026, 11, 16): 14}


def test_monday_starts_a_new_week():
    entries = [Entry(date(2026, 11, 22), 3), Entry(date(2026, 11, 23), 5)]  # Sun, Mon
    assert weekly_totals(entries) == {date(2026, 11, 16): 3, date(2026, 11, 23): 5}


def test_empty():
    assert weekly_totals([]) == {}
