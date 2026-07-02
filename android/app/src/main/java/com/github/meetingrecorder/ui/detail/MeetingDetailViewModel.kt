package com.github.meetingrecorder.ui.detail

import android.media.MediaPlayer
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.MeetingMeta
import com.github.meetingrecorder.data.MeetingProcessor
import com.github.meetingrecorder.util.extensionToMimeType
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.TimeUnit

/** Generation (transcribe / summarize) status for the detail screen. */
sealed interface GenState {
    data object Idle : GenState
    data class Processing(val status: String) : GenState
    data class Error(val msg: String) : GenState
}

// View model for the meeting detail screen. Plain ViewModel — no Context needed (MediaPlayer is
// context-free); dependencies are constructor-injected via appViewModelFactory. The generation
// workflows (transcribe/summarize/title/disk writes) live in MeetingProcessor; this class keeps
// only screen state and playback.
class MeetingDetailViewModel(
    private val config: Config,
    private val processor: MeetingProcessor,
) : ViewModel() {

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
    private var generationJob: Job? = null

    // Everything read off disk by load(), so file I/O happens on IO and the
    // ViewModel state assignments happen on the Main thread only.
    private data class LoadedMeeting(
        val transcript: String?,
        val notes: String?,
        val title: String?,
        val durationSeconds: Int,
        val audioFile: File?,
        val player: MediaPlayer?,
    )

    fun load(meetingPath: String) {
        // Cancel any in-flight generation from a previously loaded meeting so its background
        // writes can't clobber the meeting we're loading now.
        generationJob?.cancel()
        // Reset playback state from the previous meeting: without this, a meeting
        // that was playing leaves _isPlaying true and the timer running, so the
        // newly loaded meeting shows as "playing" on a player that never started.
        timeUpdaterJob?.cancel()
        timeUpdaterJob = null
        _isPlaying.value = false
        _currentTime.value = "00:00"
        val dir = File(meetingPath)
        meetingDir = dir
        _genState.value = GenState.Idle
        viewModelScope.launch {
            val loaded = withContext(Dispatchers.IO) {
                val transcript = File(dir, "transcript.md").takeIf { it.exists() }?.readText()
                val notes = File(dir, "notes.md").takeIf { it.exists() }?.readText()

                // Preserve existing title / duration so generation writes them back correctly.
                val meta = File(dir, "meeting.json").takeIf { it.exists() }?.let { metaFile ->
                    try {
                        MeetingMeta.parse(metaFile.readText())
                    } catch (_: Exception) {
                        null
                    }
                }

                val audio = sequenceOf(File(dir, "recording.m4a"), File(dir, "recording.mp3"))
                    .firstOrNull { it.exists() }
                val player = audio?.let {
                    MediaPlayer().apply {
                        setDataSource(it.absolutePath)
                        prepare()
                    }
                }
                LoadedMeeting(transcript, notes, meta?.title, meta?.durationSeconds ?: 0, audio, player)
            }

            _transcript.value = loaded.transcript
            _notes.value = loaded.notes
            currentTitle = loaded.title
            durationSeconds = loaded.durationSeconds

            mediaPlayer?.release()
            mediaPlayer = loaded.player
            audioFile = loaded.audioFile
            loaded.player?.setOnCompletionListener {
                _isPlaying.value = false
                _currentTime.value = "00:00"
                timeUpdaterJob?.cancel()
            }
            _totalTime.value = loaded.player?.let { formatDuration(it.duration.toLong()) } ?: "00:00"
            _hasAudio.value = loaded.player != null
        }
    }

    /** Transcribe the audio, then summarize — writes transcript.md and notes.md. Requires audio. */
    fun generateTranscriptAndNotes() {
        if (_genState.value is GenState.Processing) return
        val dir = meetingDir ?: return
        val audio = audioFile ?: return
        if (config.apiKey.isBlank()) {
            _genState.value = GenState.Error("No Gemini API key set. Add one in Settings.")
            return
        }
        generationJob = viewModelScope.launch {
            _genState.value = GenState.Processing("Starting…")
            try {
                val result = processor.transcribeAndSummarize(audio, extensionToMimeType(audio.extension)) {
                    _genState.value = GenState.Processing(it)
                }
                maybeGenerateTitle(result.notes)
                processor.saveResults(dir, result.transcript, result.notes, currentTitle, durationSeconds)
                _transcript.value = result.transcript
                _notes.value = result.notes
                _genState.value = GenState.Idle
            } catch (e: CancellationException) {
                throw e // a cancelled job (e.g. reload) must not surface as an error
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
        if (_genState.value is GenState.Processing) return
        val dir = meetingDir ?: return
        val transcript = _transcript.value ?: return
        if (config.apiKey.isBlank()) {
            _genState.value = GenState.Error("No Gemini API key set. Add one in Settings.")
            return
        }
        generationJob = viewModelScope.launch {
            _genState.value = GenState.Processing("Generating meeting notes…")
            try {
                val notes = processor.summarizeTranscript(transcript) { _genState.value = GenState.Processing(it) }
                maybeGenerateTitle(notes)
                processor.saveNotes(dir, notes, currentTitle, durationSeconds)
                _notes.value = notes
                _genState.value = GenState.Idle
            } catch (e: CancellationException) {
                throw e // a cancelled job (e.g. reload) must not surface as an error
            } catch (e: Exception) {
                _genState.value = GenState.Error(e.message ?: "Generation failed")
            }
        }
    }

    /** Auto-generate a title when the meeting has none (best-effort, mirrors the main flow). */
    private suspend fun maybeGenerateTitle(notes: String) {
        if (currentTitle.isNullOrBlank()) {
            processor.generateTitle(notes)?.let { currentTitle = it }
        }
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
                TimeUnit.MINUTES.toSeconds(TimeUnit.MILLISECONDS.toMinutes(millis)),
        )
    }

    override fun onCleared() {
        super.onCleared()
        mediaPlayer?.release()
        mediaPlayer = null
        timeUpdaterJob?.cancel()
    }
}
