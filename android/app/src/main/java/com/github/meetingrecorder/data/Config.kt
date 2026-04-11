package com.github.meetingrecorder.data

import android.content.Context
import android.content.SharedPreferences

class Config(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("meeting_recorder", Context.MODE_PRIVATE)

    var apiKey: String
        get() = prefs.getString("api_key", "") ?: ""
        set(value) { prefs.edit().putString("api_key", value).apply() }

    var model: String
        get() = prefs.getString("model", DEFAULT_MODEL) ?: DEFAULT_MODEL
        set(value) { prefs.edit().putString("model", value).apply() }

    var audioQuality: AudioQuality
        get() = AudioQuality.entries.find {
            it.name == prefs.getString("audio_quality", AudioQuality.LOW.name)
        } ?: AudioQuality.LOW
        set(value) { prefs.edit().putString("audio_quality", value.name).apply() }

    // Empty string means "use the built-in default prompt"
    var transcriptionPrompt: String
        get() = prefs.getString("transcription_prompt", "") ?: ""
        set(value) { prefs.edit().putString("transcription_prompt", value).apply() }

    var summarizationPrompt: String
        get() = prefs.getString("summarization_prompt", "") ?: ""
        set(value) { prefs.edit().putString("summarization_prompt", value).apply() }

    var titlePrompt: String
        get() = prefs.getString("title_prompt", "") ?: ""
        set(value) { prefs.edit().putString("title_prompt", value).apply() }

    companion object {
        const val DEFAULT_MODEL = "gemini-flash-latest"

        val AVAILABLE_MODELS = listOf(
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        )

        val DEFAULT_TRANSCRIPTION_PROMPT = """
            Transcribe this audio recording exactly as spoken.

            Label each speaker turn with a timestamp and speaker label on a new line, for example:

            [00:00:05] **Alice:** Hello, can everyone hear me?
            [00:00:09] **Bob:** Yes, loud and clear.

            Rules:
            - Try to infer each speaker's name from the conversation (e.g. if someone is addressed by name or introduces themselves). Use that name as their label.
            - If a name cannot be determined, label speakers as **Person 1:**, **Person 2:**, etc., assigned in the order they first speak. Use the same label consistently for the same speaker.
            - Start each new speaker turn on a new line.
            - Timestamps should be in [HH:MM:SS] format, incremented roughly every turn.
            - Transcribe faithfully in whatever language is spoken; do not translate.
        """.trimIndent()

        val DEFAULT_SUMMARIZATION_PROMPT = """
            You are a meeting assistant. Given the following meeting transcript, produce concise, well-structured meeting notes in Markdown format.

            The transcript may include speaker labels (e.g. **Speaker 1:**, **John:**). Where speaker labels are present, reference speakers by name or label when attributing decisions and key points.

            Structure the notes as follows:
            1. A brief summary of the meeting (2-4 sentences).
            2. Key discussion points and decisions, attributed to speakers where identifiable.
            3. If and only if there are clear action items mentioned in the meeting, add an ## Action Items section at the very end. List each item as a checkbox with the owner if known (e.g. `- [ ] John to send the report by Friday`). If there are no action items, omit this section entirely — do not write "None".

            TRANSCRIPT:
            {transcript}
        """.trimIndent()

        val DEFAULT_TITLE_PROMPT = """
            Generate a concise 3-6 word title for this meeting based on the content below. Return only the title text, nothing else.

            {notes}
        """.trimIndent()
    }
}

enum class AudioQuality(val label: String, val bitrate: Int) {
    LOW("Low (64 kbps)", 64_000),
    MEDIUM("Medium (96 kbps)", 96_000),
    HIGH("High (128 kbps)", 128_000),
    VERY_HIGH("Very High (192 kbps)", 192_000),
}
