package com.github.meetingrecorder.ui.detail

import android.app.Application
import android.media.MediaPlayer
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
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

// View model for the meeting detail screen
class MeetingDetailViewModel(application: Application) : AndroidViewModel(application) {

    private val _transcript = MutableStateFlow<String?>(null)
    val transcript: StateFlow<String?> = _transcript.asStateFlow()

    private val _notes = MutableStateFlow<String?>(null)
    val notes: StateFlow<String?> = _notes.asStateFlow()

    private val _hasAudio = MutableStateFlow(false)
    val hasAudio: StateFlow<Boolean> = _hasAudio.asStateFlow()

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    private val _currentTime = MutableStateFlow("00:00")
    val currentTime: StateFlow<String> = _currentTime.asStateFlow()

    private val _totalTime = MutableStateFlow("00:00")
    val totalTime: StateFlow<String> = _totalTime.asStateFlow()

    @Volatile private var mediaPlayer: MediaPlayer? = null
    private var timeUpdaterJob: Job? = null
    private var audioFile: File? = null

    fun load(meetingPath: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                val dir = File(meetingPath)
                _transcript.value = File(dir, "transcript.md")
                    .takeIf { it.exists() }?.readText()
                _notes.value = File(dir, "notes.md")
                    .takeIf { it.exists() }?.readText()

                audioFile = sequenceOf(File(dir, "recording.m4a"), File(dir, "recording.mp3"))
                    .firstOrNull { it.exists() }

                if (audioFile != null) {
                    mediaPlayer = MediaPlayer().apply {
                        setDataSource(audioFile!!.absolutePath)
                        prepare()
                        _totalTime.value = formatDuration(duration.toLong())
                    }
                    _hasAudio.value = true
                }
            }
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
            if (it.isPlaying) {
                it.stop()
                try {
                    it.prepare() // To allow playing again
                } catch (e: Exception) {
                    // Ignore, player might be in a bad state
                }
            }
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
