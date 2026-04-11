package com.github.meetingrecorder.data

import java.io.File
import java.time.LocalDateTime

data class Meeting(
    val path: File,
    val timeLabel: String,
    val date: LocalDateTime,
    val title: String?,
    val hasNotes: Boolean,
    val hasTranscript: Boolean,
    val hasAudio: Boolean,
    val durationSeconds: Int?,
)
