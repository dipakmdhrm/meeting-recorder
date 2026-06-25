package com.github.meetingrecorder.ui.detail

import android.app.Application
import android.media.MediaPlayer
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.MeetingRecorderApp
import com.github.meetingrecorder.data.GeminiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/** Generation (transcribe / summarize) status for the detail screen. */
sealed interface GenState {
    data object Idle : GenState
    data class Processing(val status: String) : GenState
    data class Error(val msg: String) : GenState
}

// View model for the meeting detail screen
class MeetingDetailViewModel(application: Application) : AndroidViewModel(application) {

    private val app = application as MeetingRecorderApp

    private val _transcript = MutableStateFlow<String?>(null)
    val transcript: StateFlow<String?> = _transcript.asStateFlow()

    private val _notes = MutableStateFlow<String?>(null)
    val notes: StateFlow<String?> = _notes.asStateFlow()

    private val _hasAudio = MutableStateFlow(false)
    val hasAudio: StateFlow<Boolean> = _hasAudio.asStateFlow()

    private val _genState = MutableStateFlow<GenState>(GenState.Idle)
    val genState: StateFlow<GenState> = _genState.asStateFlow()

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    private val _currentTime = MutableStateFlow("00:00")
    val currentTime: StateFlow<String> = _currentTime.asStateFlow()

    private val _totalTime = MutableStateFlow("00:00")
    val totalTime: StateFlow<String> = _totalTime.asStateFlow()

    @Volatile private var mediaPlayer: MediaPlayer? = null
    private var timeUpdaterJob: Job? = null
    private var audioFile: File? = null

    // Generation context, read from meeting.json on load() and preserved on save.
    private var meetingDir: File? = null
    private var currentTitle: String? = null
    private var durationSeconds: Int = 0

