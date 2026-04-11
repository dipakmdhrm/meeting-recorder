package com.github.meetingrecorder

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.github.meetingrecorder.ui.nav.AppNavGraph
import com.github.meetingrecorder.ui.theme.MeetingRecorderTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MeetingRecorderTheme {
                AppNavGraph()
            }
        }
    }
}
