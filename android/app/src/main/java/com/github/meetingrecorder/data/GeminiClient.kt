package com.github.meetingrecorder.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.coroutineContext

class GeminiClient(
    private val config: Config,
    private val baseUrl: String = "https://generativelanguage.googleapis.com",
    // Injectable so tests can observe backoff without real sleeps.
    private val delayFn: suspend (Long) -> Unit = { delay(it) },
) {
    companion object {
        private const val MAX_RETRIES = 2
        private const val INITIAL_BACKOFF_MS = 2_000L
        private const val POLL_INTERVAL_MS = 2_000L
        private const val POLL_TIMEOUT_MS = 120_000L

        private val client = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    /** Non-2xx HTTP response. [code] drives retry classification (5xx/429 = transient). */
    private class HttpStatusException(val code: Int, message: String) : RuntimeException(message)

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
    // Retry / transport helpers
    // -------------------------------------------------------------------------

    /**
     * Runs one network step, retrying transient failures (IOException, HTTP 5xx/429)
     * up to [MAX_RETRIES] times with exponential backoff (2s, 4s). Permanent errors
     * (other 4xx, parse errors) propagate immediately. Checks for coroutine
     * cancellation before every attempt so a cancelled job stops promptly.
     */
    private suspend fun <T> withRetry(block: () -> T): T {
        var backoffMs = INITIAL_BACKOFF_MS
        repeat(MAX_RETRIES) {
            coroutineContext.ensureActive()
            try {
                return block()
            } catch (e: Exception) {
                if (!isTransient(e)) throw e
                delayFn(backoffMs)
                backoffMs *= 2
            }
        }
        coroutineContext.ensureActive()
        return block()
    }

    private fun isTransient(e: Exception): Boolean = when (e) {
        is HttpStatusException -> e.code >= 500 || e.code == 429
        is IOException -> true
        else -> false
    }

    /** Executes [request] and returns the response body, throwing [HttpStatusException] on non-2xx. */
    private fun executeForBody(request: Request, step: String): String =
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw HttpStatusException(
                    response.code,
                    "$step failed (${response.code}): ${response.body?.string()}",
                )
            }
            response.body?.string() ?: throw RuntimeException("Empty $step response")
        }

    // -------------------------------------------------------------------------
    // Gemini flow
    // -------------------------------------------------------------------------

    private suspend fun uploadFile(file: File, mimeType: String): String {
        // Step 1: Initialise resumable upload
        val uploadUrl = withRetry { initiateUpload(file, mimeType) }

        // Step 2: Upload bytes
        val uploadRequest = Request.Builder()
            .url(uploadUrl)
            .post(file.asRequestBody(mimeType.toMediaType()))
            .header("x-goog-api-key", config.apiKey)
            .header("Content-Length", file.length().toString())
            .header("X-Goog-Upload-Offset", "0")
            .header("X-Goog-Upload-Command", "upload, finalize")
            .build()
        val responseJson = withRetry { executeForBody(uploadRequest, "Upload") }

        val json = JSONObject(responseJson)
        // Gemini occasionally returns error bodies with HTTP 200; surface them clearly.
        json.optJSONObject("error")?.let { err ->
            throw RuntimeException("Gemini API error ${err.optInt("code")}: ${err.optString("message")}")
        }
        val fileObj = json.optJSONObject("file")
            ?: throw RuntimeException(
                "Unexpected upload response (missing 'file'). " +
                    "Response was: ${responseJson.take(200)}",
            )
        return fileObj.getString("name")
    }

    private fun initiateUpload(file: File, mimeType: String): String {
        val initBody = JSONObject()
            .put("file", JSONObject().put("display_name", file.name))
            .toString()
            .toRequestBody("application/json".toMediaType())

        val initRequest = Request.Builder()
            .url("$baseUrl/upload/v1beta/files?uploadType=resumable")
            .post(initBody)
            .header("x-goog-api-key", config.apiKey)
            .header("X-Goog-Upload-Protocol", "resumable")
            .header("X-Goog-Upload-Command", "start")
            .header("X-Goog-Upload-Header-Content-Length", file.length().toString())
            .header("X-Goog-Upload-Header-Content-Type", mimeType)
            .build()

        return client.newCall(initRequest).execute().use { response ->
            if (!response.isSuccessful) {
                throw HttpStatusException(
                    response.code,
                    "Upload init failed (${response.code}): ${response.body?.string()}",
                )
            }
            response.header("X-Goog-Upload-URL")
                ?: throw RuntimeException("No X-Goog-Upload-URL in response")
        }
    }

    private suspend fun waitForFileActive(fileName: String, onStatus: (String) -> Unit) {
        val deadline = System.currentTimeMillis() + POLL_TIMEOUT_MS
        val pollRequest = Request.Builder()
            .url("$baseUrl/v1beta/$fileName")
            .get()
            .header("x-goog-api-key", config.apiKey)
            .build()

        while (System.currentTimeMillis() < deadline) {
            coroutineContext.ensureActive()
            val state = withRetry {
                val body = executeForBody(pollRequest, "File poll")
                // GET /v1beta/files/{id} returns the File object directly (not wrapped in "file")
                JSONObject(body).getString("state")
            }

            when (state) {
                "ACTIVE" -> return
                "FAILED" -> throw RuntimeException("Gemini file processing failed")
                else -> {
                    onStatus("Waiting for file processing…")
                    delayFn(POLL_INTERVAL_MS)
                }
            }
        }
        throw RuntimeException("File processing timed out after 120 seconds")
    }

    private suspend fun generateContent(
        prompt: String,
        fileUri: String? = null,
        mimeType: String? = null,
    ): String {
        val parts = JSONArray()

        if (fileUri != null && mimeType != null) {
            parts.put(
                JSONObject().put(
                    "fileData",
                    JSONObject().put("mimeType", mimeType).put("fileUri", fileUri),
                ),
            )
        }
        parts.put(JSONObject().put("text", prompt))

        val body = JSONObject()
            .put(
                "contents",
                JSONArray().put(JSONObject().put("parts", parts)),
            )
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$baseUrl/v1beta/models/${config.model}:generateContent")
            .post(body)
            .header("x-goog-api-key", config.apiKey)
            .build()

        val responseJson = withRetry { executeForBody(request, "generateContent") }

        return JSONObject(responseJson)
            .getJSONArray("candidates")
            .getJSONObject(0)
            .getJSONObject("content")
            .getJSONArray("parts")
            .getJSONObject(0)
            .getString("text")
    }
}
