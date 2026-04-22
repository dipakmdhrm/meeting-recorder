package com.github.meetingrecorder.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

class GeminiClient(
    private val config: Config,
    private val baseUrl: String = "https://generativelanguage.googleapis.com",
) {
    companion object {
        private val client = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    suspend fun transcribe(
        audioFile: File,
        mimeType: String = "audio/mp4",
        onStatus: (String) -> Unit = {},
    ): String = withContext(Dispatchers.IO) {
        onStatus("Uploading audio…")
        val uploadedFileName = uploadFile(audioFile, mimeType)

        onStatus("Processing audio file…")
        waitForFileActive(uploadedFileName, onStatus)

        onStatus("Transcribing…")
        generateContent(
            prompt = config.transcriptionPrompt.ifBlank { Config.DEFAULT_TRANSCRIPTION_PROMPT },
            fileUri = "$baseUrl/v1beta/$uploadedFileName",
            mimeType = mimeType,
        )
    }

    suspend fun summarize(
        transcript: String,
        onStatus: (String) -> Unit = {},
    ): String = withContext(Dispatchers.IO) {
        onStatus("Generating meeting notes…")
        val summarizeTemplate = config.summarizationPrompt.ifBlank { Config.DEFAULT_SUMMARIZATION_PROMPT }
        generateContent(
            prompt = if (summarizeTemplate.contains("{transcript}")) {
                summarizeTemplate.replace("{transcript}", transcript)
            } else {
                "$summarizeTemplate\n\n$transcript"
            },
        )
    }

    suspend fun generateTitle(
        notes: String,
        onStatus: (String) -> Unit = {},
    ): String = withContext(Dispatchers.IO) {
        onStatus("Generating title…")
        val titleTemplate = config.titlePrompt.ifBlank { Config.DEFAULT_TITLE_PROMPT }
        generateContent(
            prompt = if (titleTemplate.contains("{notes}")) {
                titleTemplate.replace("{notes}", notes)
            } else {
                "$titleTemplate\n\n$notes"
            },
        )
    }

    // -------------------------------------------------------------------------
    // Private helpers
    // -------------------------------------------------------------------------

    private fun uploadFile(file: File, mimeType: String): String {
        val apiKey = config.apiKey
        val fileSize = file.length()

        // Step 1: Initialise resumable upload
        val initBody = JSONObject()
            .put("file", JSONObject().put("display_name", file.name))
            .toString()
            .toRequestBody("application/json".toMediaType())

        val initRequest = Request.Builder()
            .url("$baseUrl/upload/v1beta/files?uploadType=resumable&key=$apiKey")
            .post(initBody)
            .header("X-Goog-Upload-Protocol", "resumable")
            .header("X-Goog-Upload-Command", "start")
            .header("X-Goog-Upload-Header-Content-Length", fileSize.toString())
            .header("X-Goog-Upload-Header-Content-Type", mimeType)
            .build()

        val uploadUrl = client.newCall(initRequest).execute().use { response ->
            if (!response.isSuccessful) {
                throw RuntimeException("Upload init failed (${response.code}): ${response.body?.string()}")
            }
            response.header("X-Goog-Upload-URL")
                ?: throw RuntimeException("No X-Goog-Upload-URL in response")
        }

        // Step 2: Upload bytes
        val uploadRequest = Request.Builder()
            .url(uploadUrl)
            .post(file.asRequestBody(mimeType.toMediaType()))
            .header("Content-Length", fileSize.toString())
            .header("X-Goog-Upload-Offset", "0")
            .header("X-Goog-Upload-Command", "upload, finalize")
            .build()

        val responseJson = client.newCall(uploadRequest).execute().use { response ->
            if (!response.isSuccessful) {
                throw RuntimeException("Upload failed (${response.code}): ${response.body?.string()}")
            }
            response.body?.string() ?: throw RuntimeException("Empty upload response")
        }

        val json = JSONObject(responseJson)
        // Gemini occasionally returns error bodies with HTTP 200; surface them clearly.
        json.optJSONObject("error")?.let { err ->
            throw RuntimeException("Gemini API error ${err.optInt("code")}: ${err.optString("message")}")
        }
        val fileObj = json.optJSONObject("file")
            ?: throw RuntimeException(
                "Unexpected upload response (missing 'file'). " +
                "Response was: ${responseJson.take(200)}"
            )
        return fileObj.getString("name")
    }

    private suspend fun waitForFileActive(fileName: String, onStatus: (String) -> Unit) {
        val apiKey = config.apiKey
        val deadline = System.currentTimeMillis() + 120_000L

        while (System.currentTimeMillis() < deadline) {
            val request = Request.Builder()
                .url("$baseUrl/v1beta/$fileName?key=$apiKey")
                .get()
                .build()

            val state = withContext(Dispatchers.IO) {
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        throw RuntimeException("File poll failed (${response.code})")
                    }
                    val body = response.body?.string() ?: ""
                    // GET /v1beta/files/{id} returns the File object directly (not wrapped in "file")
                    JSONObject(body).getString("state")
                }
            }

            when (state) {
                "ACTIVE" -> return
                "FAILED" -> throw RuntimeException("Gemini file processing failed")
                else -> {
                    onStatus("Waiting for file processing…")
                    delay(2_000)
                }
            }
        }
        throw RuntimeException("File processing timed out after 120 seconds")
    }

    private fun generateContent(
        prompt: String,
        fileUri: String? = null,
        mimeType: String? = null,
    ): String {
        val apiKey = config.apiKey
        val parts = JSONArray()

        if (fileUri != null && mimeType != null) {
            parts.put(
                JSONObject().put(
                    "fileData",
                    JSONObject().put("mimeType", mimeType).put("fileUri", fileUri)
                )
            )
        }
        parts.put(JSONObject().put("text", prompt))

        val body = JSONObject()
            .put(
                "contents",
                JSONArray().put(JSONObject().put("parts", parts))
            )
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$baseUrl/v1beta/models/${config.model}:generateContent?key=$apiKey")
            .post(body)
            .build()

        val responseJson = client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw RuntimeException("generateContent failed (${response.code}): ${response.body?.string()}")
            }
            response.body?.string() ?: throw RuntimeException("Empty generateContent response")
        }

        return JSONObject(responseJson)
            .getJSONArray("candidates")
            .getJSONObject(0)
            .getJSONObject("content")
            .getJSONArray("parts")
            .getJSONObject(0)
            .getString("text")
    }
}
