package com.github.meetingrecorder

import android.app.Application
import android.os.Environment
import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.MeetingRepository
import java.io.File

class MeetingRecorderApp : Application() {

    lateinit var config: Config
        private set

    lateinit var meetingRepository: MeetingRepository
        private set

    override fun onCreate() {
        super.onCreate()
        config = Config(this)
        meetingRepository = MeetingRepository(
            File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS),
                "Meetings"
            )
        )
    }
}
