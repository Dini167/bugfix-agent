from datetime import date

from timesheet.dates import iso_week_label


def test_iso_week_label():
    assert iso_week_label(date(2026, 11, 23)) == "2026-W48"
