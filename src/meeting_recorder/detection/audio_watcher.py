"""pactl subscribe stdout reader for new microphone capture streams."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class AudioWatcher:
    """
    Runs `pactl subscribe` in the background and watches for new microphone
    capture stream events — catches browser-based calls (Meet, Teams web, etc.)
    that process watching would miss.
    """

    def __init__(self, on_detected: Callable[[str], None]) -> None:
        self._on_detected = on_detected
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["pactl", "subscribe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            logger.warning("pactl not found; audio watcher disabled")
            return

        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            # Example lines:
            #   Event 'new' on source-output #123
            #   Event 'new' on client #456
            if "new" in line and "source-output" in line:
                logger.debug("New audio source-output detected: %s", line)
                self._on_detected("audio-stream")

        if self._proc:
            self._proc.wait()
