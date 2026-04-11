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
        val datePart = "%04d-%02d-%02d".format(now.year, now.monthValue, now.dayOfMonth)
        val timePart = "%02d-%02d".format(now.hour, now.minute)

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

}
