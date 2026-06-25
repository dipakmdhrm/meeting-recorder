package com.github.meetingrecorder

import com.github.meetingrecorder.ui.detail.GenerateAction
import com.github.meetingrecorder.ui.detail.notesTabAction
import com.github.meetingrecorder.ui.detail.transcriptTabAction
import org.junit.Assert.assertEquals
import org.junit.Test

class GenerateActionDecisionTest {

    // -------------------------------------------------------------------------
    // Notes tab
    // -------------------------------------------------------------------------

    @Test
    fun `notes tab offers nothing once notes exist`() {
        // Regenerate is a separate explicit menu action, so the empty-state offer is NONE.
        assertEquals(
            GenerateAction.NONE,
            notesTabAction(hasNotes = true, hasTranscript = true, hasAudio = true),
        )
    }

    @Test
    fun `notes tab reuses existing transcript instead of re-transcribing`() {
        // A failed-then-transcribed meeting: notes missing, transcript present, audio present.
        // Prefer summarizing the transcript over re-uploading the audio.
        assertEquals(
            GenerateAction.NOTES_ONLY,
            notesTabAction(hasNotes = false, hasTranscript = true, hasAudio = true),
        )
    }

    @Test
    fun `notes tab summarizes transcript even when no audio remains`() {
        assertEquals(
            GenerateAction.NOTES_ONLY,
            notesTabAction(hasNotes = false, hasTranscript = true, hasAudio = false),
        )
    }

    @Test
    fun `notes tab transcribes and summarizes when only audio exists`() {
        // The classic failed-processing case: raw audio in the library, nothing generated yet.
        assertEquals(
            GenerateAction.TRANSCRIBE_AND_NOTES,
            notesTabAction(hasNotes = false, hasTranscript = false, hasAudio = true),
        )
    }

    @Test
    fun `notes tab offers nothing when there is neither transcript nor audio`() {
        assertEquals(
            GenerateAction.NONE,
            notesTabAction(hasNotes = false, hasTranscript = false, hasAudio = false),
        )
    }

    // -------------------------------------------------------------------------
    // Transcript tab
    // -------------------------------------------------------------------------

    @Test
    fun `transcript tab offers nothing once a transcript exists`() {
        // A transcript is never re-generated, even if the audio is still around.
        assertEquals(
            GenerateAction.NONE,
            transcriptTabAction(hasTranscript = true, hasAudio = true),
        )
    }

    @Test
    fun `transcript tab transcribes when audio exists and no transcript yet`() {
        assertEquals(
            GenerateAction.TRANSCRIBE_AND_NOTES,
            transcriptTabAction(hasTranscript = false, hasAudio = true),
        )
    }

    @Test
    fun `transcript tab offers nothing when there is no audio to transcribe`() {
        assertEquals(
            GenerateAction.NONE,
            transcriptTabAction(hasTranscript = false, hasAudio = false),
        )
    }
}
