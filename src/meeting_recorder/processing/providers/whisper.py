"""OpenAI Whisper transcription provider with timestamps and auto-chunking for files >25MB."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from ...config.defaults import (
    WHISPER_CHUNK_SIZE,
    WHISPER_OVERLAP_SECONDS,
    WHISPER_SIZE_LIMIT,
)

logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to [HH:MM:SS] string."""
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    return f"[{h:02d}:{m:02d}:{s:02d}]"


# Segments with no_speech_prob above this threshold are likely silence/hallucination
_NO_SPEECH_THRESHOLD = 0.6


def _format_segments(segments, time_offset: float = 0.0, skip_before: float = 0.0) -> str:
    """
    Format Whisper verbose_json segments into timestamped lines.

    time_offset: add this many seconds to each segment's start time (for chunks).
    skip_before: skip segments whose relative start is before this threshold
                 (used to drop the overlap region at the start of non-first chunks).
    """
    lines = []
    for seg in segments:
        if seg.start < skip_before:
            continue
        no_speech_prob = getattr(seg, "no_speech_prob", 0.0) or 0.0
        if no_speech_prob > _NO_SPEECH_THRESHOLD:
            logger.debug("Skipping silent segment at %.1fs (no_speech_prob=%.2f): %s",
                         seg.start, no_speech_prob, seg.text.strip())
            continue
        ts = _format_timestamp(seg.start + time_offset)
        text = seg.text.strip()
        if text:
            lines.append(f"{ts} {text}")
    return "\n".join(lines)


class WhisperProvider:
    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        size = audio_path.stat().st_size
        if size > WHISPER_SIZE_LIMIT:
            return self._transcribe_chunked(audio_path, on_status)
        return self._transcribe_single(audio_path, on_status)

    def _transcribe_single(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None,
        time_offset: float = 0.0,
        skip_before: float = 0.0,
    ) -> str:
        if on_status:
            on_status("Transcribing with Whisper…")
        client = self._get_client()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            mono_path = Path(tmp.name)
        try:
            # Mix stereo down to mono — Whisper is designed for mono and hallucinates
            # on stereo (especially when one channel is silent).
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(audio_path),
                    "-ac", "1", "-c:a", "libmp3lame", "-q:a", "2",
                    str(mono_path),
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.warning("ffmpeg mono conversion failed, using original: %s",
                               result.stderr.decode(errors="replace"))
                mono_path.unlink(missing_ok=True)
                mono_path = audio_path

            with open(mono_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model=self._model,
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    prompt="Meeting recording.",
                )
        finally:
            if mono_path != audio_path:
                mono_path.unlink(missing_ok=True)

        segments = response.segments or []
        return _format_segments(segments, time_offset=time_offset, skip_before=skip_before)

    def _transcribe_chunked(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None,
    ) -> str:
        """Split large file into overlapping chunks, transcribe each with correct timestamps."""
        if on_status:
            on_status("Audio file is large — splitting into chunks…")

        logger.info("Audio file %s is >25MB; using chunked transcription", audio_path)

        # Estimate segment duration
        size_bytes = audio_path.stat().st_size
        bitrate_bps = 128_000  # conservative MP3 estimate
        duration_secs = (size_bytes * 8) / bitrate_bps
        segment_secs = int((WHISPER_CHUNK_SIZE * 8) / bitrate_bps)
        segment_secs = max(60, segment_secs)

        with tempfile.TemporaryDirectory(prefix="whisper-chunks-") as tmpdir:
            chunks = self._split_audio(
                audio_path, tmpdir, duration_secs, segment_secs
            )
            if not chunks:
                raise RuntimeError("Failed to split audio into chunks")

            parts = []
            for i, (chunk_path, chunk_start) in enumerate(chunks):
                if on_status:
                    on_status(f"Transcribing chunk {i + 1}/{len(chunks)}…")
                # First chunk: no skip. Subsequent chunks: skip the overlap region.
                skip = WHISPER_OVERLAP_SECONDS if i > 0 else 0.0
                text = self._transcribe_single(
                    chunk_path,
                    on_status=None,
                    time_offset=chunk_start,
                    skip_before=skip,
                )
                if text:
                    parts.append(text)

        return "\n".join(parts)

    def _split_audio(
        self,
        audio_path: Path,
        tmpdir: str,
        duration_secs: float,
        segment_secs: int,
    ) -> list[tuple[Path, float]]:
        """
        Split audio into overlapping segments. Returns list of (path, start_time_secs).
        """
        overlap = WHISPER_OVERLAP_SECONDS
        chunks = []
        start = 0.0

        while start < duration_secs:
            chunk_path = os.path.join(tmpdir, f"chunk_{len(chunks):03d}.mp3")
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", str(audio_path),
                "-t", str(segment_secs + overlap),
                "-c", "copy",
                chunk_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                logger.warning(
                    "ffmpeg chunk failed: %s",
                    result.stderr.decode(errors="replace"),
                )
                break

            chunk_file = Path(chunk_path)
            if chunk_file.exists() and chunk_file.stat().st_size > 0:
                chunks.append((chunk_file, start))

            start += segment_secs

        logger.info("Split into %d chunks", len(chunks))
        return chunks
