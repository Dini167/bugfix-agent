"""Calendar helpers. Weeks follow ISO 8601: they start on Monday."""

from datetime import date, timedelta


def week_start(day: date) -> date:
    """The Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.weekday() + 1)


def iso_week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"
