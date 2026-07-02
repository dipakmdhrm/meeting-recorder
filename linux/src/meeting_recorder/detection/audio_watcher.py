"""
Monitors system audio events using 'pactl subscribe' to detect when applications start capturing
from the microphone. This allows the application to identify potential calls from browser-based
tools and desktop applications.

If the pactl process dies (PipeWire/PulseAudio restart, session hiccup) the
watcher restarts it with exponential backoff instead of silently going deaf
for the rest of the session.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Restart backoff: start small, double up to the cap; reset after a healthy run.
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
_HEALTHY_RUNTIME = 60.0


def is_call_start_event(line: str) -> bool:
    """True if a ``pactl subscribe`` line indicates a new mic capture stream.

    Example lines:
        Event 'new' on source-output #123
        Event 'new' on client #456

    A "source-output" is created whenever any app captures from the mic.
    This is intentionally broad: we want to catch browser calls (Meet,
    Teams), desktop apps (Zoom, Slack), and any other call software. The
    CallDetector deduplication window handles the burst of events a single
    call start produces.
    """
    return "new" in line and "source-output" in line


def _spawn_pactl() -> Any:
    return subprocess.Popen(
        ["pactl", "subscribe"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


class AudioWatcher:
    """
    Runs `pactl subscribe` in the background and watches for new microphone
    capture stream events — catches browser-based calls (Meet, Teams web, etc.)
    that process watching would miss.

    Runs on its own long-lived daemon thread (not the TaskRunner, which is for
    finite tasks) with an explicit stop(); the subprocess is terminated on stop,
    which unblocks the reading loop.
    """

    def __init__(
        self,
        on_detected: Callable[[str], None],
        spawn_fn: Callable[[], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_detected = on_detected
        self._spawn = spawn_fn or _spawn_pactl
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._proc: Any | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="audio-watcher: pactl subscribe"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------

    def _run(self) -> None:
        backoff = _INITIAL_BACKOFF
        while not self._stop.is_set():
            try:
                self._proc = self._spawn()
            except FileNotFoundError:
                logger.warning("pactl not found; audio watcher disabled")
                return

            started = self._monotonic()
            self._read_events(self._proc)

            if self._stop.is_set():
                break

            # pactl died on its own (PipeWire restart, crash) — restart it
            # with backoff so a broken audio server can't busy-loop us.
            returncode = self._proc.poll()
            if self._monotonic() - started >= _HEALTHY_RUNTIME:
                backoff = _INITIAL_BACKOFF
            logger.warning(
                "pactl subscribe exited unexpectedly (code %s); restarting in %.0fs",
                returncode,
                backoff,
            )
            self._sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)

        # Reap the child so it doesn't linger as a zombie.
        if self._proc:
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass

    def _read_events(self, proc: Any) -> None:
        """Consume subscribe output until the process exits or stop is set."""
        for line in proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if is_call_start_event(line):
                logger.debug("New audio source-output detected: %s", line)
                self._on_detected("audio-stream")
