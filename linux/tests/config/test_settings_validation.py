"""Tests for the pure API-key helpers and for config forward compatibility."""

import json

import pytest

from meeting_recorder.config import settings
from meeting_recorder.config.defaults import DEFAULT_CONFIG
from meeting_recorder.config.settings import (
    api_key_error,
    gemini_key_warning,
    uses_gemini,
)

VALID_KEY = "AIza" + "x" * 35


class TestGeminiKeyWarning:
    def test_no_warning_for_plausible_key(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": VALID_KEY}
        assert gemini_key_warning(cfg) is None

    def test_warns_when_gemini_selected_but_key_empty(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": ""}
        assert "no API key" in (gemini_key_warning(cfg) or "")

    def test_warns_on_malformed_key(self):
        cfg = {
            "transcription_service": "gemini",
            "gemini_api_key": "sk-somethingelse123456789012345678",
        }
        warning = gemini_key_warning(cfg)
        assert warning is not None and "AIza" in warning

    def test_warns_on_too_short_key(self):
        cfg = {"summarization_service": "gemini", "gemini_api_key": "AIzaShort"}
        assert gemini_key_warning(cfg) is not None

    def test_no_warning_when_gemini_not_used(self):
        cfg = {
            "transcription_service": "whisper",
            "summarization_service": "ollama",
            "gemini_api_key": "",
        }
        assert gemini_key_warning(cfg) is None

    def test_defaults_treat_missing_services_as_gemini(self):
        # An empty config means gemini/gemini (the shipped defaults).
        assert gemini_key_warning({}) is not None

    def test_key_is_stripped_before_checking(self):
        cfg = {"transcription_service": "gemini", "gemini_api_key": f"  {VALID_KEY}  "}
        assert gemini_key_warning(cfg) is None


class TestUsesGemini:
    @pytest.mark.parametrize(
        "ts,ss,expected",
        [
            ("gemini", "gemini", True),
            # The dedicated speech-to-text service needs the same API key, so a
            # membership test is required here - comparing to "gemini" would
            # let it through unchecked and fail deep inside the provider.
            ("gemini_transcribe", "ollama", True),
            ("gemini_transcribe", "gemini", True),
            ("whisper", "gemini", True),
            ("whisper", "ollama", False),
            ("whisper_cpp", "ollama", False),
        ],
    )
    def test_detects_every_gemini_path(self, ts, ss, expected):
        cfg = {"transcription_service": ts, "summarization_service": ss}
        assert uses_gemini(cfg) is expected

    def test_missing_services_default_to_gemini(self):
        assert uses_gemini({}) is True


class TestApiKeyError:
    def test_speech_to_text_without_a_key_is_caught_pre_flight(self):
        cfg = {
            "transcription_service": "gemini_transcribe",
            "summarization_service": "ollama",
            "gemini_api_key": "",
        }
        assert "API key is not configured" in (api_key_error(cfg) or "")

    def test_no_error_with_a_key(self):
        cfg = {"transcription_service": "gemini_transcribe", "gemini_api_key": VALID_KEY}
        assert api_key_error(cfg) is None

    def test_no_error_for_a_fully_local_setup(self):
        cfg = {
            "transcription_service": "whisper",
            "summarization_service": "ollama",
            "gemini_api_key": "",
        }
        assert api_key_error(cfg) is None


class TestSpeechToTextKeyWarning:
    def test_malformed_key_is_warned_about(self):
        cfg = {
            "transcription_service": "gemini_transcribe",
            "summarization_service": "ollama",
            "gemini_api_key": "not-a-google-key-but-long-enough-to-pass-length",
        }
        assert "AIza" in (gemini_key_warning(cfg) or "")

    def test_valid_key_passes(self):
        cfg = {
            "transcription_service": "gemini_transcribe",
            "summarization_service": "ollama",
            "gemini_api_key": VALID_KEY,
        }
        assert gemini_key_warning(cfg) is None


class TestConfigBackwardCompatibility:
    """An existing install must keep working after upgrading."""

    @pytest.fixture
    def config_in_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(settings, "_config_dir", lambda: tmp_path)
        return tmp_path

    def test_config_written_before_this_release_gains_the_new_defaults(self, config_in_tmp):
        # A config.json from an older version: no speech-to-text keys at all.
        (config_in_tmp / "config.json").write_text(
            json.dumps(
                {
                    "transcription_service": "gemini",
                    "gemini_transcription_model": "gemini-pro-latest",
                    "output_folder": "/home/someone/meetings",
                }
            )
        )
        loaded = settings.load(keyring=None)
        assert loaded["gemini_transcribe_model"] == DEFAULT_CONFIG["gemini_transcribe_model"]
        assert loaded["gemini_transcribe_resolve_names"] is True
        # Existing values survive untouched.
        assert loaded["gemini_transcription_model"] == "gemini-pro-latest"
        assert loaded["output_folder"] == "/home/someone/meetings"
        assert loaded["transcription_service"] == "gemini"

    def test_stored_speech_to_text_values_round_trip(self, config_in_tmp):
        (config_in_tmp / "config.json").write_text(
            json.dumps(
                {
                    "transcription_service": "gemini_transcribe",
                    "gemini_transcribe_model": "gemini-3.5-transcribe",
                    "gemini_transcribe_resolve_names": False,
                }
            )
        )
        loaded = settings.load(keyring=None)
        assert loaded["transcription_service"] == "gemini_transcribe"
        assert loaded["gemini_transcribe_resolve_names"] is False
