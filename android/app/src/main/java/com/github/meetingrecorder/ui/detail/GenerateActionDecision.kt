package com.github.meetingrecorder.ui.detail

/** Which generation button the detail screen should offer in an empty Notes/Transcript tab. */
enum class GenerateAction {
    /** Nothing to offer — no audio to transcribe and no transcript to summarize. */
    NONE,

    /** Transcribe the audio then summarize — writes both transcript.md and notes.md. */
    TRANSCRIBE_AND_NOTES,

    /** Summarize the existing transcript into notes — no audio re-upload. */
    NOTES_ONLY,
}

/**
 * Pure policy for the Notes tab's empty-state button. Extracted from the Composable so the rule —
 * prefer reusing an existing transcript over re-transcribing the audio — can be unit-tested without
 * Compose. When notes already exist there is nothing to generate here (regenerate is a separate,
 * explicit menu action), so this returns [GenerateAction.NONE].
 */
fun notesTabAction(hasNotes: Boolean, hasTranscript: Boolean, hasAudio: Boolean): GenerateAction = when {
    hasNotes -> GenerateAction.NONE
    hasTranscript -> GenerateAction.NOTES_ONLY
    hasAudio -> GenerateAction.TRANSCRIBE_AND_NOTES
    else -> GenerateAction.NONE
}

/**
 * Pure policy for the Transcript tab's empty-state button. A transcript can only come from the audio,
 * so the only offer is [GenerateAction.TRANSCRIBE_AND_NOTES], and only when audio is present.
 */
fun transcriptTabAction(hasTranscript: Boolean, hasAudio: Boolean): GenerateAction = when {
    hasTranscript -> GenerateAction.NONE
    hasAudio -> GenerateAction.TRANSCRIBE_AND_NOTES
    else -> GenerateAction.NONE
}
