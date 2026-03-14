import sys
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import pytest

# Create a mock litellm module so @patch can resolve it
_mock_litellm = MagicMock()
sys.modules.setdefault("litellm", _mock_litellm)


class TestLiteLLMTranscription:
    def test_transcribe_calls_litellm(self):
        from meeting_recorder.processing.providers.litellm_provider import (
            LiteLLMTranscriptionProvider,
        )
        mock_response = MagicMock()
        mock_response.text = "Hello world"

        with patch("litellm.transcription", return_value=mock_response) as mock_ts, \
             patch("builtins.open", mock_open(read_data=b"fake audio")):
            provider = LiteLLMTranscriptionProvider(model="groq/whisper-large-v3")
            result = provider.transcribe(audio_path=Path("/tmp/test.mp3"))

            mock_ts.assert_called_once()
            assert result == "Hello world"


class TestLiteLLMSummarization:
    def test_summarize_calls_litellm(self):
        from meeting_recorder.processing.providers.litellm_provider import (
            LiteLLMSummarizationProvider,
        )
        mock_choice = MagicMock()
        mock_choice.message.content = "# Meeting Notes\n- Discussed X"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_response) as mock_comp:
            provider = LiteLLMSummarizationProvider(
                model="gemini/gemini-2.5-flash",
                summarization_prompt="Summarize:\n{transcript}",
                timeout_minutes=5,
            )
            result = provider.summarize("Alice said hello. Bob said goodbye.")

            mock_comp.assert_called_once()
            call_args = mock_comp.call_args
            assert "Alice said hello" in call_args.kwargs["messages"][0]["content"]
            assert "Meeting Notes" in result

    def test_summarize_raises_on_empty(self):
        from meeting_recorder.processing.providers.litellm_provider import (
            LiteLLMSummarizationProvider,
        )
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("litellm.completion", return_value=mock_response):
            provider = LiteLLMSummarizationProvider(
                model="gemini/gemini-2.5-flash",
                summarization_prompt="Summarize:\n{transcript}",
                timeout_minutes=5,
            )
            with pytest.raises(RuntimeError):
                provider.summarize("test transcript")
