package com.github.meetingrecorder

import com.github.meetingrecorder.util.extensionToMimeType
import com.github.meetingrecorder.util.normalizeMimeType
import org.junit.Assert.assertEquals
import org.junit.Test

class MimeTest {

    @Test
    fun `known extensions map to their audio MIME types`() {
        assertEquals("audio/mpeg", extensionToMimeType("mp3"))
        assertEquals("audio/wav", extensionToMimeType("wav"))
        assertEquals("audio/ogg", extensionToMimeType("ogg"))
        assertEquals("audio/flac", extensionToMimeType("flac"))
        assertEquals("audio/webm", extensionToMimeType("webm"))
        assertEquals("audio/mp4", extensionToMimeType("m4a"))
    }

    @Test
    fun `extension matching is case-insensitive`() {
        assertEquals("audio/mpeg", extensionToMimeType("MP3"))
        assertEquals("audio/wav", extensionToMimeType("Wav"))
    }

    @Test
    fun `unknown or empty extensions default to mp4`() {
        assertEquals("audio/mp4", extensionToMimeType("aac"))
        assertEquals("audio/mp4", extensionToMimeType(""))
    }

    @Test
    fun `mime aliases are normalized to canonical types`() {
        assertEquals("audio/mp4", normalizeMimeType("audio/m4a"))
        assertEquals("audio/mp4", normalizeMimeType("audio/x-m4a"))
        assertEquals("audio/wav", normalizeMimeType("audio/x-wav"))
        assertEquals("audio/mpeg", normalizeMimeType("audio/mp3"))
    }

    @Test
    fun `canonical and unrelated mime types pass through unchanged`() {
        assertEquals("audio/mp4", normalizeMimeType("audio/mp4"))
        assertEquals("audio/ogg", normalizeMimeType("audio/ogg"))
        assertEquals("application/octet-stream", normalizeMimeType("application/octet-stream"))
    }

    @Test
    fun `mime normalization is case-insensitive per RFC 2045`() {
        assertEquals("audio/mp4", normalizeMimeType("AUDIO/M4A"))
        assertEquals("audio/wav", normalizeMimeType("Audio/X-Wav"))
        assertEquals("audio/mpeg", normalizeMimeType("AUDIO/MP3"))
        assertEquals("audio/ogg", normalizeMimeType("Audio/OGG"))
    }
}
