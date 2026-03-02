"""Orchestrates transcription → summarization with status callbacks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Runs the full AI processing pipeline for a completed recording.

    - If both transcription and summarization are Gemini, uses single-call path.
    - Otherwise runs transcription then summarization as separate calls.
    - Writes transcript and notes to their respective paths.
    """

    def __init__(
        self,
        config: dict,
        audio_path: Path | None,
        transcript_path: Path | None,
        notes_path: Path | None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._audio_path = audio_path
        self._transcript_path = transcript_path
        self._notes_path = notes_path
        self._on_status = on_status

    def run(self) -> None:
        """Execute the pipeline. Raises on failure."""
        self._run_separate()

    # ------------------------------------------------------------------

    def _run_separate(self) -> None:
        """Separate transcription and summarization calls."""
        from .transcription import create_transcription_provider
        from .summarization import create_summarization_provider
        audio_path = self._audio_path

        # Transcription
        ts_provider = create_transcription_provider(self._config)
        transcript = ts_provider.transcribe(
            audio_path=audio_path,
            on_status=self._on_status,
        )

        if self._on_status:
            self._on_status("Summarizing…")

        # Summarization
        ss_provider = create_summarization_provider(self._config)
        notes = ss_provider.summarize(transcript, on_status=self._on_status)

        self._write_results(transcript, notes)

    def _write_results(self, transcript: str, notes: str) -> None:
        if self._on_status:
            self._on_status("Saving results…")

        if self._transcript_path:
            self._transcript_path.parent.mkdir(parents=True, exist_ok=True)
            self._transcript_path.write_text(transcript, encoding="utf-8")
            logger.info("Transcript saved: %s", self._transcript_path)

        if self._notes_path:
            self._notes_path.parent.mkdir(parents=True, exist_ok=True)
            self._notes_path.write_text(notes, encoding="utf-8")
            logger.info("Notes saved: %s", self._notes_path)
