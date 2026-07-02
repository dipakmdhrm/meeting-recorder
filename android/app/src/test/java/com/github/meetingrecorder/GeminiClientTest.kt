package com.github.meetingrecorder

import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.GeminiClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever
import kotlin.coroutines.coroutineContext

class GeminiClientTest {

    @get:Rule
    val tempDir = TemporaryFolder()

    private val server = MockWebServer()
    private lateinit var config: Config
    private lateinit var baseUrl: String
    private lateinit var client: GeminiClient

    /** Delays requested by the client (backoff + poll waits) — recorded, never slept. */
    private val recordedDelays = mutableListOf<Long>()

    @Before
    fun setUp() {
        server.start()
        config = mock<Config>().also {
            whenever(it.apiKey).thenReturn("test-key")
            whenever(it.model).thenReturn("gemini-flash-latest")
            whenever(it.transcriptionPrompt).thenReturn("")
            whenever(it.summarizationPrompt).thenReturn("")
            whenever(it.titlePrompt).thenReturn("")
        }
        baseUrl = server.url("/").toString().trimEnd('/')
        client = GeminiClient(config, baseUrl, delayFn = { recordedDelays += it })
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    // -------------------------------------------------------------------------
    // transcribe — happy path
    // -------------------------------------------------------------------------

    @Test
    fun `transcribe succeeds through full upload-poll-generate flow`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/abc123"))
        server.enqueue(pollResponse("ACTIVE")) // flat JSON — regression guard
        server.enqueue(contentResponse("Hello transcript."))

        val result = client.transcribe(audioFile())

        assertEquals("Hello transcript.", result)
        assertEquals(4, server.requestCount)
    }

    @Test
    fun `transcribe polls multiple times before file becomes ACTIVE`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/xyz"))
        server.enqueue(pollResponse("PROCESSING"))
        server.enqueue(pollResponse("PROCESSING"))
        server.enqueue(pollResponse("ACTIVE"))
        server.enqueue(contentResponse("Polled transcript."))

