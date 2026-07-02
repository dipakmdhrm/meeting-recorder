"""Tests for Pipeline cancellation and input validation (no network)."""

from pathlib import Path

import pytest

from meeting_recorder.core.task_runner import CancelToken
from meeting_recorder.processing.pipeline import Pipeline, PipelineCancelled


def _pipeline(audio: Path | None) -> Pipeline:
    return Pipeline(
        config={"transcription_service": "gemini", "summarization_service": "gemini"},
        audio_path=audio,
        transcript_path=None,
        notes_path=None,
    )


class TestPipelineGuards:
    def test_missing_audio_path_fails_fast(self):
        with pytest.raises(ValueError, match="audio path"):
            _pipeline(None).run()

    def test_cancelled_token_stops_before_any_provider_work(self, tmp_path):
        token = CancelToken()
        token.cancel()
        # Raises PipelineCancelled before any provider is created, so no
        # network/API key is needed for this test.
        with pytest.raises(PipelineCancelled):
            _pipeline(tmp_path / "recording.mp3").run(cancel_token=token)

    def test_uncancelled_token_proceeds_to_provider_creation(self, tmp_path):
        # With gemini selected and no API key, provider creation/transcription
        # is the next step after the cancel check — reaching an error that is
        # NOT PipelineCancelled proves the token check passed.
        token = CancelToken()
        with pytest.raises(Exception) as excinfo:
            _pipeline(tmp_path / "recording.mp3").run(cancel_token=token)
        assert not isinstance(excinfo.value, PipelineCancelled)
