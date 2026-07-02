package com.github.meetingrecorder.util

/** Maps an audio-file extension to the MIME type sent to Gemini. Unknown extensions → audio/mp4. */
fun extensionToMimeType(ext: String): String = when (ext.lowercase()) {
    "mp3" -> "audio/mpeg"
    "wav" -> "audio/wav"
    "ogg" -> "audio/ogg"
    "flac" -> "audio/flac"
    "webm" -> "audio/webm"
    else -> "audio/mp4"
}

/**
 * Normalizes MIME-type aliases reported by content resolvers to the canonical audio types
 * Gemini expects. MIME types are case-insensitive (RFC 2045), so matching is done lowercased.
 */
fun normalizeMimeType(mimeType: String): String = when (val lowered = mimeType.lowercase()) {
    "audio/m4a", "audio/x-m4a" -> "audio/mp4"
    "audio/x-wav" -> "audio/wav"
    "audio/mp3" -> "audio/mpeg"
    else -> lowered
}
