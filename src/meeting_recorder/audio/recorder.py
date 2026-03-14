"""
Manages the audio recording lifecycle. Delegates raw capture to an AudioBackend
via dependency injection. Implements pause/resume via segments and produces
separate audio tracks (mic + system) with a merged combined file.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from ..platform.audio.base import (
    AudioBackend, AudioResult, CaptureMode, CaptureOutputPaths,
)

logger = logging.getLogger(__name__)


class RecordingError(Exception):
    pass


class Recorder:
    """
    Manages the full recording lifecycle with separate audio tracks.

    Pause/resume works via segments: on pause the backend capture is stopped
    (saving the current segment), then on resume a new capture writes a new
    segment. On stop all segments are concatenated per track and merged.

    Usage:
        r = Recorder(backend=..., output_dir=..., mode=..., quality=...,
                      separate_tracks=True, on_tick=..., on_error=...)
        r.start()
        r.pause()
        r.resume()
        result = r.stop()  # returns AudioResult
    """

    def __init__(
        self,
        backend: AudioBackend,
        output_dir: Path,
        mode: CaptureMode = CaptureMode.HEADPHONES,
        quality: str = "2",
        separate_tracks: bool = True,
        on_tick: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._backend = backend
        self._output_dir = output_dir
        self._mode = mode
        self._quality = quality
        self._separate_tracks = separate_tracks
        self._on_tick = on_tick
        self._on_error = on_error

        self._mic_segments: list[Path] = []
        self._system_segments: list[Path] = []
        self._segment_index: int = 0

        self._timer_thread: threading.Thread | None = None
        self._elapsed: int = 0
        self._paused = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start recording. Raises RecordingError on failure."""
        ok, err = self._backend.validate()
        if not ok:
            raise RecordingError(err)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._paused = False
        self._elapsed = 0
        self._mic_segments = []
        self._system_segments = []
        self._segment_index = 0

        self._start_segment()

        self._timer_thread = threading.Thread(
            target=self._timer_loop, daemon=True
        )
        self._timer_thread.start()

        logger.info("Recording started → %s", self._output_dir)

    def pause(self) -> None:
        """Pause recording by stopping the current capture segment."""
        with self._lock:
            if self._paused:
                return
            self._paused = True

        self._backend.stop_capture()
        logger.info("Recording paused — segment %d saved", self._segment_index)

    def resume(self) -> None:
        """Resume recording by starting a new capture segment."""
        with self._lock:
            if not self._paused:
                return
            self._paused = False

        self._segment_index += 1
        self._start_segment()
        logger.info("Recording resumed — segment %d started", self._segment_index)

    def stop(self) -> AudioResult:
        """Stop recording, concatenate segments, produce final output."""
        logger.info("Stopping recording...")
        self._stop_event.set()

        self._backend.stop_capture()

        if self._timer_thread:
            self._timer_thread.join(timeout=2)

        # Build final output paths
        combined_path = self._output_dir / "recording.mp3"
        mic_path = self._output_dir / "recording_mic.mp3" if self._separate_tracks else None
        system_path = (
            self._output_dir / "recording_system.mp3"
            if self._separate_tracks and self._mode == CaptureMode.HEADPHONES
            else None
        )

        # Concatenate mic segments
        if self._mic_segments:
            final_mic = mic_path or combined_path
            self._concatenate_segments(self._mic_segments, final_mic)

        # Concatenate system segments
        if self._system_segments:
            final_system = system_path or (self._output_dir / "_system_tmp.mp3")
            self._concatenate_segments(self._system_segments, final_system)

        # Merge tracks into combined file
        if self._separate_tracks and mic_path and mic_path.exists():
            if system_path and system_path.exists():
                self._merge_tracks(mic_path, system_path, combined_path)
            else:
                # Speaker mode or no system audio — mic IS the combined
                import shutil
                shutil.copy2(mic_path, combined_path)
        elif not self._separate_tracks and combined_path.exists():
            pass  # already in place
        elif self._mic_segments:
            # Fallback: if separate_tracks is off, mic segments → combined
            pass

        result = AudioResult(
            combined=combined_path,
            mic=mic_path if mic_path and mic_path.exists() else None,
            system=system_path if system_path and system_path.exists() else None,
        )

        logger.info("Recording stopped. Output dir: %s", self._output_dir)
        return result

    @property
    def elapsed(self) -> int:
        return self._elapsed

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_segment(self) -> None:
        """Start a new capture segment via the backend."""
        mic_seg = self._output_dir / f"recording_mic_seg{self._segment_index:03d}.mp3"
        self._mic_segments.append(mic_seg)

        system_seg = None
        if self._mode == CaptureMode.HEADPHONES:
            system_seg = self._output_dir / f"recording_system_seg{self._segment_index:03d}.mp3"
            self._system_segments.append(system_seg)

        paths = CaptureOutputPaths(mic=mic_seg, system=system_seg)

        try:
            self._backend.start_capture(paths, self._quality)
        except Exception as exc:
            raise RecordingError(f"Failed to start audio capture: {exc}") from exc

        logger.info("Segment %d started", self._segment_index)

    def _concatenate_segments(self, segments: list[Path], output: Path) -> None:
        """Use ffmpeg concat demuxer to merge segments."""
        existing = [s for s in segments if s.exists()]
        if not existing:
            return

        if len(existing) == 1:
            if existing[0] != output:
                existing[0].rename(output)
            return

        concat_list = output.parent / f"{output.stem}_concat.txt"
        try:
            with open(concat_list, "w") as f:
                for seg in existing:
                    f.write(f"file '{seg.resolve()}'\n")

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-c", "copy",
                    str(output),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error("ffmpeg concat failed (code %d): %s",
                            result.returncode, result.stderr.decode(errors="replace"))
            else:
                logger.info("Segments concatenated → %s", output)
                for seg in existing:
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

    def _merge_tracks(self, mic: Path, system: Path, output: Path) -> None:
        """Merge mic + system tracks into a combined stereo file."""
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-hide_banner", "-loglevel", "error",
                    "-i", str(mic),
                    "-i", str(system),
                    "-filter_complex", "[0:a][1:a]amerge=inputs=2[out]",
                    "-map", "[out]",
                    "-acodec", "libmp3lame", "-q:a", self._quality,
                    str(output),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error("Track merge failed: %s",
                            result.stderr.decode(errors="replace"))
            else:
                logger.info("Tracks merged → %s", output)
        except Exception:
            logger.exception("Failed to merge audio tracks")

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
