"""
The dedicated Gemini speech-to-text provider (``gemini-3.5-transcribe``).

This is a different API surface from the prompt-based ``GeminiProvider``: the
model is served by the Interactions API rather than ``generateContent``, and it
rejects prompts, ``temperature`` and ``max_output_tokens`` outright. What it
takes instead is a ``transcription_config`` asking for verbatim mode with
speaker diarization and word timestamps, and what it returns is the plain
transcript plus per-word annotations.

It also caps a request at 30 minutes of audio once diarization or timestamps
are requested, so anything longer is split into overlapping chunks here,
transcribed piece by piece, and stitched back into one timeline (see
``processing/chunking.py`` and ``processing/diarization.py``).
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ...core.retry import retry_on_transient
from ...core.task_runner import CancelToken
from ...utils.audio_probe import detect_silences, extract_segment, probe_duration
from ..cancellation import PipelineCancelled
from ..chunking import (
    CHUNK_MAX_SECONDS,
    CHUNK_OVERLAP_SECONDS,
    Chunk,
    overlap_region,
    plan_chunks,
    plan_seams,
)
from ..diarization import (
    Word,
    default_speaker_labels,
    format_timestamp,
    format_transcript,
    group_turns,
    interaction_text,
    merge_chunks,
    shift_words,
    words_from_interaction,
)
from .gemini import delete_uploaded, mime_for_audio, upload_audio

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-transcribe"

# Statuses the Interactions API reports for a request that did not produce a
# usable result.
_FAILED_STATUSES = {"failed", "cancelled", "canceled", "expired", "requires_action"}


class GeminiTranscribeProvider:
    """Transcribe audio with Gemini's dedicated speech-to-text model.

    All subprocess and network access is injected so the chunking, stitching and
    error paths can be exercised without ffmpeg or a network.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_minutes: int = 5,
        language_codes: Sequence[str] | None = None,
        *,
        max_chunk_seconds: float = CHUNK_MAX_SECONDS,
        overlap_seconds: float = CHUNK_OVERLAP_SECONDS,
        client_factory: Callable[[], Any] | None = None,
        probe_fn: Callable[[Path], float | None] | None = None,
        silence_fn: Callable[[Path], list[tuple[float, float]]] | None = None,
        extract_fn: Callable[[Path, Path, float, float], Path] | None = None,
        upload_fn: Callable[..., Any] | None = None,
        retry_sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = float(timeout_minutes * 60)
        self._language_codes = list(language_codes or [])
        self._max_chunk = max_chunk_seconds
        self._overlap = overlap_seconds
        self._client_factory = client_factory
        self._probe = probe_fn or probe_duration
        self._detect_silences = silence_fn or detect_silences
        self._extract = extract_fn or extract_segment
        self._upload = upload_fn or upload_audio
        self._retry_sleep = retry_sleep_fn or time.sleep
        self._client: Any = None

    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
                return self._client
            try:
                from google import genai
            except ImportError:
                raise ImportError(
                    "google-genai is not installed. Run: pip install google-genai"
                ) from None
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    @property
    def _transcription_config(self) -> dict[str, Any]:
        """The request config: verbatim mode, diarized, with word timestamps.

        ``diarization_mode``/``timestamp_granularities`` are set inside the
        discriminated ``mode`` object rather than at the top level, where the
        API marks them deprecated. An empty ``language_codes`` list means
        automatic detection across the model's 85+ languages.
        """
        return {
            "language_codes": list(self._language_codes),
            "mode": {
                "type": "verbatim",
                "diarization_mode": "speaker",
                "timestamp_granularities": ["word"],
            },
        }

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> str:
        """Transcribe a recording of any length, chunking it when necessary."""
        duration = self._probe(audio_path)
        if duration is None:
            logger.warning(
                "Could not determine the duration of %s; sending it as a single "
                "request. Gemini will reject it if it exceeds the limit.",
                audio_path,
            )

        chunks = (
            plan_chunks(duration, max_chunk=self._max_chunk, overlap=self._overlap)
            if duration is not None
            else [Chunk(index=0, start=0.0, end=0.0)]
        )

        if len(chunks) == 1:
            return self._transcribe_whole(audio_path, on_status, cancel_token)

        logger.info(
            "Chunking %.1fs of audio into %d parts of ~%.1fs for %s",
            duration,
            len(chunks),
            chunks[0].duration,
            self._model,
        )
        return self._transcribe_chunked(audio_path, chunks, on_status, cancel_token)

    # ------------------------------------------------------------------

    def _transcribe_whole(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None,
        cancel_token: CancelToken | None,
    ) -> str:
        self._check_cancelled(cancel_token)
        words, fallback = self._transcribe_file(
            audio_path, on_status, label="Transcribing with Gemini…"
        )
        if not words:
            return fallback
        return format_transcript(group_turns(words), default_speaker_labels(words))

    def _transcribe_chunked(
        self,
        audio_path: Path,
        chunks: Sequence[Chunk],
        on_status: Callable[[str], None] | None,
        cancel_token: CancelToken | None,
    ) -> str:
        if on_status:
            on_status(f"Preparing {len(chunks)} audio parts…")
        silences = self._detect_silences(audio_path)
        seams = plan_seams(chunks, silences)
        overlaps = [overlap_region(chunks[i], chunks[i + 1]) for i in range(len(chunks) - 1)]

        per_chunk: list[list[Word]] = []
        suffix = audio_path.suffix or ".mp3"

        with tempfile.TemporaryDirectory(prefix="meeting-recorder-chunks-") as tmpdir:
            for chunk in chunks:
                self._check_cancelled(cancel_token)
                span = f"{format_timestamp(chunk.start)}-{format_timestamp(chunk.end)}"
                label = f"Transcribing part {chunk.index + 1} of {len(chunks)} ({span})…"
                part_path = Path(tmpdir) / f"part-{chunk.index:03d}{suffix}"
                self._extract(audio_path, part_path, chunk.start, chunk.duration)

                words, _ = self._transcribe_file(part_path, on_status, label=label)
                if not words:
                    raise RuntimeError(
                        f"Gemini returned no word annotations for part "
                        f"{chunk.index + 1} of {len(chunks)} ({span}); cannot "
                        "stitch a chunked transcript without them."
                    )
                per_chunk.append(shift_words(words, chunk.start))
                part_path.unlink(missing_ok=True)

        merged = merge_chunks(per_chunk, seams, overlaps)
        return format_transcript(group_turns(merged), default_speaker_labels(merged))

    # ------------------------------------------------------------------

    def _transcribe_file(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None,
        *,
        label: str,
    ) -> tuple[list[Word], str]:
        """Upload and transcribe one file; returns (words, plain text fallback)."""
        client = self._get_client()
        uploaded = self._upload(client, audio_path, mime_for_audio(audio_path), on_status)
        try:
            if on_status:
                on_status(label)
            interaction = retry_on_transient(
                lambda: client.interactions.create(
                    model=self._model,
                    input=[
                        {
                            "type": "audio",
                            "uri": uploaded.uri,
                            "mime_type": getattr(uploaded, "mime_type", None)
                            or mime_for_audio(audio_path),
                        }
                    ],
                    generation_config={"transcription_config": self._transcription_config},
                    timeout=self._timeout_seconds,
                ),
                description="Gemini transcription",
                sleep_fn=self._retry_sleep,
            )
        except Exception as exc:
            raise self._wrap_timeout(exc) from exc
        finally:
            delete_uploaded(client, uploaded)

        self._log_usage(interaction)
        self._raise_for_status(interaction)

        words = words_from_interaction(interaction)
        text = interaction_text(interaction)
        if not words and not text:
            raise RuntimeError(f"Gemini returned an empty transcription for {audio_path.name}.")
        if not words:
            logger.warning(
                "Gemini returned no word annotations for %s; falling back to the "
                "plain transcript without speaker labels or timestamps.",
                audio_path.name,
            )
        return words, text

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    @staticmethod
    def _check_cancelled(cancel_token: CancelToken | None) -> None:
        if cancel_token is not None and cancel_token.cancelled:
            raise PipelineCancelled()

    @staticmethod
    def _log_usage(interaction: Any) -> None:
        usage = getattr(interaction, "usage", None)
        if not usage:
            return
        logger.info(
            "Transcription token usage — input: %s, output: %s, total: %s",
            getattr(usage, "total_input_tokens", "?"),
            getattr(usage, "total_output_tokens", "?"),
            getattr(usage, "total_tokens", "?"),
        )

    @staticmethod
    def _raise_for_status(interaction: Any) -> None:
        status = getattr(interaction, "status", None)
        status_text = str(getattr(status, "value", status) or "").lower()
        errors = getattr(interaction, "errors", None) or []
        if status_text in _FAILED_STATUSES or errors:
            messages = [str(getattr(error, "message", None) or error) for error in errors]
            detail = "; ".join(m for m in messages if m) or status_text or "unknown"
            raise RuntimeError(f"Gemini transcription failed ({detail})")

    def _wrap_timeout(self, exc: Exception) -> Exception:
        """Turn an httpx timeout into a message that says what to do about it."""
        name = type(exc).__name__
        if "Timeout" in name or "timeout" in str(exc).lower():
            minutes = int(self._timeout_seconds // 60)
            return RuntimeError(
                f"Gemini did not respond within {minutes} minutes (transcription). "
                "Raise the processing timeout in Settings → Models, or try again."
            )
        return exc
