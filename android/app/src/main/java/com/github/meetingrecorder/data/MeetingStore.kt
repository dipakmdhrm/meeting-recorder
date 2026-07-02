package com.github.meetingrecorder.data

import java.io.File

/**
 * Minimal storage seam covering the repository operations [MeetingProcessor] uses. Today the only
 * implementation is the direct-file [MeetingRepository]; the interface exists so a future
 * SAF-backed store (the Play-Store-safe tree grant over the same `Documents/Meetings/` layout)
 * can slot in without touching the processing workflow. It grows only as MeetingProcessor needs
 * more operations.
 */
interface MeetingStore {
    /** Writes `meeting.json` in [dir]; null [title]/[durationSeconds] fields are omitted. */
    fun saveMeetingMeta(dir: File, title: String?, durationSeconds: Int?)
}
