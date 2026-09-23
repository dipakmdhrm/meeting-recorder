"""Tests for the pure Library row formatting helpers."""

from datetime import datetime

from meeting_recorder.utils.meeting_format import (
    format_duration,
    format_meeting_datetime,
    format_meeting_subtitle,
)


class TestFormatMeetingDatetime:
    def test_includes_weekday_before_date_and_time(self):
        # 2026-03-02 is a Monday.
        assert (
            format_meeting_datetime(datetime(2026, 3, 2, 14, 30)) == "Mon, Mar 02, 2026  ·  2:30 PM"
        )

    def test_weekday_tracks_the_actual_day(self):
        assert format_meeting_datetime(datetime(2026, 3, 1, 9, 5)).startswith("Sun, ")
        assert format_meeting_datetime(datetime(2026, 3, 7, 9, 5)).startswith("Sat, ")

    def test_strips_leading_zero_from_hour_only(self):
        result = format_meeting_datetime(datetime(2026, 3, 2, 9, 5))
        assert result == "Mon, Mar 02, 2026  ·  9:05 AM"

    def test_midnight_and_noon(self):
        assert "12:00 AM" in format_meeting_datetime(datetime(2026, 3, 2, 0, 0))
        assert "12:00 PM" in format_meeting_datetime(datetime(2026, 3, 2, 12, 0))


class TestFormatDuration:
    def test_under_an_hour_shows_minutes(self):
        assert format_duration(45 * 60) == "45m"

    def test_zero_seconds(self):
        assert format_duration(0) == "0m"

    def test_partial_minute_rounds_down(self):
        assert format_duration(59) == "0m"

    def test_hour_and_above_shows_hours_and_minutes(self):
        assert format_duration(3600) == "1h 0m"
        assert format_duration(3600 + 5 * 60) == "1h 5m"
        assert format_duration(2 * 3600 + 30 * 60) == "2h 30m"


class TestFormatMeetingSubtitle:
    def test_appends_duration_when_known(self):
        subtitle = format_meeting_subtitle(datetime(2026, 3, 2, 14, 30), 45 * 60)
        assert subtitle == "Mon, Mar 02, 2026  ·  2:30 PM  ·  45m"

    def test_omits_duration_when_unknown(self):
        subtitle = format_meeting_subtitle(datetime(2026, 3, 2, 14, 30), None)
        assert subtitle == "Mon, Mar 02, 2026  ·  2:30 PM"

    def test_duration_defaults_to_unknown(self):
        assert format_meeting_subtitle(datetime(2026, 3, 2, 14, 30)) == (
            "Mon, Mar 02, 2026  ·  2:30 PM"
        )

    def test_zero_duration_is_still_shown(self):
        subtitle = format_meeting_subtitle(datetime(2026, 3, 2, 14, 30), 0)
        assert subtitle.endswith("  ·  0m")
