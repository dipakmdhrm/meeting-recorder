package com.github.meetingrecorder

import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.GeminiClient
import com.github.meetingrecorder.data.MeetingProcessor
import com.github.meetingrecorder.data.MeetingRepository
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever
import java.io.File

class MeetingProcessorTest {

    @get:Rule
    val tempDir = TemporaryFolder()

    private val server = MockWebServer()
    private lateinit var config: Config
    private lateinit var repository: MeetingRepository
    private lateinit var processor: MeetingProcessor
    private lateinit var meetingDir: File

    /** Warnings the processor logged (instead of android.util.Log, which is not JVM-mockable). */
    private val loggedWarnings = mutableListOf<String>()

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
        val baseUrl = server.url("/").toString().trimEnd('/')
        val gemini = GeminiClient(config, baseUrl, delayFn = { })
        repository = MeetingRepository(tempDir.newFolder("Meetings"))
        processor = MeetingProcessor(
            gemini = gemini,
            store = repository,
            logWarn = { msg, _ -> loggedWarnings += msg },
        )
        meetingDir = repository.createMeetingDir("Standup")
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    // -------------------------------------------------------------------------
    // Happy path: transcribe → summarize → title → save
    // -------------------------------------------------------------------------

    @Test
    fun `happy path writes transcript, notes and meeting meta`() = runTest {
        enqueueTranscribeFlow(transcript = "Full transcript.", notes = "- Decision: ship it")
        server.enqueue(contentResponse("Weekly Sync"))

        val statuses = mutableListOf<String>()
        val result = processor.transcribeAndSummarize(audioFile()) { statuses += it }
        val title = processor.generateTitle(result.notes)
        processor.saveResults(meetingDir, result.transcript, result.notes, title, 90)

        assertEquals("Full transcript.", result.transcript)
        assertEquals("- Decision: ship it", result.notes)
        assertEquals("Weekly Sync", title)
        assertEquals(6, server.requestCount) // init + upload + poll + transcribe + summarize + title

        assertEquals("Full transcript.", File(meetingDir, "transcript.md").readText())
        assertEquals("- Decision: ship it", File(meetingDir, "notes.md").readText())
        val meta = JSONObject(File(meetingDir, "meeting.json").readText())
        assertEquals("Weekly Sync", meta.getString("title"))
        assertEquals(90, meta.getInt("duration_seconds"))

        assertTrue("Expected upload status, got: $statuses", statuses.contains("Uploading audio…"))
        assertTrue("Expected notes status, got: $statuses", statuses.contains("Generating meeting notes…"))
    }

    @Test
    fun `generateTitle trims surrounding whitespace`() = runTest {
        server.enqueue(contentResponse("  Weekly Sync \n"))

        assertEquals("Weekly Sync", processor.generateTitle("some notes"))
    }

    // -------------------------------------------------------------------------
    // Title generation is best-effort
    // -------------------------------------------------------------------------

    @Test
    fun `title generation failure returns null, logs, and does not fail the flow`() = runTest {
        server.enqueue(MockResponse().setResponseCode(400).setBody("Bad request"))

        val title = processor.generateTitle("- Notes")
        processor.saveResults(meetingDir, "Transcript.", "- Notes", title, 60)

        assertNull(title)
        assertEquals(1, loggedWarnings.size)
        assertEquals("Transcript.", File(meetingDir, "transcript.md").readText())
        assertEquals("- Notes", File(meetingDir, "notes.md").readText())
        val meta = JSONObject(File(meetingDir, "meeting.json").readText())
        assertFalse("Untitled meeting must not carry a title key", meta.has("title"))
        assertEquals(60, meta.getInt("duration_seconds"))
    }

    // -------------------------------------------------------------------------
    // Notes-only path (detail screen's generate/regenerate notes)
    // -------------------------------------------------------------------------

    @Test
    fun `notes-only path reuses transcript without uploading audio`() = runTest {
        File(meetingDir, "transcript.md").writeText("Existing transcript.")
        server.enqueue(contentResponse("- Regenerated notes"))

        val notes = processor.summarizeTranscript("Existing transcript.")
        processor.saveNotes(meetingDir, notes, "Standup", 45)

        assertEquals("- Regenerated notes", notes)
        assertEquals(1, server.requestCount) // a single generateContent call — no upload, no poll
        val request = server.takeRequest()
        assertTrue(
            "Expected generateContent, got: ${request.path}",
            request.path!!.contains(":generateContent"),
        )

        assertEquals("- Regenerated notes", File(meetingDir, "notes.md").readText())
        assertEquals("Existing transcript.", File(meetingDir, "transcript.md").readText()) // untouched
        val meta = JSONObject(File(meetingDir, "meeting.json").readText())
        assertEquals("Standup", meta.getString("title"))
        assertEquals(45, meta.getInt("duration_seconds"))
    }

    // -------------------------------------------------------------------------
    // Errors from transcription/summarization propagate (unlike title failures)
    // -------------------------------------------------------------------------

    @Test
    fun `transcription failure propagates and nothing is written`() = runTest {
        server.enqueue(MockResponse().setResponseCode(403).setBody("Forbidden"))

        try {
            processor.transcribeAndSummarize(audioFile())
            fail("Expected RuntimeException")
        } catch (e: RuntimeException) {
            assertTrue("Expected 403, got: ${e.message}", e.message!!.contains("403"))
        }
        assertFalse(File(meetingDir, "transcript.md").exists())
        assertFalse(File(meetingDir, "notes.md").exists())
        assertFalse(File(meetingDir, "meeting.json").exists())
    }

    // -------------------------------------------------------------------------
    // Helpers (mirroring GeminiClientTest)
    // -------------------------------------------------------------------------

    private fun audioFile() =
        tempDir.newFile("recording.m4a").also { it.writeBytes(ByteArray(1024)) }

    /** Enqueues the full transcribe (init/upload/poll) + summarize response sequence. */
    private fun enqueueTranscribeFlow(transcript: String, notes: String) {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .addHeader("X-Goog-Upload-URL", server.url("/upload").toString()),
        )
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"file":{"name":"files/abc123","state":"PROCESSING"}}"""),
        )
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"name":"files/abc123","state":"ACTIVE"}"""),
        )
        server.enqueue(contentResponse(transcript))
        server.enqueue(contentResponse(notes))
    }

    private fun contentResponse(text: String): MockResponse {
        val escaped = text.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
        return MockResponse()
            .setResponseCode(200)
            .setBody("""{"candidates":[{"content":{"parts":[{"text":"$escaped"}]}}]}""")
    }
}
