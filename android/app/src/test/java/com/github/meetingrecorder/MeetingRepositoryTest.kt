package com.github.meetingrecorder

import com.github.meetingrecorder.data.MeetingRepository
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class MeetingRepositoryTest {

    @get:Rule
    val tempDir = TemporaryFolder()

    private fun repo() = MeetingRepository(tempDir.root)

    @Test
    fun `listMeetings returns empty list when root does not exist`() {
        val repo = MeetingRepository(File(tempDir.root, "nonexistent"))
        assertEquals(emptyList<Any>(), repo.listMeetings())
    }

    @Test
    fun `listMeetings returns empty when root is empty`() {
        assertEquals(0, repo().listMeetings().size)
    }

    @Test
    fun `listMeetings skips directories with recording lock file`() {
        val meetingDir = File(tempDir.root, "2024-01-01_14-30_Test").also { it.mkdirs() }
        File(meetingDir, ".recording").createNewFile()

        assertEquals(0, repo().listMeetings().size)
    }

    @Test
    fun `listMeetings parses valid meeting correctly`() {
        val meetingDir = File(tempDir.root, "2024-01-15_10-30_Standup").also { it.mkdirs() }
        File(meetingDir, "recording.m4a").createNewFile()
        File(meetingDir, "notes.md").writeText("## Notes")
        File(meetingDir, "meeting.json").writeText(
            JSONObject()
                .put("title", "Morning Standup")
                .put("duration_seconds", 300)
                .toString()
        )

        val meetings = repo().listMeetings()
        assertEquals(1, meetings.size)
        val m = meetings[0]
        assertEquals("Morning Standup", m.title)
        assertEquals(300, m.durationSeconds)
        assertTrue(m.hasAudio)
        assertTrue(m.hasNotes)
        assertFalse(m.hasTranscript)
        assertEquals(2024, m.date.year)
        assertEquals(1, m.date.monthValue)
        assertEquals(15, m.date.dayOfMonth)
        assertEquals(10, m.date.hour)
        assertEquals(30, m.date.minute)
    }

    @Test
    fun `listMeetings returns null title when meeting_json has no title`() {
        val meetingDir = File(tempDir.root, "2024-02-05_09-00").also { it.mkdirs() }
        File(meetingDir, "recording.m4a").createNewFile()

        val meetings = repo().listMeetings()
        assertEquals(1, meetings.size)
        assertEquals(null, meetings[0].title)
    }

    @Test
    fun `listMeetings sorts meetings newest first`() {
        File(tempDir.root, "2024-01-01_08-00").mkdirs()
        File(tempDir.root, "2024-01-02_09-00").mkdirs()
        File(tempDir.root, "2024-03-10_14-30").mkdirs()

        val meetings = repo().listMeetings()
        assertEquals(3, meetings.size)
        assertTrue(meetings[0].date.isAfter(meetings[1].date))
        assertTrue(meetings[1].date.isAfter(meetings[2].date))
    }

    @Test
    fun `createMeetingDir creates directory under root`() {
        val dir = repo().createMeetingDir(null)
        assertNotNull(dir)
        assertTrue(dir.exists())
        assertTrue(dir.isDirectory)
        assertTrue(dir.absolutePath.startsWith(tempDir.root.absolutePath))
    }

    @Test
    fun `createMeetingDir includes sanitized title in folder name`() {
        val dir = repo().createMeetingDir("Design Review!")
        assertTrue(dir.name.contains("Design_Review_"))
    }

    @Test
    fun `saveMeetingMeta writes expected JSON`() {
        val dir = tempDir.newFolder("meeting")
        repo().saveMeetingMeta(dir, "My Meeting", 120)

        val json = JSONObject(File(dir, "meeting.json").readText())
        assertEquals("My Meeting", json.getString("title"))
        assertEquals(120, json.getInt("duration_seconds"))
    }

    @Test
    fun `saveMeetingMeta omits keys when values are null`() {
        val dir = tempDir.newFolder("meeting2")
        repo().saveMeetingMeta(dir, null, null)

        val json = JSONObject(File(dir, "meeting.json").readText())
        assertFalse(json.has("title"))
        assertFalse(json.has("duration_seconds"))
    }

    // -------------------------------------------------------------------------
    // listMeetings — edge cases
    // -------------------------------------------------------------------------

    @Test
    fun `listMeetings skips folder with non-numeric hour`() {
        File(tempDir.root, "2024-01-15_ab-30_Bad").mkdirs()
        assertEquals(0, repo().listMeetings().size)
    }

    @Test
    fun `listMeetings skips folder with non-numeric minute`() {
        File(tempDir.root, "2024-01-15_10-xx_Bad").mkdirs()
        assertEquals(0, repo().listMeetings().size)
    }

    @Test
    fun `listMeetings skips folder with invalid format`() {
        File(tempDir.root, "not-a-valid-folder").mkdirs()
        assertEquals(0, repo().listMeetings().size)
    }

    @Test
    fun `listMeetings handles corrupted meeting_json without crashing`() {
        val meetingDir = File(tempDir.root, "2024-01-15_10-30").also { it.mkdirs() }
        File(meetingDir, "meeting.json").writeText("not valid json {{{")

        val meetings = repo().listMeetings()
        assertEquals(1, meetings.size)
        assertNull(meetings[0].title)
        assertNull(meetings[0].durationSeconds)
    }

    @Test
    fun `listMeetings handles empty meeting_json without crashing`() {
        val meetingDir = File(tempDir.root, "2024-01-15_11-00").also { it.mkdirs() }
        File(meetingDir, "meeting.json").writeText("")

        val meetings = repo().listMeetings()
        assertEquals(1, meetings.size)
        assertNull(meetings[0].title)
    }

    @Test
    fun `listMeetings correctly reads all file presence flags`() {
        val dir = File(tempDir.root, "2024-02-01_09-00").also { it.mkdirs() }
        File(dir, "recording.m4a").createNewFile()
        File(dir, "transcript.md").writeText("Transcript")
        File(dir, "notes.md").writeText("Notes")

        val m = repo().listMeetings().single()
        assertTrue(m.hasAudio)
        assertTrue(m.hasTranscript)
        assertTrue(m.hasNotes)
    }

    // -------------------------------------------------------------------------
    // createMeetingDir — edge cases
    // -------------------------------------------------------------------------

    @Test
    fun `createMeetingDir with null title produces no trailing underscore`() {
        val dir = repo().createMeetingDir(null)
        // Folder is YYYY-MM-DD_HH-MM with no trailing underscore
        assertFalse("Folder name should not end with underscore: ${dir.name}", dir.name.endsWith("_"))
    }

    @Test
    fun `createMeetingDir truncates title longer than 30 characters`() {
        val longTitle = "A".repeat(100)
        val dir = repo().createMeetingDir(longTitle)
        // YYYY-MM-DD_HH-MM_ (17 chars) + max 30 from title = 47 chars max
        assertTrue("Folder name too long: ${dir.name}", dir.name.length <= 47)
    }

    @Test
    fun `createMeetingDir replaces special characters in title`() {
        val dir = repo().createMeetingDir("Q3 Review: Budget & Goals!")
        // Title part is after the third segment when splitting by "_"
        // YYYY-MM-DD _ HH-MM _ title_part → split("_", limit=3)[2]
        val parts = dir.name.split("_", limit = 3)
        val titlePart = parts.getOrElse(2) { "" }
        assertTrue(
            "Title part contains illegal chars: $titlePart",
            titlePart.matches(Regex("[a-zA-Z0-9_\\-]+"))
        )
    }
}
