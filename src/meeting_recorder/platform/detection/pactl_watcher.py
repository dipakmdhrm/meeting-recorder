from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable

from .base import AudioEventWatcher

logger = logging.getLogger(__name__)


class PactlAudioWatcher(AudioEventWatcher):
    """Monitors pactl subscribe for new source-output events."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._on_detected: Callable[[str], None] | None = None

    def start(self, on_detected: Callable[[str], None]) -> None:
        self._on_detected = on_detected
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["pactl", "subscribe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in iter(self._proc.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                if "new" in line.lower() and "source-output" in line.lower():
                    if self._on_detected:
                        self._on_detected("audio-stream")
        except Exception:
            logger.debug("pactl subscribe ended", exc_info=True)
