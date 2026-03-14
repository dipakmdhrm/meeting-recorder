"""
Manages the end-to-end AI processing workflow for recorded meetings. It coordinates the execution of transcription followed by summarization, ensuring that results are correctly saved to the designated output paths on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Runs the full AI processing pipeline for a completed recording.
    Runs transcription then summarization as separate calls, writing results to disk.
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
        # A previous optimisation ran both transcription and summarisation in a single
        # Gemini call (GEMINI_DUAL_PROMPT). It was removed because the model would stop
        # transcribing early (finish_reason=STOP, not MAX_TOKENS) to conserve output
        # budget for the notes section. Separate calls give each task its full quota.
        self._run_separate()

    # ------------------------------------------------------------------

    def _run_separate(self) -> None:
        """Separate transcription and summarization calls."""
        from .transcription import create_transcription_provider
        from .summarization import create_summarization_provider
        from .providers.ollama import unload_all_models

        ts_provider_name = self._config.get("transcription_provider", "gemini")
        ollama_host = self._config.get("ollama_host", "http://localhost:11434")

        # Before GPU transcription, evict any ollama models that are occupying VRAM.
        if ts_provider_name == "whisper":
            loaded = []
            try:
                from .providers.ollama import get_loaded_models
                loaded = get_loaded_models(ollama_host)
            except Exception:
                pass
            if loaded:
                if self._on_status:
                    self._on_status("Freeing GPU memory (unloading ollama models)…")
                unload_all_models(ollama_host)

        # Transcription
        ts_provider = create_transcription_provider(self._config)
        transcript = ts_provider.transcribe(
            audio_path=self._audio_path,
            on_status=self._on_status,
        )

        # Free transcription model from memory before loading summarization model.
        if hasattr(ts_provider, "unload"):
            if self._on_status:
                self._on_status("Freeing GPU memory (unloading transcription model)…")
            ts_provider.unload()

        # Summarization
        ss_provider = create_summarization_provider(self._config)
        notes = ss_provider.summarize(transcript, on_status=self._on_status)

        # Free summarization model from memory once done.
        if hasattr(ss_provider, "unload"):
            if self._on_status:
                self._on_status("Freeing GPU memory (unloading summarization model)…")
            ss_provider.unload()

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
