package com.github.meetingrecorder

import com.github.meetingrecorder.ui.nav.Routes
import org.junit.Assert.assertEquals
import org.junit.Test

class RoutesTest {

    private val meetingPath = "/storage/emulated/0/Documents/Meetings/2026/July/02/14-30_Standup"

    @Test
    fun `encode replaces every slash with percent-2F`() {
        assertEquals(
            "%2Fstorage%2Femulated%2F0%2FDocuments%2FMeetings%2F2026%2FJuly%2F02%2F14-30_Standup",
            Routes.encodeMeetingPath(meetingPath),
        )
    }

    @Test
    fun `decode is the inverse of encode`() {
        assertEquals(meetingPath, Routes.decodeMeetingPath(Routes.encodeMeetingPath(meetingPath)))
    }

    @Test
    fun `decode is a no-op on an already-decoded path`() {
        // Newer Navigation versions may hand back the argument already URI-decoded; decoding
        // must then leave the path untouched.
        assertEquals(meetingPath, Routes.decodeMeetingPath(meetingPath))
    }

    @Test
    fun `meetingDetail builds the route with the encoded path`() {
        assertEquals(
            "meeting_detail/${Routes.encodeMeetingPath(meetingPath)}",
            Routes.meetingDetail(meetingPath),
        )
    }

    @Test
    fun `meetingDetail route matches the destination pattern prefix and arg name`() {
        val prefix = Routes.MEETING_DETAIL_PATTERN.substringBefore("{")
        assertEquals("meeting_detail/", prefix)
        assertEquals("{${Routes.MEETING_PATH_ARG}}", "{" + Routes.MEETING_DETAIL_PATTERN.substringAfter("{"))
    }

    @Test
    fun `encoding a path without slashes is identity`() {
        assertEquals("14-30_Standup", Routes.encodeMeetingPath("14-30_Standup"))
    }
}
