"""Call detector: audio watcher with deduplication."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..config.defaults import CALL_DETECTION_DEDUP_WINDOW
from .audio_watcher import AudioWatcher

logger = logging.getLogger(__name__)


class CallDetector:
    """
    Watches for new microphone capture streams via AudioWatcher (pactl subscribe).
    Notifies on_call_detected at most once per CALL_DETECTION_DEDUP_WINDOW seconds.
    """

    def __init__(self, on_call_detected: Callable[[str], None]) -> None:
        self._on_call_detected = on_call_detected
        self._last_notified: float = 0.0
        self._lock = threading.Lock()

        self._audio_watcher = AudioWatcher(
            on_detected=self._handle_detection
        )

    def start(self) -> None:
        logger.info("Call detector started")
        self._audio_watcher.start()

    def stop(self) -> None:
        logger.info("Call detector stopping")
        self._audio_watcher.stop()

    def _handle_detection(self, source: str) -> None:
        now = time.time()
        with self._lock:
            if now - self._last_notified < CALL_DETECTION_DEDUP_WINDOW:
                return
            self._last_notified = now

        logger.info("Call detected from %s — notifying", source)
        try:
            self._on_call_detected(source)
        except Exception as exc:
            logger.warning("on_call_detected raised: %s", exc)
