"""Tests for the pure error-presentation policy."""

import pytest

from meeting_recorder.core.errors import error_presentation


class TestErrorPresentation:
    @pytest.mark.parametrize(
        "msg",
        [
            "Gemini API key is not configured. Please open Settings.",
            "Audio device error: no default source",
            "ffmpeg not found. Please install ffmpeg.",
            "faster-whisper is not installed. Run: pip install faster-whisper",
            "Permission denied writing to /var/log",
        ],
    )
    def test_actionable_problems_get_a_dialog(self, msg):
        assert error_presentation(msg) == "dialog"

    @pytest.mark.parametrize(
        "msg",
        [
            "Gemini did not respond within 3 minutes (transcription).",
            "Cannot reach Ollama at http://localhost:11434. Make sure it is running.",
            "Failed to stop recording: timeout",
            "Ollama error: model crashed",
        ],
    )
    def test_runtime_failures_get_a_toast(self, msg):
        assert error_presentation(msg) == "toast"

    def test_classification_is_case_insensitive(self):
        assert error_presentation("GEMINI API KEY MISSING") == "dialog"
