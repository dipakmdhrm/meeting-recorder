package com.github.meetingrecorder

import com.github.meetingrecorder.util.formatDuration
import com.github.meetingrecorder.util.formatMeetingDate
import com.github.meetingrecorder.util.formatMeetingSubtitle
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.LocalDateTime
import java.util.Locale

class MeetingFormatTest {

    private lateinit var originalLocale: Locale

    @Before
    fun setUp() {
        // Weekday and month names are locale-dependent; pin the locale so the
        // expected strings below are stable on any dev machine / CI runner.
        originalLocale = Locale.getDefault()
        Locale.setDefault(Locale.US)
    }

    @After
    fun tearDown() {
        Locale.setDefault(originalLocale)
    }

    @Test
    fun `includes the day of the week before the date and time`() {
        // 2026-03-02 is a Monday.
        val formatted = formatMeetingDate(LocalDateTime.of(2026, 3, 2, 14, 30))
        assertEquals("Mon, Mar 02, 2026  ·  2:30 PM", formatted)
    }

    @Test
    fun `weekday tracks the actual day`() {
        assertTrue(formatMeetingDate(LocalDateTime.of(2026, 3, 1, 9, 5)).startsWith("Sun, "))
        assertTrue(formatMeetingDate(LocalDateTime.of(2026, 3, 7, 9, 5)).startsWith("Sat, "))
    }

    @Test
    fun `time is 12-hour with no leading zero on the hour`() {
        assertEquals(
            "Mon, Mar 02, 2026  ·  9:05 AM",
            formatMeetingDate(LocalDateTime.of(2026, 3, 2, 9, 5)),
        )
    }

    @Test
    fun `midnight and noon`() {
        assertTrue(formatMeetingDate(LocalDateTime.of(2026, 3, 2, 0, 0)).endsWith("12:00 AM"))
        assertTrue(formatMeetingDate(LocalDateTime.of(2026, 3, 2, 12, 0)).endsWith("12:00 PM"))
    }

    @Test
    fun `follows the current locale`() {
        Locale.setDefault(Locale.GERMANY)
        val formatted = formatMeetingDate(LocalDateTime.of(2026, 3, 2, 14, 30))
        assertTrue(formatted, formatted.startsWith("Mo"))
    }

    @Test
    fun `duration under an hour shows minutes`() {
        assertEquals("45m", formatDuration(45 * 60))
        assertEquals("0m", formatDuration(0))
        assertEquals("0m", formatDuration(59))
    }

    @Test
    fun `duration of an hour or more shows hours and minutes`() {
        assertEquals("1h 0m", formatDuration(3600))
        assertEquals("1h 5m", formatDuration(3600 + 5 * 60))
        assertEquals("2h 30m", formatDuration(2 * 3600 + 30 * 60))
    }

    @Test
    fun `subtitle appends the duration when known`() {
        assertEquals(
            "Mon, Mar 02, 2026  ·  2:30 PM  ·  45m",
            formatMeetingSubtitle(LocalDateTime.of(2026, 3, 2, 14, 30), 45 * 60),
        )
    }

    @Test
    fun `subtitle omits the duration when unknown`() {
        assertEquals(
            "Mon, Mar 02, 2026  ·  2:30 PM",
            formatMeetingSubtitle(LocalDateTime.of(2026, 3, 2, 14, 30), null),
        )
    }

    @Test
    fun `subtitle still shows a zero duration`() {
        assertTrue(
            formatMeetingSubtitle(LocalDateTime.of(2026, 3, 2, 14, 30), 0).endsWith("  ·  0m"),
        )
    }
}
