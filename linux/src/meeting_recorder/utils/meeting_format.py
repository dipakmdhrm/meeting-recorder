"""Pure display formatting for Library meeting rows (no GTK involved)."""

from __future__ import annotations

from datetime import datetime

# Middle dot with padding, matching the rest of the Library list styling.
_SEPARATOR = "  ·  "


def format_meeting_datetime(date: datetime) -> str:
    """Return "Mon, Mar 02, 2026  ·  2:30 PM" — weekday, date, then time."""
    day_str = date.strftime("%a, %b %d, %Y")
    time_str = date.strftime("%I:%M %p").lstrip("0")
    return _SEPARATOR.join([day_str, time_str])


def format_duration(seconds: int) -> str:
    """Return a compact duration: "1h 05m" past an hour, otherwise "45m"."""
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 60}m"


def format_meeting_subtitle(date: datetime, duration_seconds: int | None = None) -> str:
    """Build the secondary line of a Library row: weekday, date, time, duration."""
    parts = [format_meeting_datetime(date)]
    if duration_seconds is not None:
        parts.append(format_duration(duration_seconds))
    return _SEPARATOR.join(parts)
