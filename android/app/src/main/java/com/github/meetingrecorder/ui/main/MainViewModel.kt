package com.github.meetingrecorder.ui.main

import android.app.Application
import android.net.Uri
import android.os.Environment
import android.provider.DocumentsContract
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.MeetingRecorderApp
import com.github.meetingrecorder.audio.AudioRecorder
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

sealed class RecordingState {
    data object Ready : RecordingState()
    data class Recording(val elapsedSecs: Int) : RecordingState()
    data class Countdown(val remainingSecs: Int) : RecordingState()
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
    private var countdownJob: Job? = null
    private var pendingAudioFile: File? = null
    private var currentMeetingDir: File? = null
    private var lockFile: File? = null
    private var currentTitle: String? = null
    private var durationSeconds: Int = 0
    // True when processing a file that already lives inside a meeting directory (no copy was made).
    // discardResults() must not delete the directory in this case.
    private var isInPlace: Boolean = false

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
            audioRecorder.start(meetingDir, app.config.audioQuality.bitrate)
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
            app.config.processingCountdownEnabled -> startCountdown(audioFile)
            else -> processRecording(audioFile)
        }
    }

    private fun startCountdown(audioFile: File) {
        pendingAudioFile = audioFile
        var remaining = 5
        _state.value = RecordingState.Countdown(remaining)
        countdownJob = viewModelScope.launch {
            while (remaining > 0) {
                delay(1_000)
                remaining--
                if (remaining > 0) {
                    _state.value = RecordingState.Countdown(remaining)
                } else {
                    pendingAudioFile = null
                    processRecording(audioFile)
                }
            }
        }
    }

    fun cancelCountdown() {
        countdownJob?.cancel()
        countdownJob = null
        val audioFile = pendingAudioFile
        pendingAudioFile = null
        if (audioFile == null) {
            _state.value = RecordingState.Ready
            return
        }
        // Save audio only — no transcription
        val meetingDir = currentMeetingDir ?: run {
            _state.value = RecordingState.Ready
            return
        }
        viewModelScope.launch {
            try {
                app.meetingRepository.saveMeetingMeta(meetingDir, currentTitle, durationSeconds)
                lockFile?.delete()
                lockFile = null
                _state.value = RecordingState.Ready
            } catch (e: Exception) {
                _state.value = RecordingState.Error("Save failed: ${e.message}")
            }
        }
    }

    fun processExistingRecording(uri: Uri) {
        if (!hasApiKey()) {
            _state.value = RecordingState.Error("No Gemini API key set. Add one in Settings.")
            return
        }

        // If the URI resolves to a file that already lives inside a meeting directory,
        // process it in-place — no copy, no new directory.
        val resolvedFile = resolveUriToFile(uri)
        val existingDir = resolvedFile?.let { app.meetingRepository.meetingDirContaining(it) }
        if (existingDir != null && resolvedFile != null) {
            processInPlace(existingDir, resolvedFile)
        } else {
            copyAndProcess(uri)
        }
    }

    private fun processInPlace(meetingDir: File, audioFile: File) {
        currentMeetingDir = meetingDir
        isInPlace = true

        // Preserve existing title and duration so saveResults() writes them back correctly.
        val metaFile = File(meetingDir, "meeting.json")
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

        // No lock file: the meeting remains visible in the list (with audio only) while
        // processing runs. If processing fails the original audio-only meeting is unaffected.
        lockFile = null
        processRecording(audioFile, extensionToMimeType(audioFile.extension))
    }

    private fun copyAndProcess(uri: Uri) {
        val meetingDir = try {
            app.meetingRepository.createMeetingDir(null)
        } catch (e: Exception) {
            _state.value = RecordingState.Error("Could not create recording folder: ${e.message}")
            return
        }
        currentMeetingDir = meetingDir
        currentTitle = null
        durationSeconds = 0
        isInPlace = false
        lockFile = File(meetingDir, ".recording").also { it.createNewFile() }

        viewModelScope.launch {
            try {
                val mimeType = normalizeMimeType(app.contentResolver.getType(uri) ?: "audio/mp4")
                val audioFile = withContext(Dispatchers.IO) {
                    val dest = File(meetingDir, "recording.${mimeTypeToExtension(mimeType)}")
                    app.contentResolver.openInputStream(uri)?.use { input ->
                        dest.outputStream().use { output -> input.copyTo(output) }
                    } ?: throw RuntimeException("Could not open selected file")
                    if (dest.length() == 0L) throw RuntimeException("Selected file is empty")
                    dest
                }
                processRecording(audioFile, mimeType)
            } catch (e: Exception) {
                lockFile?.delete()
                lockFile = null
                currentMeetingDir?.deleteRecursively()
                currentMeetingDir = null
                _state.value = RecordingState.Error(e.message ?: "Failed to import recording")
            }
        }
    }

    /** Resolves a content/file URI to a File on primary external storage, or null if not possible. */
    private fun resolveUriToFile(uri: Uri): File? {
        if (uri.scheme == "file") return File(uri.path ?: return null)
        if (uri.scheme == "content" &&
            uri.authority == "com.android.externalstorage.documents") {
            val docId = DocumentsContract.getDocumentId(uri)
            val parts = docId.split(":")
            if (parts.size == 2 && parts[0] == "primary") {
                return File(Environment.getExternalStorageDirectory(), parts[1])
            }
        }
        return null
    }

    private fun normalizeMimeType(mimeType: String): String = when (mimeType) {
        "audio/m4a", "audio/x-m4a" -> "audio/mp4"
        "audio/x-wav" -> "audio/wav"
        "audio/mp3" -> "audio/mpeg"
        else -> mimeType
    }

    private fun mimeTypeToExtension(mimeType: String): String = when (mimeType) {
        "audio/mpeg" -> "mp3"
        "audio/wav" -> "wav"
        "audio/ogg" -> "ogg"
        "audio/flac" -> "flac"
        "audio/webm" -> "webm"
        else -> "m4a"
    }

    private fun extensionToMimeType(ext: String): String = when (ext.lowercase()) {
        "mp3" -> "audio/mpeg"
        "wav" -> "audio/wav"
        "ogg" -> "audio/ogg"
        "flac" -> "audio/flac"
        "webm" -> "audio/webm"
        else -> "audio/mp4"
    }

    private fun processRecording(audioFile: File, mimeType: String = "audio/mp4") {
        viewModelScope.launch {
            try {
                val gemini = GeminiClient(app.config)

                val transcript = gemini.transcribe(audioFile, mimeType) { status ->
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
                isInPlace = false
                _state.value = RecordingState.Ready
            } catch (e: Exception) {
                _state.value = RecordingState.Error("Save failed: ${e.message}")
            }
        }
    }

    fun discardResults() {
        lockFile?.delete()
        lockFile = null
        if (!isInPlace) {
            // New recording or copied import — remove the directory we created.
            currentMeetingDir?.deleteRecursively()
        }
        // In-place: leave the existing meeting directory untouched (audio is still there).
        isInPlace = false
        currentMeetingDir = null
        _state.value = RecordingState.Ready
    }

    fun dismissError() {
        isInPlace = false
        _state.value = RecordingState.Ready
    }
}
