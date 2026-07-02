package com.github.meetingrecorder.ui.nav

/**
 * Central route definitions for [AppNavGraph] so no call site hand-builds route strings.
 *
 * The helpers are pure string functions (no `android.net.Uri`) so they are JVM-testable
 * (`RoutesTest`); slashes are escaped as `%2F` — exactly as before — so absolute meeting paths
 * survive NavHost route matching.
 */
object Routes {
    const val MAIN = "main"
    const val SETTINGS = "settings"
    const val MEETINGS = "meetings"

    const val MEETING_PATH_ARG = "meetingPath"
    const val MEETING_DETAIL_PATTERN = "meeting_detail/{$MEETING_PATH_ARG}"

    /** Builds the meeting-detail route for [absolutePath], encoding slashes as `%2F`. */
    fun meetingDetail(absolutePath: String): String =
        "meeting_detail/${encodeMeetingPath(absolutePath)}"

    /** Encodes a filesystem path for embedding in a route (slashes → `%2F`). */
    fun encodeMeetingPath(path: String): String = path.replace("/", "%2F")

    /** Decodes a `{meetingPath}` nav argument back to a filesystem path (no-op if already decoded). */
    fun decodeMeetingPath(encoded: String): String = encoded.replace("%2F", "/")
}
