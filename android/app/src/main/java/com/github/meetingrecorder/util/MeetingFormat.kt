package com.github.meetingrecorder.util

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Display formatting for Library meeting rows. Mirrors the Linux app's
 * `utils/meeting_format.py` so both apps label a meeting identically:
 * "Mon, Mar 02, 2026  ·  2:30 PM  ·  45m".
 *
 * Weekday and month names follow the device locale, like every other formatted
 * date in the app; the formatters are built per call so a locale change is
 * picked up without a process restart.
 */

// Middle dot with padding, matching the rest of the Library list styling.
private const val SEPARATOR = "  ·  "
private const val DATE_PATTERN = "EEE, MMM dd, yyyy"
private const val TIME_PATTERN = "h:mm a"

/** Returns "Mon, Mar 02, 2026  ·  2:30 PM" — weekday, date, then time. */
fun formatMeetingDate(date: LocalDateTime): String {
    val locale = Locale.getDefault()
    val day = date.format(DateTimeFormatter.ofPattern(DATE_PATTERN, locale))
    val time = date.format(DateTimeFormatter.ofPattern(TIME_PATTERN, locale))
    return listOf(day, time).joinToString(SEPARATOR)
}

/** Returns a compact duration: "1h 5m" past an hour, otherwise "45m". */
fun formatDuration(seconds: Int): String =
    if (seconds >= 3600) {
        "${seconds / 3600}h ${(seconds % 3600) / 60}m"
    } else {
        "${seconds / 60}m"
    }

/** Builds a Library row's timestamp line: weekday, date, time, and duration when known. */
fun formatMeetingSubtitle(date: LocalDateTime, durationSeconds: Int?): String {
    val parts = mutableListOf(formatMeetingDate(date))
    if (durationSeconds != null) parts.add(formatDuration(durationSeconds))
    return parts.joinToString(SEPARATOR)
}
