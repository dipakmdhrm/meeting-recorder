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

    companion object {
        const val DEFAULT_MODEL = "gemini-flash-latest"

        val AVAILABLE_MODELS = listOf(
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        )
    }
}
