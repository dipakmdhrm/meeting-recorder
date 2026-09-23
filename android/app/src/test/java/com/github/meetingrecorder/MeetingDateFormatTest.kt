package com.github.meetingrecorder

import com.github.meetingrecorder.util.formatMeetingDate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.LocalDateTime
import java.util.Locale

class MeetingDateFormatTest {

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
        assertEquals("Mon, Mar 2, 2026  14:30", formatted)
    }

    @Test
    fun `weekday tracks the actual day`() {
        assertTrue(formatMeetingDate(LocalDateTime.of(2026, 3, 1, 9, 5)).startsWith("Sun, "))
        assertTrue(formatMeetingDate(LocalDateTime.of(2026, 3, 7, 9, 5)).startsWith("Sat, "))
    }

    @Test
    fun `time is zero-padded 24-hour`() {
        assertEquals("Mon, Mar 2, 2026  09:05", formatMeetingDate(LocalDateTime.of(2026, 3, 2, 9, 5)))
        assertEquals("Mon, Mar 2, 2026  00:00", formatMeetingDate(LocalDateTime.of(2026, 3, 2, 0, 0)))
    }

    @Test
    fun `follows the current locale`() {
        Locale.setDefault(Locale.GERMANY)
        val formatted = formatMeetingDate(LocalDateTime.of(2026, 3, 2, 14, 30))
        assertTrue(formatted, formatted.startsWith("Mo"))
    }
}
