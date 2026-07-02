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

    NavHost(navController = navController, startDestination = Routes.MAIN) {
        composable(Routes.MAIN) {
            MainScreen(
                onNavigateToSettings = { navController.navigate(Routes.SETTINGS) },
                onNavigateToMeetings = { navController.navigate(Routes.MEETINGS) },
            )
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(onBack = { navController.popBackStack() })
        }
        composable(Routes.MEETINGS) {
            MeetingsScreen(
                onBack = { navController.popBackStack() },
                onMeetingClick = { absolutePath ->
                    navController.navigate(Routes.meetingDetail(absolutePath))
                },
            )
        }
        composable(Routes.MEETING_DETAIL_PATTERN) { backStackEntry ->
            val path = backStackEntry.arguments
                ?.getString(Routes.MEETING_PATH_ARG)
                ?.let(Routes::decodeMeetingPath)
                ?: ""
            MeetingDetailScreen(
                meetingPath = path,
                onBack = { navController.popBackStack() },
            )
        }
    }
}