        assertEquals("Polled transcript.", client.transcribe(audioFile()))
        assertEquals(6, server.requestCount)
    }

    // -------------------------------------------------------------------------
    // transcribe — regression: flat poll response (the bug we fixed)
    // -------------------------------------------------------------------------

    @Test
    fun `waitForFileActive parses flat JSON without file wrapper`() = runTest {
        // Before the fix, getJSONObject("file") on this response threw
        // JSONException: No value for file
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/flat"))
        server.enqueue(
            // no "file" wrapper
            MockResponse().setResponseCode(200)
                .setBody("""{"name":"files/flat","state":"ACTIVE"}"""),
        )
        server.enqueue(contentResponse("Regression passes."))

        assertEquals("Regression passes.", client.transcribe(audioFile()))
    }

    // -------------------------------------------------------------------------
    // transcribe — error handling
    // -------------------------------------------------------------------------

    @Test
    fun `transcribe throws on non-2xx upload init`() = runTest {
        server.enqueue(MockResponse().setResponseCode(403).setBody("Forbidden"))

        try {
            client.transcribe(audioFile())
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue("Expected 403, got: ${e.message}", e.message!!.contains("403"))
        }
    }

    @Test
    fun `transcribe throws with Gemini error body returned alongside HTTP 200`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"error":{"code":400,"message":"Invalid audio format"}}"""),
        )

        try {
            client.transcribe(audioFile())
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue("Expected error code, got: ${e.message}", e.message!!.contains("400"))
            assertTrue(
                "Expected error message, got: ${e.message}",
                e.message!!.contains("Invalid audio format"),
            )
        }
    }

    @Test
    fun `transcribe throws descriptively when upload response missing file key`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"unexpectedKey":"someValue"}"""),
        )

        try {
            client.transcribe(audioFile())
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue(
                "Expected 'missing file', got: ${e.message}",
                e.message!!.contains("missing 'file'"),
            )
        }
    }

    @Test
    fun `transcribe throws when file processing fails`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/fail"))
        server.enqueue(pollResponse("FAILED"))

        try {
            client.transcribe(audioFile())
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue(
                "Expected failure message, got: ${e.message}",
                e.message!!.lowercase().contains("fail"),
            )
        }
    }

    // -------------------------------------------------------------------------
    // Retry on transient failures
    // -------------------------------------------------------------------------

    @Test
    fun `transient 503 on upload init is retried and succeeds`() = runTest {
        server.enqueue(MockResponse().setResponseCode(503).setBody("Service Unavailable"))
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/retry"))
        server.enqueue(pollResponse("ACTIVE"))
        server.enqueue(contentResponse("Retried transcript."))

        assertEquals("Retried transcript.", client.transcribe(audioFile()))
        assertEquals(5, server.requestCount)
        assertEquals(listOf(2_000L), recordedDelays) // one backoff, recorded not slept
    }

    @Test
    fun `transient 429 on generateContent is retried and succeeds`() = runTest {
        server.enqueue(MockResponse().setResponseCode(429).setBody("Rate limited"))
        server.enqueue(contentResponse("Notes after retry."))

        assertEquals("Notes after retry.", client.summarize("transcript"))
        assertEquals(2, server.requestCount)
        assertEquals(listOf(2_000L), recordedDelays)
    }

    @Test
    fun `transient 500 during poll is retried and poll loop continues`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/pollretry"))
        server.enqueue(MockResponse().setResponseCode(500).setBody("Internal"))
        server.enqueue(pollResponse("PROCESSING"))
        server.enqueue(pollResponse("ACTIVE"))
        server.enqueue(contentResponse("Poll retried."))

        assertEquals("Poll retried.", client.transcribe(audioFile()))
        assertEquals(6, server.requestCount)
        // 2s backoff for the 500, then a 2s poll interval after PROCESSING
        assertEquals(listOf(2_000L, 2_000L), recordedDelays)
    }

    @Test
    fun `persistent 503 exhausts retries with exponential backoff and fails`() = runTest {
        repeat(3) { server.enqueue(MockResponse().setResponseCode(503).setBody("down")) }

        try {
            client.summarize("transcript")
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue("Expected 503, got: ${e.message}", e.message!!.contains("503"))
        }
        assertEquals(3, server.requestCount) // initial attempt + 2 retries
        assertEquals(listOf(2_000L, 4_000L), recordedDelays) // doubling backoff
    }

    @Test
    fun `permanent 401 fails immediately without retry`() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("Unauthorized"))

        try {
            client.transcribe(audioFile())
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue("Expected 401, got: ${e.message}", e.message!!.contains("401"))
        }
        assertEquals(1, server.requestCount)
        assertTrue("No backoff expected, got: $recordedDelays", recordedDelays.isEmpty())
    }

    @Test
    fun `retry loop stops promptly when coroutine is cancelled`() = runTest {
        repeat(3) { server.enqueue(MockResponse().setResponseCode(503).setBody("down")) }

        val job = launch {
            // delayFn cancels the coroutine mid-backoff; ensureActive() before the
            // next attempt must abort the retry loop instead of hitting the server again.
            val cancellingClient = GeminiClient(config, baseUrl, delayFn = { coroutineContext.cancel() })
            try {
                cancellingClient.summarize("transcript")
                fail("Expected CancellationException")
            } catch (_: CancellationException) {
                // expected
            }
        }
        job.join()

        assertEquals(1, server.requestCount) // no further attempts after cancellation
    }

    // -------------------------------------------------------------------------
    // API key handling
    // -------------------------------------------------------------------------

    @Test
    fun `api key is sent as header and never appears in URLs`() = runTest {
        server.enqueue(uploadInitResponse())
        server.enqueue(uploadBytesResponse("files/hdr"))
        server.enqueue(pollResponse("ACTIVE"))
        server.enqueue(contentResponse("Header auth works."))

        client.transcribe(audioFile())

        repeat(4) {
            val request = server.takeRequest()
            assertEquals("test-key", request.getHeader("x-goog-api-key"))
            val url = request.requestUrl.toString()
            assertFalse("API key leaked into URL: $url", url.contains("test-key"))
            assertFalse("URL still has key param: $url", url.contains("key="))
        }
    }

    // -------------------------------------------------------------------------
    // summarize / generateTitle
    // -------------------------------------------------------------------------

    @Test
    fun `summarize returns generated notes`() = runTest {
        server.enqueue(contentResponse("- Decision: launch Monday\n- Action: Alice to send invite"))

        val result = client.summarize("We discussed the launch and Alice will send the invite.")

        assertTrue(result.contains("Decision"))
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `generateTitle returns text from API`() = runTest {
        server.enqueue(contentResponse("Weekly Standup"))

        val result = client.generateTitle("Notes about the standup.")

        assertEquals("Weekly Standup", result)
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private fun audioFile() =
        tempDir.newFile("recording.m4a").also { it.writeBytes(ByteArray(1024)) }

    private fun uploadInitResponse() = MockResponse()
        .setResponseCode(200)
        .addHeader("X-Goog-Upload-URL", server.url("/upload").toString())

    private fun uploadBytesResponse(fileName: String) = MockResponse()
        .setResponseCode(200)
        .setBody("""{"file":{"name":"$fileName","state":"PROCESSING"}}""")

    private fun pollResponse(state: String) = MockResponse()
        .setResponseCode(200)
        .setBody("""{"name":"files/test","state":"$state"}""")

    private fun contentResponse(text: String): MockResponse {
        val escaped = text.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
        return MockResponse()
            .setResponseCode(200)
            .setBody("""{"candidates":[{"content":{"parts":[{"text":"$escaped"}]}}]}""")
    }
}
