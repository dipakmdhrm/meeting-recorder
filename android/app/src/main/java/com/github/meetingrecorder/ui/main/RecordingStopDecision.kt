package com.github.meetingrecorder.ui.main

import com.github.meetingrecorder.audio.RecordingPhase

/** What [MainViewModel.stopRecording] should do once the recorder has stopped. */
enum class StopOutcome {
    /** Service failed or no audio file was written — likely a storage-permission problem. */
    FILE_NOT_FOUND,

    /** File exists but is 0 bytes. */
    EMPTY,

    /** The OS silenced the mic mid-recording (e.g. an answered call) — warn instead of transcribing. */
    SILENCED,

    /** Show the pre-processing countdown so the user can cancel transcription. */
    COUNTDOWN,

    /** Proceed straight to transcription + summarization. */
    PROCESS,
}

/**
 * Pure decision for what to do after a recording stops. Extracted from the ViewModel so the branch
 * ordering — in particular that a non-empty but system-silenced recording is surfaced as a warning
 * rather than sent for transcription — can be unit-tested without Android platform APIs.
 *
 * Order matters: a missing/failed file wins over everything, a 0-byte file is reported as EMPTY
 * before the silenced check (the empty-file message is more actionable), and silencing is surfaced
 * before the countdown/processing paths.
 */
fun decideStopOutcome(
    phase: RecordingPhase,
    fileExists: Boolean,
    fileLength: Long,
    silenced: Boolean,
    countdownEnabled: Boolean,
): StopOutcome = when {
    phase == RecordingPhase.FAILED || !fileExists -> StopOutcome.FILE_NOT_FOUND
    fileLength == 0L -> StopOutcome.EMPTY
    silenced -> StopOutcome.SILENCED
    countdownEnabled -> StopOutcome.COUNTDOWN
    else -> StopOutcome.PROCESS
}
