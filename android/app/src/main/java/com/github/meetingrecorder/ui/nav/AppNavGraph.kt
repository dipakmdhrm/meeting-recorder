package com.github.meetingrecorder.ui.nav

import androidx.compose.runtime.Composable
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.github.meetingrecorder.ui.detail.MeetingDetailScreen
import com.github.meetingrecorder.ui.main.MainScreen
import com.github.meetingrecorder.ui.meetings.MeetingsScreen
import com.github.meetingrecorder.ui.settings.SettingsScreen

@Composable
fun AppNavGraph() {
    val navController = rememberNavController()

    NavHost(navController = navController, startDestination = "main") {
        composable("main") {
            MainScreen(
                onNavigateToSettings = { navController.navigate("settings") },
                onNavigateToMeetings = { navController.navigate("meetings") },
            )
        }
        composable("settings") {
            SettingsScreen(onBack = { navController.popBackStack() })
        }
        composable("meetings") {
            MeetingsScreen(
                onBack = { navController.popBackStack() },
                onMeetingClick = { absolutePath ->
                    // Encode slashes so the path survives NavHost route matching
                    val encoded = absolutePath.replace("/", "%2F")
                    navController.navigate("meeting_detail/$encoded")
                },
            )
        }
        composable("meeting_detail/{meetingPath}") { backStackEntry ->
            val path = backStackEntry.arguments
                ?.getString("meetingPath")
                ?.replace("%2F", "/")
                ?: ""
            MeetingDetailScreen(
                meetingPath = path,
                onBack = { navController.popBackStack() },
            )
        }
    }
}
