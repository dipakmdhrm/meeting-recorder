package com.github.meetingrecorder.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Shared lenient JSON codec for all meeting.json and Gemini wire handling. Unknown keys are
 * ignored and primitives are coerced where possible, matching the tolerance of the org.json
 * parser this codebase used before migrating to kotlinx.serialization. Defaults are not encoded
 * (Json's default), so null/absent fields are omitted from output — same as org.json's
 * put-only-what-you-have behavior.
 */
internal val lenientJson = Json {
    ignoreUnknownKeys = true
    isLenient = true
}

/**
 * The meeting.json metadata schema. Field names are the exact on-disk format shared with the
 * Linux app (snake_case `duration_seconds`) — do not rename them.
 */
@Serializable
data class MeetingMeta(
    val title: String? = null,
    @SerialName("duration_seconds") val durationSeconds: Int? = null,
) {
    companion object {
        /**
         * Parses meeting.json text. Blank titles normalize to null (matching the historical
         * `optString("title").ifBlank { null }` behavior). Throws on malformed JSON — callers
         * keep their own try/catch so broken metadata is skipped, never fatal.
         */
        fun parse(text: String): MeetingMeta {
            val meta = lenientJson.decodeFromString(serializer(), text)
            return meta.copy(title = meta.title?.ifBlank { null })
        }
    }

    /** Serializes to the on-disk meeting.json format; null fields are omitted. */
    fun toJson(): String = lenientJson.encodeToString(serializer(), this)
}
