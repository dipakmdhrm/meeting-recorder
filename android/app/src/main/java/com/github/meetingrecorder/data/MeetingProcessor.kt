package com.github.meetingrecorder.data

import android.util.Log
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

private const val TAG = "MeetingProcessor"

/** Result of the full transcribe → summarize workflow. */
data class ProcessingResult(val transcript: String, val notes: String)

/**
 * Owns the meeting-processing workflows shared by the record flow (MainViewModel) and the
 * detail-screen generation flows (MeetingDetailViewModel): Gemini transcription /
 * summarization, best-effort title generation, and the transcript.md / notes.md / meeting.json
 * disk writes. Everything is constructor-injected ([GeminiClient], [MeetingStore], dispatcher,
 * log sink) so the whole workflow is JVM unit-testable; the ViewModels keep only state management
 * and platform glue (recording service, lock files, countdowns, content resolvers).
 */
class MeetingProcessor(
    private val gemini: GeminiClient,
    private val store: MeetingStore,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    // Injectable so JVM tests don't hit android.util.Log.
    private val logWarn: (String, Exception) -> Unit = { msg, e -> Log.w(TAG, msg, e) },
) {

    /** Upload → transcribe → summarize. Progress text for the UI arrives via [onStatus]. */
    suspend fun transcribeAndSummarize(
        audioFile: File,
        mimeType: String = "audio/mp4",
        onStatus: (String) -> Unit = {},
    ): ProcessingResult {
        val transcript = gemini.transcribe(audioFile, mimeType, onStatus)
        val notes = gemini.summarize(transcript, onStatus)
        return ProcessingResult(transcript, notes)
    }

    /** Notes-only generation reusing an existing transcript — no audio upload. */
    suspend fun summarizeTranscript(
        transcript: String,
        onStatus: (String) -> Unit = {},
    ): String = gemini.summarize(transcript, onStatus)

    /**
     * Best-effort title generation: returns the trimmed title, or null on failure (logged, never
     * thrown) so a title problem can't fail the surrounding flow — the meeting stays untitled.
     */
    suspend fun generateTitle(notes: String): String? = try {
        gemini.generateTitle(notes).trim()
    } catch (e: Exception) {
        logWarn("Title generation failed; keeping meeting untitled", e)
        null
    }

    /** Writes transcript.md + notes.md and saves the meeting.json metadata. */
    suspend fun saveResults(
        meetingDir: File,
        transcript: String,
        notes: String,
        title: String?,
        durationSeconds: Int?,
    ) = withContext(ioDispatcher) {
        File(meetingDir, "transcript.md").writeText(transcript)
        File(meetingDir, "notes.md").writeText(notes)
        store.saveMeetingMeta(meetingDir, title, durationSeconds)
    }

    /** Writes notes.md and saves the meeting.json metadata (the notes-only path). */
    suspend fun saveNotes(
        meetingDir: File,
        notes: String,
        title: String?,
        durationSeconds: Int?,
    ) = withContext(ioDispatcher) {
        File(meetingDir, "notes.md").writeText(notes)
        store.saveMeetingMeta(meetingDir, title, durationSeconds)
    }
}
