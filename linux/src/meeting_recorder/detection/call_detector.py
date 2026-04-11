"""
Orchestrates call detection by leveraging the AudioWatcher to monitor microphone usage. It includes a deduplication mechanism to ensure that the user is notified only once per call start, even if multiple audio streams are initialized simultaneously.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from meeting_recorder.config.defaults import CALL_DETECTION_DEDUP_WINDOW
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
            # When a browser opens a call (e.g. Google Meet) it creates several
            # source-outputs in quick succession (tab audio, video, etc.), which
            # would spam the user with repeated notifications for a single call start.
            if now - self._last_notified < CALL_DETECTION_DEDUP_WINDOW:
                return
            self._last_notified = now

        logger.info("Call detected from %s — notifying", source)
        try:
            self._on_call_detected(source)
        except Exception as exc:
            logger.warning("on_call_detected raised: %s", exc)
