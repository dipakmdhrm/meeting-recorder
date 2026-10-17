"""Tests for Pipeline cancellation, input validation, and the best-effort
speaker-name resolution pass (no network)."""

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


class _FakeSummarizer:
    """Stands in for whatever summarization provider the config selects."""

    def __init__(self, reply="", error=None):
        self._reply = reply
        self._error = error
        self.prompts: list[str] = []

    def summarize(self, transcript, on_status=None):
        self.prompts.append(transcript)
        if self._error is not None:
            raise self._error
        return self._reply


TRANSCRIPT = "[00:00:05] **Person 1:** Hi, I'm Alice.\n[00:00:09] **Person 2:** Bob here."


def _resolver(config, reply="", error=None, monkeypatch=None):
    """Build a Pipeline with the name-resolution provider stubbed out."""
    fake = _FakeSummarizer(reply=reply, error=error)
    monkeypatch.setattr(
        "meeting_recorder.processing.summarization.create_summarization_provider",
        lambda cfg: fake,
    )
    pipeline = Pipeline(
        config=config,
        audio_path=Path("rec.mp3"),
        transcript_path=None,
        notes_path=None,
    )
    return pipeline, fake


class TestSpeakerNameResolution:
    def _config(self, **overrides):
        return {
            "transcription_service": "gemini_transcribe",
            "summarization_service": "gemini",
            "gemini_transcribe_resolve_names": True,
            **overrides,
        }

    def test_generic_labels_are_replaced_with_resolved_names(self, monkeypatch):
        pipeline, fake = _resolver(
            self._config(),
            reply='{"Person 1": "Alice", "Person 2": "Bob"}',
            monkeypatch=monkeypatch,
        )
        result = pipeline._maybe_resolve_speaker_names(TRANSCRIPT)
        assert "**Alice:**" in result and "**Bob:**" in result
        assert "**Person " not in result
        # The transcript is what gets sent, not the audio.
        assert fake.prompts == [TRANSCRIPT]

    def test_partial_mapping_leaves_the_rest_generic(self, monkeypatch):
        pipeline, _ = _resolver(
            self._config(), reply='{"Person 1": "Alice"}', monkeypatch=monkeypatch
        )
        result = pipeline._maybe_resolve_speaker_names(TRANSCRIPT)
        assert "**Alice:**" in result
        assert "**Person 2:**" in result

    def test_unparseable_reply_leaves_the_transcript_alone(self, monkeypatch):
        pipeline, _ = _resolver(self._config(), reply="I could not tell.", monkeypatch=monkeypatch)
        assert pipeline._maybe_resolve_speaker_names(TRANSCRIPT) == TRANSCRIPT

    def test_provider_failure_is_not_fatal(self, monkeypatch):
        pipeline, _ = _resolver(
            self._config(), error=RuntimeError("API down"), monkeypatch=monkeypatch
        )
        assert pipeline._maybe_resolve_speaker_names(TRANSCRIPT) == TRANSCRIPT

    def test_disabled_by_setting_makes_no_call(self, monkeypatch):
        pipeline, fake = _resolver(
            self._config(gemini_transcribe_resolve_names=False),
            reply='{"Person 1": "Alice"}',
            monkeypatch=monkeypatch,
        )
        assert pipeline._maybe_resolve_speaker_names(TRANSCRIPT) == TRANSCRIPT
        assert fake.prompts == []

    def test_other_transcription_services_are_untouched(self, monkeypatch):
        # The prompt-based Gemini path and the local engines name or label
        # speakers themselves; this pass is only for the diarized model.
        for service in ("gemini", "whisper", "whisper_cpp"):
            pipeline, fake = _resolver(
                self._config(transcription_service=service),
                reply='{"Person 1": "Alice"}',
                monkeypatch=monkeypatch,
            )
            assert pipeline._maybe_resolve_speaker_names(TRANSCRIPT) == TRANSCRIPT
            assert fake.prompts == []

    def test_transcript_without_generic_labels_makes_no_call(self, monkeypatch):
        pipeline, fake = _resolver(
            self._config(), reply='{"Person 1": "Alice"}', monkeypatch=monkeypatch
        )
        named = "[00:00:05] **Alice:** Hi."
        assert pipeline._maybe_resolve_speaker_names(named) == named
        assert fake.prompts == []

    def test_the_name_prompt_is_used_not_the_notes_prompt(self, monkeypatch):
        captured: dict = {}

        def capture(cfg):
            captured.update(cfg)
            return _FakeSummarizer(reply="{}")

        monkeypatch.setattr(
            "meeting_recorder.processing.summarization.create_summarization_provider",
            capture,
        )
        pipeline = Pipeline(
            config=self._config(summarization_prompt="MY NOTES PROMPT"),
            audio_path=Path("rec.mp3"),
            transcript_path=None,
            notes_path=None,
        )
        pipeline._maybe_resolve_speaker_names(TRANSCRIPT)
        assert "JSON object" in captured["summarization_prompt"]
        assert captured["summarization_prompt"] != "MY NOTES PROMPT"
