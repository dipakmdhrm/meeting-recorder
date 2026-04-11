package com.github.meetingrecorder.ui.settings

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import com.github.meetingrecorder.MeetingRecorderApp
import com.github.meetingrecorder.data.AudioQuality
import com.github.meetingrecorder.data.Config
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class SettingsViewModel(application: Application) : AndroidViewModel(application) {

    private val config = (application as MeetingRecorderApp).config

    private val _apiKey = MutableStateFlow(config.apiKey)
    val apiKey: StateFlow<String> = _apiKey.asStateFlow()

    private val _model = MutableStateFlow(config.model)
    val model: StateFlow<String> = _model.asStateFlow()

    val availableModels: List<String> = Config.AVAILABLE_MODELS

    private val _audioQuality = MutableStateFlow(config.audioQuality)
    val audioQuality: StateFlow<AudioQuality> = _audioQuality.asStateFlow()

    val availableQualities: List<AudioQuality> = AudioQuality.entries

    private val _transcriptionPrompt = MutableStateFlow(config.transcriptionPrompt)
    val transcriptionPrompt: StateFlow<String> = _transcriptionPrompt.asStateFlow()

    private val _summarizationPrompt = MutableStateFlow(config.summarizationPrompt)
    val summarizationPrompt: StateFlow<String> = _summarizationPrompt.asStateFlow()

    private val _titlePrompt = MutableStateFlow(config.titlePrompt)
    val titlePrompt: StateFlow<String> = _titlePrompt.asStateFlow()

    fun setApiKey(key: String) {
        _apiKey.value = key
        config.apiKey = key
    }

    fun setModel(model: String) {
        _model.value = model
        config.model = model
    }

    fun setAudioQuality(quality: AudioQuality) {
        _audioQuality.value = quality
        config.audioQuality = quality
    }

    fun setTranscriptionPrompt(value: String) {
        _transcriptionPrompt.value = value
        config.transcriptionPrompt = value
    }

    fun setSummarizationPrompt(value: String) {
        _summarizationPrompt.value = value
        config.summarizationPrompt = value
    }

    fun setTitlePrompt(value: String) {
        _titlePrompt.value = value
        config.titlePrompt = value
    }
}
