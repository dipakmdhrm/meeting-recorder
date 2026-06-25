"""
Tests for the whisper.cpp transcription provider: the pure output parser and
the provider's transcribe() flow (with an injected runner so no binary runs),
plus the transcription factory wiring for the "whisper_cpp" service.
"""
import json
from pathlib import Path

from meeting_recorder.processing.providers.whisper_cpp import (
    WhisperCppProvider,
    parse_whisper_cpp_output,
)
from meeting_recorder.processing.transcription import create_transcription_provider


def _wcpp_json(segments):
    return json.dumps({"transcription": segments})


# ── parse_whisper_cpp_output ──────────────────────────────────────────────────

class TestParseWhisperCppOutput:
    def test_formats_timestamp_and_text(self):
        raw = _wcpp_json([
            {"offsets": {"from": 0, "to": 2000}, "text": " Hello there"},
            {"offsets": {"from": 65000, "to": 67000}, "text": " Second line"},
        ])
        out = parse_whisper_cpp_output(raw)
        assert out == "[00:00:00] Hello there\n[00:01:05] Second line"

    def test_converts_milliseconds_to_hms(self):
        raw = _wcpp_json([{"offsets": {"from": 3_661_000, "to": 3_662_000}, "text": "x"}])
        # 3,661,000 ms == 1h 01m 01s
        assert parse_whisper_cpp_output(raw).startswith("[01:01:01]")

    def test_skips_empty_text_segments(self):
        raw = _wcpp_json([
            {"offsets": {"from": 0, "to": 1000}, "text": "   "},
            {"offsets": {"from": 1000, "to": 2000}, "text": "kept"},
        ])
        assert parse_whisper_cpp_output(raw) == "[00:00:01] kept"

    def test_missing_offsets_default_to_zero(self):
        raw = _wcpp_json([{"text": "no offsets"}])
        assert parse_whisper_cpp_output(raw) == "[00:00:00] no offsets"

    def test_non_json_falls_back_to_trimmed_text(self):
        assert parse_whisper_cpp_output("  plain text  ") == "plain text"

    def test_empty_transcription_yields_empty_string(self):
        assert parse_whisper_cpp_output(_wcpp_json([])) == ""


# ── WhisperCppProvider.transcribe ─────────────────────────────────────────────

class TestWhisperCppProviderTranscribe:
    def test_runs_binary_and_parses_output(self):
        raw = _wcpp_json([{"offsets": {"from": 0, "to": 1000}, "text": "hi"}])
        captured: list[list[str]] = []

        def fake_runner(cmd):
            captured.append(cmd)
            return raw

        provider = WhisperCppProvider(
            model="small",
            binary_path=Path("/opt/whisper-cli"),
            model_path=Path("/models/ggml-small.bin"),
            runner=fake_runner,
        )
        result = provider.transcribe(Path("/tmp/audio.m4a"))
        assert result == "[00:00:00] hi"
        # command references the binary, model, and audio file
        cmd = captured[0]
        assert "/opt/whisper-cli" in cmd
        assert "/models/ggml-small.bin" in cmd
        assert "/tmp/audio.m4a" in cmd

    def test_reports_status(self):
        statuses: list[str] = []
        provider = WhisperCppProvider(
            model="small",
            binary_path=Path("/opt/whisper-cli"),
            model_path=Path("/models/ggml-small.bin"),
            runner=lambda _cmd: _wcpp_json([]),
        )
        provider.transcribe(Path("/tmp/a.m4a"), on_status=statuses.append)
        assert any("whisper.cpp" in s for s in statuses)


# ── factory wiring ────────────────────────────────────────────────────────────

class TestTranscriptionFactory:
    def test_returns_whisper_cpp_provider(self):
        provider = create_transcription_provider(
            {"transcription_service": "whisper_cpp", "whisper_cpp_model": "medium"}
        )
        assert isinstance(provider, WhisperCppProvider)
        assert provider._model_name == "medium"

    def test_defaults_model_when_unset(self):
        provider = create_transcription_provider({"transcription_service": "whisper_cpp"})
        assert provider._model_name == "large-v3-turbo"
