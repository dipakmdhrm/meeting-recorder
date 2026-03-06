"""Recording thread: ffmpeg subprocess lifecycle, pause/resume via segments."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Callable

from .devices import get_default_source, get_default_sink, get_monitor_source
from .mixer import build_ffmpeg_command, build_ffmpeg_command_mic_only

logger = logging.getLogger(__name__)

# Set by install.sh launcher when running as an installed app.
_INSTALLED = bool(os.environ.get("MEETING_RECORDER_INSTALLED"))
_SYSTEM_LOG_DIR = Path("/var/log/meeting-recorder")


def _ffmpeg_log_path(output_path: Path) -> Path:
    """
    Return the path where ffmpeg stderr should be written.

    Installed (MEETING_RECORDER_INSTALLED=1):
        /var/log/meeting-recorder/ffmpeg-<session-dir>.log
        e.g. /var/log/meeting-recorder/ffmpeg-14-32.log

    Dev mode (PYTHONPATH=src python3 -m meeting_recorder):
        <recording-dir>/ffmpeg.log
        e.g. ~/meetings/2026/March/05/14-32/ffmpeg.log
    """
    if _INSTALLED:
        try:
            _SYSTEM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Fall back to recording dir if system log dir is inaccessible.
            return output_path.with_name("ffmpeg.log")
        return _SYSTEM_LOG_DIR / f"ffmpeg-{output_path.parent.name}.log"
    return output_path.with_name("ffmpeg.log")


class RecordingError(Exception):
    pass


class Recorder:
    """
    Manages the full recording lifecycle.

    Pause/resume works via segments: on pause ffmpeg is terminated cleanly
    (saving the current segment), then on resume a new ffmpeg process writes
    a new segment.  On stop all segments are concatenated into the final
    output file so the paused intervals are excluded.

    Usage:
        r = Recorder(output_path, on_tick=..., on_error=...)
        r.start()
        r.pause()
        r.resume()
        r.stop()   # blocks until ffmpeg exits and segments are merged
    """

    def __init__(
        self,
        output_path: Path,
        mode: str = "headphones",
        quality: str = "2",
        on_tick: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._output_path = output_path
        self._mode = mode  # "headphones" = mic + monitor; "speaker" = mic only
        self._quality = quality
        self._on_tick = on_tick
        self._on_error = on_error

        self._ffmpeg: subprocess.Popen | None = None
        self._stderr_log: IO[bytes] | None = None
        self._ffmpeg_log_path: Path | None = None

        self._segments: list[Path] = []
        self._segment_index: int = 0

        # Cached device names — resolved once in start(), reused on resume().
        self._mic_source: str | None = None
        self._monitor_source: str | None = None

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
        self._mic_source = get_default_source()
        if not self._mic_source:
            raise RecordingError("No microphone found. Check audio setup.")

        if self._mode != "speaker":
            sink = get_default_sink()
            if not sink:
                raise RecordingError("No audio output device found. Check audio setup.")
            self._monitor_source = get_monitor_source(sink)

        self._stop_event.clear()
        self._paused = False
        self._elapsed = 0
        self._segments = []
        self._segment_index = 0

        self._start_ffmpeg_segment()

        self._timer_thread = threading.Thread(
            target=self._timer_loop, daemon=True
        )
        self._timer_thread.start()

        logger.info("Recording started → %s", self._output_path)

    def pause(self) -> None:
        """Pause recording by terminating the current ffmpeg segment cleanly."""
        with self._lock:
            if self._paused:
                return
            self._paused = True

        self._stop_ffmpeg_segment()
        logger.info("Recording paused — segment %d saved", self._segment_index)

    def resume(self) -> None:
        """Resume recording by starting a new ffmpeg segment."""
        with self._lock:
            if not self._paused:
                return
            self._paused = False

        self._segment_index += 1
        self._start_ffmpeg_segment()
        logger.info("Recording resumed — segment %d started", self._segment_index)

    def stop(self) -> None:
        """Stop recording, concatenate segments, and produce the final output."""
        logger.info("Stopping recording...")
        self._stop_event.set()

        self._stop_ffmpeg_segment()

        if self._timer_thread:
            self._timer_thread.join(timeout=2)

        self._ffmpeg = None

        if len(self._segments) == 0:
            logger.warning("No segments recorded.")
        elif len(self._segments) == 1:
            # Single segment — just rename to final output path.
            seg = self._segments[0]
            if seg != self._output_path:
                seg.rename(self._output_path)
        else:
            self._concatenate_segments()

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

    def _start_ffmpeg_segment(self) -> None:
        """Start a new ffmpeg process writing to a new segment file."""
        seg_path = _segment_path_for(self._output_path, self._segment_index)
        seg_path.parent.mkdir(parents=True, exist_ok=True)
        self._segments.append(seg_path)

        if self._mode == "speaker":
            cmd = build_ffmpeg_command_mic_only(self._mic_source, seg_path, quality=self._quality)
        else:
            cmd = build_ffmpeg_command(self._mic_source, self._monitor_source, seg_path, quality=self._quality)

        log_path = _ffmpeg_log_path(self._output_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ffmpeg_log_path = log_path
        # Append so all segments' logs are in one file.
        self._stderr_log = open(log_path, "ab")

        try:
            self._ffmpeg = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_log,
            )
        except FileNotFoundError:
            self._stderr_log.close()
            self._stderr_log = None
            raise RecordingError("ffmpeg not found. Please install ffmpeg.")

        threading.Thread(
            target=self._monitor_ffmpeg,
            args=(self._ffmpeg, self._segment_index),
            daemon=True,
        ).start()
        logger.info("ffmpeg segment %d → %s", self._segment_index, seg_path)

    def _stop_ffmpeg_segment(self) -> None:
        """Terminate the current ffmpeg process and close the log file."""
        if self._ffmpeg and self._ffmpeg.poll() is None:
            self._ffmpeg.terminate()
            try:
                self._ffmpeg.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg did not exit in time; killing")
                self._ffmpeg.kill()
                self._ffmpeg.wait()

        if self._stderr_log:
            self._stderr_log.close()
            self._stderr_log = None

    def _concatenate_segments(self) -> None:
        """Use ffmpeg concat demuxer to merge all segments into the output file."""
        concat_list = self._output_path.parent / f"{self._output_path.stem}_concat.txt"
        try:
            with open(concat_list, "w") as f:
                for seg in self._segments:
                    f.write(f"file '{seg.resolve()}'\n")

            log_path = _ffmpeg_log_path(self._output_path)
            with open(log_path, "ab") as log:
                result = subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-hide_banner", "-loglevel", "error",
                        "-f", "concat", "-safe", "0",
                        "-i", str(concat_list),
                        "-c", "copy",
                        str(self._output_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                    timeout=300,
                )
            if result.returncode != 0:
                logger.error("ffmpeg concat failed (code %d)", result.returncode)
            else:
                logger.info("Segments concatenated → %s", self._output_path)
                for seg in self._segments:
                    try:
                        seg.unlink()
                    except OSError:
                        pass
        except Exception:
            logger.exception("Failed to concatenate segments")
        finally:
            try:
                concat_list.unlink()
            except OSError:
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

    def _monitor_ffmpeg(self, proc: subprocess.Popen, seg_index: int) -> None:
        """Watch for unexpected ffmpeg exit and report error."""
        retcode = proc.wait()
        # Ignore intentional exits: stop sets _stop_event, pause sets _paused.
        with self._lock:
            is_paused = self._paused
        if not self._stop_event.is_set() and not is_paused and retcode != 0:
            msg = (
                f"ffmpeg exited unexpectedly on segment {seg_index} (code {retcode})"
                + (f", see {self._ffmpeg_log_path}" if self._ffmpeg_log_path else "")
            )
            logger.error(msg)
            if self._on_error:
                self._on_error(msg)
            self._stop_event.set()


def _segment_path_for(output_path: Path, index: int) -> Path:
    """Return the segment file path for a given index."""
    stem = output_path.stem
    suffix = output_path.suffix
    return output_path.parent / f"{stem}_seg{index:03d}{suffix}"
