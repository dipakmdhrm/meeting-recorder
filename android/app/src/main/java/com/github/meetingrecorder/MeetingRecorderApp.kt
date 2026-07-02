package com.github.meetingrecorder

import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class MeetingRecorderApp : Application() {

    lateinit var container: AppContainer
        private set

    /** Application-scoped coroutine scope for fire-and-forget background work. */
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)

        // De-orphan recordings whose previous process died mid-recording or mid-processing, so
        // their audio reappears in the library. On Dispatchers.IO — it touches the filesystem.
        appScope.launch { container.meetingRepository.recoverOrphanedRecordings() }
    }
}
