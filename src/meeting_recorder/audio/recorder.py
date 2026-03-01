"""Recording thread: named pipes, parec + ffmpeg subprocess lifecycle, pause/resume."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .devices import get_default_source, get_default_sink, get_monitor_source
from .mixer import build_ffmpeg_command

logger = logging.getLogger(__name__)


class RecordingError(Exception):
    pass


class Recorder:
    """
    Manages the full recording lifecycle.

    Usage:
        r = Recorder(output_path, on_tick=..., on_error=...)
        r.start()
        r.pause()
        r.resume()
        r.stop()   # blocks until ffmpeg exits
    """

    def __init__(
        self,
        output_path: Path,
        on_tick: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._output_path = output_path
        self._on_tick = on_tick
        self._on_error = on_error

        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._mic_pipe: str | None = None
        self._monitor_pipe: str | None = None

        self._parec_mic: subprocess.Popen | None = None
        self._parec_monitor: subprocess.Popen | None = None
        self._ffmpeg: subprocess.Popen | None = None

        self._timer_thread: threading.Thread | None = None
        self._elapsed: int = 0  # seconds
        self._paused = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start recording. Raises RecordingError on failure."""
        mic_source = get_default_source()
        sink = get_default_sink()
        if not mic_source or not sink:
            raise RecordingError("No audio devices found. Check audio setup.")

        monitor_source = get_monitor_source(sink)

        # Create named pipes in a temp directory
        self._tmpdir = tempfile.TemporaryDirectory(prefix="meeting-recorder-")
        tmpdir = self._tmpdir.name
        self._mic_pipe = os.path.join(tmpdir, "mic.pipe")
        self._monitor_pipe = os.path.join(tmpdir, "monitor.pipe")
        os.mkfifo(self._mic_pipe)
        os.mkfifo(self._monitor_pipe)

        # Start parec processes BEFORE ffmpeg (pipes must have writers first)
        try:
            self._parec_mic = subprocess.Popen(
                [
                    "parec",
                    "--device", mic_source,
                    "--format=s16le",
                    "--rate=44100",
                    "--channels=1",
                    "--raw",
                    self._mic_pipe,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._parec_monitor = subprocess.Popen(
                [
                    "parec",
                    "--device", monitor_source,
                    "--format=s16le",
                    "--rate=44100",
                    "--channels=1",
                    "--raw",
                    self._monitor_pipe,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self._cleanup()
            raise RecordingError(
                "parec not found. Install pulseaudio-utils or pipewire-pulse."
            )

        # Brief delay to let parec open the pipes before ffmpeg tries to read
        time.sleep(0.3)

        cmd = build_ffmpeg_command(
            self._mic_pipe,
            self._monitor_pipe,
            self._output_path,
        )
        try:
            self._ffmpeg = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self._cleanup()
            raise RecordingError("ffmpeg not found. Please install ffmpeg.")

        # Start the timer thread
        self._stop_event.clear()
        self._paused = False
        self._elapsed = 0
        self._timer_thread = threading.Thread(
            target=self._timer_loop, daemon=True
        )
        self._timer_thread.start()

        # Monitor thread to detect unexpected ffmpeg exit
        threading.Thread(target=self._monitor_ffmpeg, daemon=True).start()

        logger.info("Recording started → %s", self._output_path)

    def pause(self) -> None:
        """Pause recording by sending SIGSTOP to parec processes."""
        with self._lock:
            if self._paused:
                return
            self._paused = True
        self._signal_parec(signal.SIGSTOP)
        logger.info("Recording paused")

    def resume(self) -> None:
        """Resume recording by sending SIGCONT to parec processes."""
        with self._lock:
            if not self._paused:
                return
            self._paused = False
        self._signal_parec(signal.SIGCONT)
        logger.info("Recording resumed")

    def stop(self) -> None:
        """Stop recording gracefully. Waits for ffmpeg to finish."""
        logger.info("Stopping recording...")
        self._stop_event.set()

        # Resume parec first so pipes drain cleanly
        if self._paused:
            self._signal_parec(signal.SIGCONT)

        # Terminate parec processes
        for proc in (self._parec_mic, self._parec_monitor):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

        # Wait for ffmpeg to finish writing (it'll exit when pipes close)
        if self._ffmpeg and self._ffmpeg.poll() is None:
            try:
                self._ffmpeg.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg did not exit in time; killing")
                self._ffmpeg.kill()
                self._ffmpeg.wait()

        if self._timer_thread:
            self._timer_thread.join(timeout=2)

        self._cleanup()
        logger.info("Recording stopped. File: %s", self._output_path)

    @property
    def elapsed(self) -> int:
        return self._elapsed

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _signal_parec(self, sig: signal.Signals) -> None:
        for proc in (self._parec_mic, self._parec_monitor):
            if proc and proc.poll() is None:
                try:
                    proc.send_signal(sig)
                except ProcessLookupError:
                    pass

    def _timer_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(1)
            if not self._stop_event.is_set():
                with self._lock:
                    paused = self._paused
                if not paused:
                    self._elapsed += 1
                    if self._on_tick:
                        self._on_tick(self._elapsed)

    def _monitor_ffmpeg(self) -> None:
        """Watch for unexpected ffmpeg exit and report error."""
        if not self._ffmpeg:
            return
        retcode = self._ffmpeg.wait()
        if not self._stop_event.is_set() and retcode != 0:
            stderr = b""
            if self._ffmpeg.stderr:
                stderr = self._ffmpeg.stderr.read()
            msg = f"ffmpeg exited unexpectedly (code {retcode}): {stderr.decode(errors='replace')}"
            logger.error(msg)
            if self._on_error:
                self._on_error(msg)
            self._stop_event.set()

    def _cleanup(self) -> None:
        if self._tmpdir:
            try:
                self._tmpdir.cleanup()
            except Exception:
                pass
            self._tmpdir = None
        self._mic_pipe = None
        self._monitor_pipe = None
        self._parec_mic = None
        self._parec_monitor = None
        self._ffmpeg = None
