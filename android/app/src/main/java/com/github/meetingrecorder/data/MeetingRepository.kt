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

    /**
     * Recovers meeting directories left in a "recording in progress" state — i.e. still holding a
     * `.recording` lock file — by a previous process that died before it could finish: the app was
     * killed mid-recording, or processing failed and the directory was never de-orphaned. Such
     * directories are hidden from [listMeetings] even though their audio is on disk.
     *
     * For each locked directory whose name matches the meeting pattern:
     *  - if it holds a non-empty `recording.m4a`, the lock is removed so the audio becomes visible
     *    in the library (the user can re-transcribe later via "Use Existing Recording");
     *  - otherwise (no usable audio), the dead stub is deleted — unless it still holds some other
     *    non-empty file (e.g. manually written notes), in which case it is left untouched so real
     *    data is never destroyed.
     *
     * Safe to call when storage isn't readable yet — [listFiles] returns null and this no-ops.
     */
    fun recoverOrphanedRecordings() {
        if (!rootDir.exists()) return
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        rootDir.listFiles()?.forEach { dir ->
            if (!dir.isDirectory) return@forEach
            val lock = File(dir, ".recording")
            if (!lock.exists()) return@forEach
            if (!pattern.matches(dir.name)) return@forEach

            val audio = File(dir, "recording.m4a")
            if (audio.exists() && audio.length() > 0L) {
                lock.delete()
            } else {
                // No usable audio. A 0-byte recording.m4a counts as junk, not data.
                val hasValuableContent = dir.listFiles()?.any { it.name != ".recording" && it.length() > 0L } ?: false
                if (!hasValuableContent) dir.deleteRecursively()
            }
        }
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

    fun deleteMeeting(dir: File) {
        if (dir.canonicalPath.startsWith(rootDir.canonicalPath + File.separator)) {
            dir.deleteRecursively()
        }
    }

    /**
     * Returns the meeting directory that directly contains [file], or null if the file
     * is not inside a recognised meeting directory under [rootDir].
     */
    fun meetingDirContaining(file: File): File? {
        val parent = file.parentFile ?: return null
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        if (!pattern.matches(parent.name)) return null
        return try {
            if (parent.parentFile?.canonicalPath == rootDir.canonicalPath) parent else null
        } catch (_: Exception) { null }
    }

}
