package com.github.meetingrecorder

import com.github.meetingrecorder.data.Config
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigTest {

    @Test
    fun `AVAILABLE_MODELS is not empty`() {
        assertTrue(Config.AVAILABLE_MODELS.isNotEmpty())
    }

    @Test
    fun `default model is gemini-flash-latest`() {
        assertEquals("gemini-flash-latest", Config.DEFAULT_MODEL)
    }

    @Test
    fun `AVAILABLE_MODELS contains default model`() {
        assertTrue(Config.AVAILABLE_MODELS.contains(Config.DEFAULT_MODEL))
    }

    @Test
    fun `AVAILABLE_MODELS contains at least four entries`() {
        assertTrue(Config.AVAILABLE_MODELS.size >= 4)
    }

    @Test
    fun `AVAILABLE_MODELS has no duplicates`() {
        val unique = Config.AVAILABLE_MODELS.toSet()
        assertFalse("Duplicate model entries found", unique.size < Config.AVAILABLE_MODELS.size)
    }
}
