"""
Testable services for Whisper model cache detection and downloading.

Inject ``cache_root`` / ``model_loader`` in tests to avoid hitting the real
filesystem or the network:

    checker = WhisperStatusChecker(cache_root=tmp_path / "hub")
    downloader = WhisperDownloader(model_loader=lambda m: None)  # no-op
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _default_whisper_loader(model: str) -> None:
    """Load (and cache) a Whisper model via faster-whisper."""
    from faster_whisper import WhisperModel  # noqa: PLC0415
    WhisperModel(model, device="cpu", compute_type="int8")


class WhisperStatusChecker:
    """Checks whether a Whisper model is already cached on disk."""

    def __init__(self, cache_root: Path | None = None) -> None:
        self._cache_root = cache_root or (
            Path.home() / ".cache" / "huggingface" / "hub"
        )

    def is_cached(self, model: str) -> bool:
        from ..config.defaults import WHISPER_HF_REPOS  # noqa: PLC0415
        repo = WHISPER_HF_REPOS.get(model, f"Systran/faster-whisper-{model}")
        cache_dir = self._cache_root / f"models--{repo.replace('/', '--')}"
        return cache_dir.exists()


class WhisperDownloader:
    """Downloads a Whisper model by triggering WhisperModel initialisation."""

    def __init__(self, model_loader: Callable[[str], None] | None = None) -> None:
        self._load = model_loader or _default_whisper_loader

    def download(self, model: str) -> None:
        """Download *model*.  Raises on failure."""
        self._load(model)
