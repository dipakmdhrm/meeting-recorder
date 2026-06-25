package com.github.meetingrecorder.data

import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime

class MeetingRepository(private val rootDir: File) {

    // Captured when the repository (a per-process singleton) is constructed — effectively app launch.
    // recoverOrphanedRecordings() only acts on directories locked *before* this moment, so a recording
    // the user starts during this session (its `.recording` lock is newer) is never mistaken for an
    // orphan and deleted out from under the active recorder.
    private val sessionStartTime = System.currentTimeMillis()

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
     * Only directories locked before [sessionStartTime] are considered, so an in-progress recording
     * from the current session is never touched. For each such locked directory whose name matches
     * the meeting pattern:
     *  - if it holds a non-empty `recording.m4a`, the lock is removed so the audio becomes visible
     *    in the library (the user can re-transcribe later via "Use Existing Recording");
     *  - otherwise (no usable audio) the lock is still removed if the directory holds other real
     *    content (e.g. notes/transcript written before a crash), so that content isn't hidden
     *    forever; if there is nothing worth keeping, the dead stub is deleted.
     *
     * Safe to call when storage isn't readable yet — [listFiles] returns null and this no-ops.
     */
    fun recoverOrphanedRecordings() {
        if (!rootDir.exists()) return
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        rootDir.listFiles()?.forEach { dir ->
            if (!dir.isDirectory) return@forEach
            val lock = File(dir, ".recording")
            // Skip dirs with no lock, and any locked during this session — that lock belongs to a
            // recording the user just started, whose audio may not be on disk yet.
            if (!lock.exists() || lock.lastModified() >= sessionStartTime) return@forEach
            if (!pattern.matches(dir.name)) return@forEach

            val audio = File(dir, "recording.m4a")
            if (audio.exists() && audio.length() > 0L) {
                lock.delete()
            } else {
                // No usable audio (missing or 0 bytes). Unlock if the dir still holds real user
                // content so it isn't hidden forever; otherwise it's a dead stub — delete it. A
                // 0-byte recording.m4a and the meeting.json metadata stub don't count as content.
                val hasValuableContent = dir.listFiles()?.any {
                    it.name != ".recording" && it.name != "recording.m4a" &&
                        it.name != "meeting.json" && it.length() > 0L
                } ?: false
                if (hasValuableContent) lock.delete() else dir.deleteRecursively()
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
