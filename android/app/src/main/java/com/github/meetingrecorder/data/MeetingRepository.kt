package com.github.meetingrecorder.data

import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import java.time.Month
import java.time.format.TextStyle
import java.util.Locale

class MeetingRepository(private val rootDir: File) {

    fun listMeetings(): List<Meeting> {
        val meetings = mutableListOf<Meeting>()
        if (!rootDir.exists()) return meetings

        // Traverse YYYY/MonthName/DD/HH-MM[_title]/ structure
        rootDir.listFiles()?.forEach { yearDir ->
            val year = yearDir.name.toIntOrNull() ?: return@forEach
            yearDir.listFiles()?.forEach { monthDir ->
                val month = parseMonth(monthDir.name) ?: return@forEach
                monthDir.listFiles()?.forEach { dayDir ->
                    val day = dayDir.name.toIntOrNull() ?: return@forEach
                    dayDir.listFiles()?.forEach { meetingDir ->
                        if (!meetingDir.isDirectory) return@forEach
                        // Skip in-progress recordings
                        if (File(meetingDir, ".recording").exists()) return@forEach

                        val folderName = meetingDir.name
                        val timePart = folderName.substringBefore("_")
                        val hour = timePart.substringBefore("-").toIntOrNull() ?: return@forEach
                        val minute = timePart.substringAfter("-").toIntOrNull() ?: return@forEach

                        val date = LocalDateTime.of(year, month, day, hour, minute)

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
                                timeLabel = folderName,
                                date = date,
                                title = title,
                                hasNotes = File(meetingDir, "notes.md").exists(),
                                hasTranscript = File(meetingDir, "transcript.md").exists(),
                                hasAudio = File(meetingDir, "recording.m4a").exists(),
                                durationSeconds = durationSeconds,
                            )
                        )
                    }
                }
            }
        }

        return meetings.sortedByDescending { it.date }
    }

    fun createMeetingDir(title: String?): File {
        val now = LocalDateTime.now()
        val year = now.year.toString()
        val monthName = now.month.getDisplayName(TextStyle.FULL, Locale.US)
        val day = now.dayOfMonth.toString().padStart(2, '0')
        val time = "%02d-%02d".format(now.hour, now.minute)

        val folderName = if (title != null) {
            val sanitized = title.replace(Regex("[^a-zA-Z0-9_\\-]"), "_").take(30)
            "${time}_${sanitized}"
        } else {
            time
        }

        val dir = File(rootDir, "$year/$monthName/$day/$folderName")
        dir.mkdirs()
        return dir
    }

    fun saveMeetingMeta(dir: File, title: String?, durationSeconds: Int?) {
        val json = JSONObject()
        if (title != null) json.put("title", title)
        if (durationSeconds != null) json.put("duration_seconds", durationSeconds)
        File(dir, "meeting.json").writeText(json.toString())
    }

    private fun parseMonth(name: String): Month? =
        try {
            Month.valueOf(name.uppercase(Locale.US))
        } catch (_: Exception) {
            null
        }
}