    fun load(meetingPath: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                val dir = File(meetingPath)
                meetingDir = dir
                _genState.value = GenState.Idle

                _transcript.value = File(dir, "transcript.md")
                    .takeIf { it.exists() }?.readText()
                _notes.value = File(dir, "notes.md")
                    .takeIf { it.exists() }?.readText()

                // Preserve existing title / duration so generation writes them back correctly.
                val metaFile = File(dir, "meeting.json")
                if (metaFile.exists()) {
                    try {
                        val json = JSONObject(metaFile.readText())
                        currentTitle = json.optString("title").ifBlank { null }
                        durationSeconds = if (json.has("duration_seconds")) json.getInt("duration_seconds") else 0
                    } catch (_: Exception) {
                        currentTitle = null
                        durationSeconds = 0
                    }
                } else {
                    currentTitle = null
                    durationSeconds = 0
                }

                mediaPlayer?.release()
                mediaPlayer = null
                _hasAudio.value = false

                audioFile = sequenceOf(File(dir, "recording.m4a"), File(dir, "recording.mp3"))
                    .firstOrNull { it.exists() }

                if (audioFile != null) {
                    mediaPlayer = MediaPlayer().apply {
                        setDataSource(audioFile!!.absolutePath)
                        setOnCompletionListener {
                            _isPlaying.value = false
                            _currentTime.value = "00:00"
                            timeUpdaterJob?.cancel()
                        }
                        prepare()
                        _totalTime.value = formatDuration(duration.toLong())
                    }
                    _hasAudio.value = true
                }
            }
        }
    }

    /** Transcribe the audio, then summarize — writes transcript.md and notes.md. Requires audio. */
    fun generateTranscriptAndNotes() {
        val dir = meetingDir ?: return
        val audio = audioFile ?: return
        if (app.config.apiKey.isBlank()) {
            _genState.value = GenState.Error("No Gemini API key set. Add one in Settings.")
            return
        }
        viewModelScope.launch {
            _genState.value = GenState.Processing("Starting…")
            try {
                withContext(Dispatchers.IO) {
                    val gemini = GeminiClient(app.config)
                    val transcript = gemini.transcribe(audio, extensionToMimeType(audio.extension)) {
                        _genState.value = GenState.Processing(it)
                    }
                    val notes = gemini.summarize(transcript) { _genState.value = GenState.Processing(it) }
                    File(dir, "transcript.md").writeText(transcript)
                    File(dir, "notes.md").writeText(notes)
                    maybeGenerateTitle(gemini, notes)
                    app.meetingRepository.saveMeetingMeta(dir, currentTitle, durationSeconds)
                    _transcript.value = transcript
                    _notes.value = notes
                }
                _genState.value = GenState.Idle
            } catch (e: Exception) {
                _genState.value = GenState.Error(e.message ?: "Generation failed")
            }
        }
    }

    /** Generate notes when none exist yet, reusing the already-loaded transcript. */
    fun generateNotes() = runNotesGeneration()

    /** Re-run notes generation, overwriting the existing notes.md (reuses the transcript). */
    fun regenerateNotes() = runNotesGeneration()

    private fun runNotesGeneration() {
        val dir = meetingDir ?: return
        val transcript = _transcript.value ?: return
        if (app.config.apiKey.isBlank()) {
            _genState.value = GenState.Error("No Gemini API key set. Add one in Settings.")
            return
        }
        viewModelScope.launch {
            _genState.value = GenState.Processing("Generating meeting notes…")
            try {
                withContext(Dispatchers.IO) {
                    val gemini = GeminiClient(app.config)
                    val notes = gemini.summarize(transcript) { _genState.value = GenState.Processing(it) }
                    File(dir, "notes.md").writeText(notes)
                    maybeGenerateTitle(gemini, notes)
                    app.meetingRepository.saveMeetingMeta(dir, currentTitle, durationSeconds)
                    _notes.value = notes
                }
                _genState.value = GenState.Idle
            } catch (e: Exception) {
                _genState.value = GenState.Error(e.message ?: "Generation failed")
            }
        }
    }

    /** Auto-generate a title when the meeting has none (best-effort, mirrors the main flow). */
    private suspend fun maybeGenerateTitle(gemini: GeminiClient, notes: String) {
        if (currentTitle.isNullOrBlank()) {
            try {
                currentTitle = gemini.generateTitle(notes).trim()
            } catch (_: Exception) {
            }
        }
    }

    private fun extensionToMimeType(ext: String): String = when (ext.lowercase()) {
        "mp3" -> "audio/mpeg"
        "wav" -> "audio/wav"
        "ogg" -> "audio/ogg"
        "flac" -> "audio/flac"
        "webm" -> "audio/webm"
        else -> "audio/mp4"
    }

    fun playPause() {
        mediaPlayer?.let {
            if (it.isPlaying) {
                it.pause()
                _isPlaying.value = false
                timeUpdaterJob?.cancel()
            } else {
                it.start()
                _isPlaying.value = true
                startTimeUpdater()
            }
        }
    }

    fun stop() {
        mediaPlayer?.let {
            it.pause()
            it.seekTo(0)
            _isPlaying.value = false
            _currentTime.value = "00:00"
            timeUpdaterJob?.cancel()
        }
    }

    private fun startTimeUpdater() {
        timeUpdaterJob = viewModelScope.launch {
            while (_isPlaying.value) {
                mediaPlayer?.let {
                    try {
                        _currentTime.value = formatDuration(it.currentPosition.toLong())
                    } catch (e: IllegalStateException) {
                        // Player might have been released
                    }
                }
                delay(1000)
            }
        }
    }

    private fun formatDuration(millis: Long): String {
        return String.format(
            "%02d:%02d",
            TimeUnit.MILLISECONDS.toMinutes(millis),
            TimeUnit.MILLISECONDS.toSeconds(millis) -
                    TimeUnit.MINUTES.toSeconds(TimeUnit.MILLISECONDS.toMinutes(millis))
        )
    }

    override fun onCleared() {
        super.onCleared()
        mediaPlayer?.release()
        mediaPlayer = null
        timeUpdaterJob?.cancel()
    }
}
