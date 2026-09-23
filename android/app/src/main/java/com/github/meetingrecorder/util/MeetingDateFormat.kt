package com.github.meetingrecorder.util

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Library row timestamp: day of the week, then the date and time
 * (e.g. "Mon, Mar 2, 2026  14:30"). Weekday and month names follow the device
 * locale, like every other formatted date in the app. The formatter is built
 * per call so a locale change is picked up without a process restart.
 */
private const val MEETING_DATE_PATTERN = "EEE, MMM d, yyyy  HH:mm"

fun formatMeetingDate(date: LocalDateTime): String =
    date.format(DateTimeFormatter.ofPattern(MEETING_DATE_PATTERN, Locale.getDefault()))
