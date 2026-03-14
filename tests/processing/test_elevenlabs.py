from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from meeting_recorder.processing.providers.elevenlabs import ElevenLabsProvider


@pytest.fixture
def provider():
    return ElevenLabsProvider(api_key="test-key")


def test_provider_has_transcribe_method(provider):
    assert callable(getattr(provider, "transcribe", None))


def test_provider_has_no_summarize():
    """ElevenLabs is transcription-only."""
    provider = ElevenLabsProvider(api_key="test-key")
    assert not hasattr(provider, "summarize") or not callable(getattr(provider, "summarize", None))


def test_transcribe_calls_api(provider, tmp_path):
    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"fake audio")

    mock_client = MagicMock()

    mock_word = MagicMock()
    mock_word.text = "Hello"
    mock_word.start = 0.0
    mock_word.end = 0.5
    mock_word.speaker_id = "speaker_0"

    mock_result = MagicMock()
    mock_result.words = [mock_word]
    mock_result.text = "Hello"

    mock_client.speech_to_text.convert.return_value = mock_result
    provider._client = mock_client

    on_status = MagicMock()
    result = provider.transcribe(audio_file, on_status=on_status)

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Speaker 1" in result


def test_format_timestamp():
    assert ElevenLabsProvider._format_timestamp(0.0) == "00:00:00"
    assert ElevenLabsProvider._format_timestamp(65.5) == "00:01:05"
    assert ElevenLabsProvider._format_timestamp(3661.0) == "01:01:01"


def test_speaker_label():
    assert ElevenLabsProvider._speaker_label("speaker_0") == "Speaker 1"
    assert ElevenLabsProvider._speaker_label("speaker_2") == "Speaker 3"
    assert ElevenLabsProvider._speaker_label(None) == "Unknown"
