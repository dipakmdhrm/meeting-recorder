package com.github.meetingrecorder.ui.detail

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class MeetingDetailViewModel(application: Application) : AndroidViewModel(application) {

    private val _transcript = MutableStateFlow<String?>(null)
    val transcript: StateFlow<String?> = _transcript.asStateFlow()

    private val _notes = MutableStateFlow<String?>(null)
    val notes: StateFlow<String?> = _notes.asStateFlow()

    fun load(meetingPath: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                val dir = File(meetingPath)
                _transcript.value = File(dir, "transcript.md")
                    .takeIf { it.exists() }?.readText()
                _notes.value = File(dir, "notes.md")
                    .takeIf { it.exists() }?.readText()
            }
        }
    }
}
