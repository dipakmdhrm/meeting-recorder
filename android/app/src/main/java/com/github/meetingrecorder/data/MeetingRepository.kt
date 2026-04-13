package com.github.meetingrecorder.data

import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime

class MeetingRepository(private val rootDir: File) {

    fun listMeetings(): List<Meeting> {
        val meetings = mutableListOf<Meeting>()
        if (!rootDir.exists()) return meetings

        // Flat structure: BASE_DIR/YYYY-MM-DD_HH-MM[_title]/
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        rootDir.listFiles()?.forEach { meetingDir ->
            if (!meetingDir.isDirectory) return@forEach
            if (File(meetingDir, ".recording").exists()) return@forEach

            val match = pattern.matchEntire(meetingDir.name) ?: return@forEach
            val date = try {
                LocalDateTime.of(
                    match.groupValues[1].toInt(),
                    match.groupValues[2].toInt(),
                    match.groupValues[3].toInt(),
                    match.groupValues[4].toInt(),
                    match.groupValues[5].toInt(),
                )
            } catch (_: Exception) { return@forEach }

            var title: String? = null
            var durationSeconds: Int? = null
            val metaFile = File(meetingDir, "meeting.json")
            if (metaFile.exists()) {
                try {
                    val json = JSONObject(metaFile.readText())
                    title = json.optString("title").ifBlank { null }
                    if (json.has("duration_seconds")) {
                        durationSeconds = json.getInt("duration_seconds")
                    }
                } catch (_: Exception) {
                }
            }

            meetings.add(
                Meeting(
                    path = meetingDir,
                    timeLabel = meetingDir.name,
                    date = date,
                    title = title,
                    hasNotes = File(meetingDir, "notes.md").exists(),
                    hasTranscript = File(meetingDir, "transcript.md").exists(),
                    hasAudio = File(meetingDir, "recording.m4a").exists(),
                    durationSeconds = durationSeconds,
                )
            )
        }

        return meetings.sortedByDescending { it.date }
    }

    fun createMeetingDir(title: String?): File {
        val now = LocalDateTime.now()
        val datePart = "${now.year.toString().padStart(4, '0')}-${now.monthValue.toString().padStart(2, '0')}-${now.dayOfMonth.toString().padStart(2, '0')}"
        val timePart = "${now.hour.toString().padStart(2, '0')}-${now.minute.toString().padStart(2, '0')}"

        val folderName = if (title != null) {
            val sanitized = title.replace(Regex("[^a-zA-Z0-9_\\-]"), "_").take(30)
            "${datePart}_${timePart}_${sanitized}"
        } else {
            "${datePart}_${timePart}"
        }

        val dir = File(rootDir, folderName)
        dir.mkdirs()
        return dir
    }

    fun saveMeetingMeta(dir: File, title: String?, durationSeconds: Int?) {
        val json = JSONObject()
        if (title != null) json.put("title", title)
        if (durationSeconds != null) json.put("duration_seconds", durationSeconds)
        File(dir, "meeting.json").writeText(json.toString())
    }

    /**
     * Renames a meeting: updates the title in meeting.json and renames the folder
     * to reflect the new title suffix. Returns the (possibly new) directory.
     */
    fun renameMeeting(dir: File, newTitle: String?): File {
        val cleanTitle = newTitle?.trim()?.ifBlank { null }
        // Folder name format: "YYYY-MM-DD_HH-MM" (first 16 chars) + optional "_title"
        val timePart = dir.name.take(16)
        val sanitized = cleanTitle?.replace(Regex("[^a-zA-Z0-9_\\-]"), "_")?.take(30)
        val newName = if (sanitized == null) timePart else "${timePart}_${sanitized}"

        val newDir = File(rootDir, newName)
        if (newDir.absolutePath != dir.absolutePath) {
            dir.renameTo(newDir)
        }

        // Update meeting.json — preserve any existing fields (e.g. duration_seconds)
        val metaFile = File(newDir, "meeting.json")
        val json = if (metaFile.exists()) {
            try { JSONObject(metaFile.readText()) } catch (_: Exception) { JSONObject() }
        } else JSONObject()
        if (cleanTitle != null) json.put("title", cleanTitle) else json.remove("title")
        metaFile.writeText(json.toString())

        return newDir
    }

}
