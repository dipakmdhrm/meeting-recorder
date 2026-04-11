package com.github.meetingrecorder.ui.main

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.MeetingRecorderApp
import com.github.meetingrecorder.audio.AudioRecorder
import com.github.meetingrecorder.data.GeminiClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File

sealed class RecordingState {
    data object Ready : RecordingState()
    data class Recording(val elapsedSecs: Int) : RecordingState()
    data class Processing(val statusMsg: String) : RecordingState()
    data class Done(val transcript: String, val notes: String) : RecordingState()
    data class Error(val msg: String) : RecordingState()
}

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val app = application as MeetingRecorderApp
    private val audioRecorder = AudioRecorder(application)

    private val _state = MutableStateFlow<RecordingState>(RecordingState.Ready)
    val state: StateFlow<RecordingState> = _state.asStateFlow()

    private var timerJob: Job? = null
    private var currentMeetingDir: File? = null
    private var lockFile: File? = null
    private var currentTitle: String? = null
    private var durationSeconds: Int = 0

    fun hasApiKey(): Boolean = app.config.apiKey.isNotBlank()

    fun startRecording(title: String?) {
        if (!hasApiKey()) {
            _state.value = RecordingState.Error("No Gemini API key set. Add one in Settings.")
            return
        }

        currentTitle = title
        durationSeconds = 0

        val meetingDir = try {
            app.meetingRepository.createMeetingDir(title)
        } catch (e: Exception) {
            _state.value = RecordingState.Error("Could not create recording folder: ${e.message}")
            return
        }
        currentMeetingDir = meetingDir

        lockFile = File(meetingDir, ".recording").also { it.createNewFile() }

        try {
            audioRecorder.start(meetingDir)
        } catch (e: Exception) {
            lockFile?.delete()
            lockFile = null
            meetingDir.deleteRecursively()
            currentMeetingDir = null
            _state.value = RecordingState.Error("Could not start recording: ${e.message}")
            return
        }

        _state.value = RecordingState.Recording(0)
        timerJob = viewModelScope.launch {
            while (true) {
                delay(1_000)
                durationSeconds++
                _state.value = RecordingState.Recording(durationSeconds)
            }
        }
    }

    fun stopRecording() {
        timerJob?.cancel()
        timerJob = null
        audioRecorder.stop()

        val meetingDir = currentMeetingDir ?: run {
            _state.value = RecordingState.Error("No meeting directory")
            return
        }
        val audioFile = File(meetingDir, "recording.m4a")
        when {
            !audioFile.exists() ->
                _state.value = RecordingState.Error("Recording file not found. Check storage permission.")
            audioFile.length() == 0L ->
                _state.value = RecordingState.Error(
                    "Recording is empty (0 bytes). " +
                    "Grant 'All files access' in Settings → Apps → Meeting Recorder → Permissions."
                )
            else -> processRecording(audioFile)
        }
    }

    private fun processRecording(audioFile: File) {
        viewModelScope.launch {
            try {
                val gemini = GeminiClient(app.config)

                val transcript = gemini.transcribe(audioFile) { status ->
                    _state.value = RecordingState.Processing(status)
                }
                val notes = gemini.summarize(transcript) { status ->
                    _state.value = RecordingState.Processing(status)
                }

                // Auto-generate title when none was provided
                if (currentTitle == null) {
                    try {
                        currentTitle = gemini.generateTitle(notes).trim()
                    } catch (_: Exception) {
                    }
                }

                _state.value = RecordingState.Done(transcript, notes)
            } catch (e: Exception) {
                _state.value = RecordingState.Error(e.message ?: "Processing failed")
            }
        }
    }

    fun saveResults() {
        val done = _state.value as? RecordingState.Done ?: return
        val meetingDir = currentMeetingDir ?: return

        viewModelScope.launch {
            try {
                File(meetingDir, "transcript.md").writeText(done.transcript)
                File(meetingDir, "notes.md").writeText(done.notes)
                app.meetingRepository.saveMeetingMeta(meetingDir, currentTitle, durationSeconds)
                lockFile?.delete()
                lockFile = null
                _state.value = RecordingState.Ready
            } catch (e: Exception) {
                _state.value = RecordingState.Error("Save failed: ${e.message}")
            }
        }
    }

    fun discardResults() {
        lockFile?.delete()
        lockFile = null
        currentMeetingDir?.deleteRecursively()
        currentMeetingDir = null
        _state.value = RecordingState.Ready
    }

    fun dismissError() {
        _state.value = RecordingState.Ready
    }
}
