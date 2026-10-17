"""
Tests for the dedicated Gemini speech-to-text provider.

The SDK is not installed in CI, so every boundary is injected: the client (and
therefore ``interactions.create``), the audio probe, silence detection, segment
extraction and the file upload. What is covered here is the request shape the
Interactions API requires, the single-request path, the chunk-and-stitch path
for recordings over the 30-minute diarized limit, cancellation between chunks,
and the error paths.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from meeting_recorder.core.task_runner import CancelToken
from meeting_recorder.processing.cancellation import PipelineCancelled
from meeting_recorder.processing.providers.gemini_transcribe import (
    GeminiTranscribeProvider,
)


def _word(text, start, end, speaker, index_start, index_end):
    return {
        "type": "word_info",
        "text": text,
        "start_index": index_start,
        "end_index": index_end,
        "start_offset": f"{start}s",
        "end_offset": f"{end}s",
        "speaker": speaker,
    }


def _response(words):
    """Build an Interaction response from (text, start, end, speaker) tuples."""
    text = " ".join(w[0] for w in words)
    annotations = []
    cursor = 0
    for word, start, end, speaker in words:
        annotations.append(_word(word, start, end, speaker, cursor, cursor + len(word)))
        cursor += len(word) + 1
    return SimpleNamespace(
        status="completed",
        errors=[],
        output_text=text,
        usage=SimpleNamespace(total_input_tokens=100, total_output_tokens=20, total_tokens=120),
        steps=[
            SimpleNamespace(
                type="model_output",
                content=[SimpleNamespace(type="text", text=text, annotations=annotations)],
            )
        ],
    )


class _FakeInteractions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("interactions.create called more times than expected")
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeFiles:
    def __init__(self):
        self.deleted: list[str] = []

    def delete(self, name):
        self.deleted.append(name)


class _FakeClient:
    def __init__(self, responses):
        self.interactions = _FakeInteractions(responses)
        self.files = _FakeFiles()


def _provider(responses, **kwargs):
    client = _FakeClient(responses)
    uploads: list[Path] = []

    def fake_upload(_client, audio_path, mime_type, _on_status=None):
        uploads.append(Path(audio_path))
        return SimpleNamespace(
            uri=f"files/{Path(audio_path).name}",
            mime_type=mime_type,
            name=f"files/{Path(audio_path).name}",
        )

    provider = GeminiTranscribeProvider(
        api_key="AIzaTest",
        client_factory=lambda: client,
        upload_fn=fake_upload,
        retry_sleep_fn=lambda _seconds: None,
        **kwargs,
    )
    return provider, client, uploads


class TestRequestShape:
    def test_asks_for_verbatim_diarized_word_timestamps(self):
        provider, client, _ = _provider(
            [_response([("Hi", 0.0, 0.5, "spk_1")])],
            probe_fn=lambda _p: 60.0,
        )
        provider.transcribe(Path("rec.mp3"))

        call = client.interactions.calls[0]
        assert call["model"] == "gemini-3.5-transcribe"
        config = call["generation_config"]["transcription_config"]
        assert config["mode"] == {
            "type": "verbatim",
            "diarization_mode": "speaker",
            "timestamp_granularities": ["word"],
        }
        # Empty language list means automatic detection.
        assert config["language_codes"] == []
        # The model rejects these outright; they must never be sent.
        assert "temperature" not in call["generation_config"]
        assert "max_output_tokens" not in call["generation_config"]
        assert "contents" not in call

    def test_audio_is_passed_by_uri_with_its_mime_type(self):
        provider, client, _ = _provider(
            [_response([("Hi", 0.0, 0.5, "spk_1")])],
            probe_fn=lambda _p: 60.0,
        )
        provider.transcribe(Path("rec.m4a"))
        assert client.interactions.calls[0]["input"] == [
            {"type": "audio", "uri": "files/rec.m4a", "mime_type": "audio/m4a"}
        ]

    def test_language_hints_are_forwarded(self):
        provider, client, _ = _provider(
            [_response([("Hi", 0.0, 0.5, "spk_1")])],
            probe_fn=lambda _p: 60.0,
            language_codes=["en-US", "hi-IN"],
        )
        provider.transcribe(Path("rec.mp3"))
        config = client.interactions.calls[0]["generation_config"]["transcription_config"]
        assert config["language_codes"] == ["en-US", "hi-IN"]

    def test_timeout_is_passed_in_seconds(self):
        provider, client, _ = _provider(
            [_response([("Hi", 0.0, 0.5, "spk_1")])],
            probe_fn=lambda _p: 60.0,
            timeout_minutes=8,
        )
        provider.transcribe(Path("rec.mp3"))
        assert client.interactions.calls[0]["timeout"] == pytest.approx(480.0)


class TestSingleRequestPath:
    def test_short_recording_is_one_request_with_no_extraction(self):
        extracted = []
        provider, client, uploads = _provider(
            [
                _response(
                    [
                        ("Hello", 5.0, 5.4, "spk_1"),
                        ("there", 5.5, 6.0, "spk_1"),
                        ("Hi", 9.0, 9.4, "spk_2"),
                    ]
                )
            ],
            probe_fn=lambda _p: 600.0,
            extract_fn=lambda *a: extracted.append(a),
        )
        transcript = provider.transcribe(Path("rec.mp3"))

        assert len(client.interactions.calls) == 1
        assert extracted == []
        assert uploads == [Path("rec.mp3")]
        assert transcript == ("[00:00:05] **Person 1:** Hello there\n[00:00:09] **Person 2:** Hi")

    def test_uploaded_file_is_deleted_afterwards(self):
        provider, client, _ = _provider(
            [_response([("Hi", 0.0, 0.5, "spk_1")])],
            probe_fn=lambda _p: 60.0,
        )
        provider.transcribe(Path("rec.mp3"))
        assert client.files.deleted == ["files/rec.mp3"]

    def test_unknown_duration_is_sent_as_a_single_request(self):
        provider, client, _ = _provider(
            [_response([("Hi", 0.0, 0.5, "spk_1")])],
            probe_fn=lambda _p: None,
        )
        assert provider.transcribe(Path("rec.mp3"))
        assert len(client.interactions.calls) == 1

    def test_response_without_annotations_falls_back_to_plain_text(self):
        bare = SimpleNamespace(
            status="completed",
            errors=[],
            output_text="Just the words, no annotations.",
            usage=None,
            steps=[],
        )
        provider, _, _ = _provider([bare], probe_fn=lambda _p: 60.0)
        assert provider.transcribe(Path("rec.mp3")) == "Just the words, no annotations."


class TestChunkedPath:
    def _long_provider(self, responses, duration=3600.0, **kwargs):
        extracted: list[tuple] = []

        def fake_extract(src, dest, start, length):
            extracted.append((Path(src).name, Path(dest).name, start, length))
            return Path(dest)

        provider, client, uploads = _provider(
            responses,
            probe_fn=lambda _p: duration,
            silence_fn=lambda _p: [],
            extract_fn=fake_extract,
            **kwargs,
        )
        return provider, client, uploads, extracted

    def test_one_request_per_chunk_with_balanced_spans(self):
        # An hour plans as 3 x 20.5 min starting at 0 / 1185 / 2370.
        responses = [
            _response([("alpha", 10.0, 11.0, "spk_1")]),
            _response([("beta", 100.0, 101.0, "spk_1")]),
            _response([("gamma", 100.0, 101.0, "spk_1")]),
        ]
        provider, client, uploads, extracted = self._long_provider(responses)
        provider.transcribe(Path("rec.mp3"))

        assert len(client.interactions.calls) == 3
        assert [round(start) for _, _, start, _ in extracted] == [0, 1185, 2370]
        assert [round(length) for _, _, _, length in extracted] == [1230, 1230, 1230]
        # Each part is uploaded from the temporary directory, not the original.
        assert [p.name for p in uploads] == [
            "part-000.mp3",
            "part-001.mp3",
            "part-002.mp3",
        ]

    def test_chunk_extension_follows_the_source(self):
        responses = [_response([("a", 1.0, 2.0, "spk_1")]) for _ in range(3)]
        provider, _, uploads, _ = self._long_provider(responses)
        provider.transcribe(Path("rec.m4a"))
        assert all(p.suffix == ".m4a" for p in uploads)

    def test_timeline_is_stitched_and_speakers_unified(self):
        # Chunk 0 covers 0-1230; chunk 1 starts at 1185, so 1185-1230 is shared.
        # The shared words let chunk 1's spk_5 be recognised as chunk 0's spk_2.
        first = _response(
            [
                ("Morning", 10.0, 11.0, "spk_1"),
                ("agreed", 20.0, 21.0, "spk_2"),
                # 1200.0 absolute = 15.0 into chunk 0's own timeline... these
                # offsets are chunk-relative, so 1200 here means 1200s in.
                ("shared", 1200.0, 1201.0, "spk_2"),
            ]
        )
        second = _response(
            [
                # 15.0 into chunk 1 == 1200.0 absolute: the same shared word.
                ("shared", 15.0, 16.0, "spk_5"),
                ("later", 500.0, 501.0, "spk_5"),
            ]
        )
        third = _response([("end", 1000.0, 1001.0, "spk_3")])

        provider, _, _, _ = self._long_provider([first, second, third])
        transcript = provider.transcribe(Path("rec.mp3"))

        lines = transcript.splitlines()
        # "shared" appears once, from the chunk that owns that side of the seam.
        assert sum(line.count("shared") for line in lines) == 1
        # spk_2 and spk_5 are the same person, so "agreed", "shared" and
        # "later" are all attributed to Person 2.
        assert "[00:00:20] **Person 2:** agreed" in transcript
        assert "[00:28:05] **Person 2:** later" in transcript
        # Offsets are absolute: chunk 1's 500s lands at 1185 + 500 = 1685s.
        assert "[00:20:00]" in transcript

    def test_cancel_between_chunks_stops_the_remaining_requests(self):
        token = CancelToken()
        responses = [_response([("a", 1.0, 2.0, "spk_1")]) for _ in range(3)]
        provider, client, _, _ = self._long_provider(responses)

        original_create = client.interactions.create

        def cancel_after_first(**kwargs):
            token.cancel()
            return original_create(**kwargs)

        client.interactions.create = cancel_after_first

        with pytest.raises(PipelineCancelled):
            provider.transcribe(Path("rec.mp3"), cancel_token=token)
        assert len(client.interactions.calls) == 1

    def test_already_cancelled_token_makes_no_request(self):
        token = CancelToken()
        token.cancel()
        provider, client, _, _ = self._long_provider([])
        with pytest.raises(PipelineCancelled):
            provider.transcribe(Path("rec.mp3"), cancel_token=token)
        assert client.interactions.calls == []

    def test_silences_move_the_seam(self):
        # 30 min plans as 2 x 922.5s chunks overlapping 877.5-922.5, whose
        # midpoint is 900. A silence at 915-917 pulls the seam to 916, which
        # changes which chunk owns the word at 910: the two chunks transcribed
        # it slightly differently, so the text says which side won.
        first = _response([("alpha", 910.0, 911.0, "spk_1")])
        second = _response(
            [
                ("alfa", 32.5, 33.5, "spk_1"),  # 877.5 + 32.5 = 910.0 absolute
                ("beta", 60.0, 61.0, "spk_1"),  # 937.5 absolute, past the seam
            ]
        )

        def run(silences):
            provider, _, _ = _provider(
                [first, second],
                probe_fn=lambda _p: 1800.0,
                silence_fn=lambda _p: silences,
                extract_fn=lambda *a: Path(a[1]),
            )
            return provider.transcribe(Path("rec.mp3"))

        snapped = run([(915.0, 917.0)])
        assert "alpha" in snapped and "alfa" not in snapped
        assert "beta" in snapped

        # Without the silence the seam stays at the midpoint and the later
        # chunk owns the same word instead.
        midpoint = run([])
        assert "alfa" in midpoint and "alpha" not in midpoint

    def test_a_chunk_without_annotations_fails_loudly(self):
        bare = SimpleNamespace(
            status="completed", errors=[], output_text="text only", usage=None, steps=[]
        )
        responses = [_response([("a", 1.0, 2.0, "spk_1")]), bare, bare]
        provider, _, _, _ = self._long_provider(responses)
        with pytest.raises(RuntimeError, match="no word annotations for part 2"):
            provider.transcribe(Path("rec.mp3"))

    def test_extraction_failure_propagates(self):
        def boom(*_args):
            raise RuntimeError("ffmpeg failed to extract")

        provider, _, _ = _provider(
            [],
            probe_fn=lambda _p: 3600.0,
            silence_fn=lambda _p: [],
            extract_fn=boom,
        )
        with pytest.raises(RuntimeError, match="ffmpeg failed to extract"):
            provider.transcribe(Path("rec.mp3"))


class TestErrorHandling:
    def test_failed_status_raises_with_the_api_message(self):
        failed = SimpleNamespace(
            status="failed",
            errors=[SimpleNamespace(code=3, message="audio too long")],
            output_text="",
            usage=None,
            steps=[],
        )
        provider, _, _ = _provider([failed], probe_fn=lambda _p: 60.0)
        with pytest.raises(RuntimeError, match="audio too long"):
            provider.transcribe(Path("rec.mp3"))

    def test_errors_without_a_failed_status_still_raise(self):
        payload = SimpleNamespace(
            status="completed",
            errors=[SimpleNamespace(code=8, message="quota exceeded")],
            output_text="partial",
            usage=None,
            steps=[],
        )
        provider, _, _ = _provider([payload], probe_fn=lambda _p: 60.0)
        with pytest.raises(RuntimeError, match="quota exceeded"):
            provider.transcribe(Path("rec.mp3"))

    def test_empty_response_raises(self):
        empty = SimpleNamespace(status="completed", errors=[], output_text="", usage=None, steps=[])
        provider, _, _ = _provider([empty], probe_fn=lambda _p: 60.0)
        with pytest.raises(RuntimeError, match="empty transcription"):
            provider.transcribe(Path("rec.mp3"))

    def test_timeout_is_reported_with_the_configured_limit(self):
        class FakeTimeout(Exception):
            pass

        FakeTimeout.__name__ = "ReadTimeout"
        # Timeout-ish errors are retried first (see core.retry.is_transient),
        # so every attempt has to fail before the friendly message surfaces.
        provider, _, _ = _provider(
            [FakeTimeout("timed out")] * 3,
            probe_fn=lambda _p: 60.0,
            timeout_minutes=5,
        )
        with pytest.raises(RuntimeError, match="within 5 minutes"):
            provider.transcribe(Path("rec.mp3"))

    def test_upload_is_still_cleaned_up_when_the_request_fails(self):
        provider, client, _ = _provider([RuntimeError("boom")], probe_fn=lambda _p: 60.0)
        with pytest.raises(RuntimeError, match="boom"):
            provider.transcribe(Path("rec.mp3"))
        assert client.files.deleted == ["files/rec.mp3"]


class TestStatusReporting:
    def test_chunk_progress_names_the_part_and_its_span(self):
        messages: list[str] = []
        responses = [_response([("a", 1.0, 2.0, "spk_1")]) for _ in range(3)]
        provider, _, _ = _provider(
            responses,
            probe_fn=lambda _p: 3600.0,
            silence_fn=lambda _p: [],
            extract_fn=lambda *a: Path(a[1]),
        )
        provider.transcribe(Path("rec.mp3"), on_status=messages.append)
        assert "Preparing 3 audio parts…" in messages
        assert any(m.startswith("Transcribing part 2 of 3 (00:19:45-00:40:15)") for m in messages)
