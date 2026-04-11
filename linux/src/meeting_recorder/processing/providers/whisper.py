"""
WhisperProvider: local speech-to-text using faster-whisper.
Models are downloaded automatically from HuggingFace on first use.
Note: no speaker diarization — output is timestamped segments only.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _detect_device() -> tuple[str, str]:
    """Return (device, compute_type) based on what's available."""
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types.__module__ or True:
            supported = ctranslate2.get_supported_compute_types("cuda")
            if supported:
                return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


class WhisperProvider:
    """Transcribes audio locally using faster-whisper (GPU if available, else CPU)."""

    def __init__(self, model: str = "large-v3-turbo") -> None:
        self._model_name = model
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise ImportError(
                    "faster-whisper is not installed. Run: pip install faster-whisper"
                )
            device, compute_type = _detect_device()
            logger.info(
                "Loading Whisper model '%s' (device=%s, compute_type=%s)",
                self._model_name, device, compute_type,
            )
            self._model = WhisperModel(
                self._model_name,
                device=device,
                compute_type=compute_type,
            )
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        if on_status:
            on_status(f"Loading Whisper model ({self._model_name})\u2026")

        model = self._load_model()

        if on_status:
            on_status(f"Transcribing with Whisper ({self._model_name})\u2026")

        segments, info = model.transcribe(str(audio_path), beam_size=5)
        logger.info(
            "Detected language: %s (confidence %.0f%%)",
            info.language,
            info.language_probability * 100,
        )

        lines = []
        for seg in segments:
            h = int(seg.start // 3600)
            m = int((seg.start % 3600) // 60)
            s = int(seg.start % 60)
            lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {seg.text.strip()}")

        return "\n".join(lines)

    def unload(self) -> None:
        """Release the model from GPU/CPU memory."""
        if self._model is not None:
            logger.info("Unloading Whisper model '%s' from memory", self._model_name)
            del self._model
            self._model = None
            gc.collect()
