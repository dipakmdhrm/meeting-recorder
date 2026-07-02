package com.github.meetingrecorder

import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelProvider.AndroidViewModelFactory.Companion.APPLICATION_KEY
import androidx.lifecycle.viewmodel.CreationExtras
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.github.meetingrecorder.ui.detail.MeetingDetailViewModel
import com.github.meetingrecorder.ui.main.MainViewModel
import com.github.meetingrecorder.ui.meetings.MeetingsViewModel
import com.github.meetingrecorder.ui.settings.SettingsViewModel

private val CreationExtras.meetingRecorderApp: MeetingRecorderApp
    get() = checkNotNull(this[APPLICATION_KEY]) as MeetingRecorderApp

/**
 * Shared factory wiring every ViewModel to its [AppContainer] dependencies. This is the single
 * place that touches [MeetingRecorderApp]; ViewModels take plain constructor parameters and only
 * keep an `Application` where a Context is genuinely needed (service start, getString,
 * contentResolver).
 */
val appViewModelFactory: ViewModelProvider.Factory = viewModelFactory {
    initializer {
        val app = meetingRecorderApp
        MainViewModel(app, app.container.config, app.container.meetingRepository, app.container.geminiClient)
    }
    initializer {
        val app = meetingRecorderApp
        MeetingDetailViewModel(app.container.config, app.container.meetingRepository, app.container.geminiClient)
    }
    initializer {
        val app = meetingRecorderApp
        MeetingsViewModel(app, app.container.meetingRepository)
    }
    initializer {
        SettingsViewModel(meetingRecorderApp.container.config)
    }
}
