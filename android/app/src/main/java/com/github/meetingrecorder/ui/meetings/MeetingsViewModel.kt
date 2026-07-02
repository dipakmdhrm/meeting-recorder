package com.github.meetingrecorder.ui.meetings

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.R
import com.github.meetingrecorder.data.Meeting
import com.github.meetingrecorder.data.MeetingRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// AndroidViewModel only because rename errors need getString; the repository is injected.
class MeetingsViewModel(
    application: Application,
    private val repo: MeetingRepository,
) : AndroidViewModel(application) {

    private val _meetings = MutableStateFlow<List<Meeting>>(emptyList())
    val meetings: StateFlow<List<Meeting>> = _meetings.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    init {
        loadMeetings()
    }

    fun loadMeetings() {
        viewModelScope.launch {
            _isLoading.value = true
            _meetings.value = withContext(Dispatchers.IO) { repo.listMeetings() }
            _isLoading.value = false
        }
    }

    fun renameMeeting(meeting: Meeting, newTitle: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { repo.renameMeeting(meeting.path, newTitle) }
                loadMeetings()
            } catch (e: Exception) {
                _errorMessage.value = getApplication<Application>().getString(R.string.error_rename_failed)
            }
        }
    }

    fun deleteMeeting(meeting: Meeting) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { repo.deleteMeeting(meeting.path) }
            loadMeetings()
        }
    }

    fun clearError() {
        _errorMessage.value = null
    }
}
