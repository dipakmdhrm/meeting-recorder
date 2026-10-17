"""
ffprobe/ffmpeg helpers for inspecting and slicing a recording.

Used by the chunked Gemini transcription path, which has to split long audio
into sub-30-minute pieces. The subprocess calls are funnelled through an
injectable ``run_fn`` and the log parsing is pure, so both are unit-testable
without ffmpeg present.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

RunFn = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]

_PROBE_TIMEOUT = 30.0
# Silence detection has to decode the whole file; a multi-hour recording on a
# slow machine still finishes well inside this.
_SILENCE_TIMEOUT = 1800.0
_EXTRACT_TIMEOUT = 600.0

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")
# ffmpeg's progress/summary lines carry the decoded position as time=HH:MM:SS.ms
# (newer builds) or time=-577014:32:22.00 for "unknown".
_TIME_RE = re.compile(r"time=\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _default_run(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def probe_duration(audio_path: Path, *, run_fn: RunFn | None = None) -> float | None:
    """Return the audio duration in seconds, or None if it cannot be determined.

    Tries ffprobe's container metadata first, then falls back to a full decode
    with ffmpeg. The fallback matters: a recording whose ffmpeg was killed
    mid-write can have a truncated header with no duration field, and the
    chunk plan depends on knowing the real length.
    """
    run = run_fn or _default_run

    try:
        result = run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            _PROBE_TIMEOUT,
        )
        if result.returncode == 0 and (result.stdout or "").strip():
            value = float(result.stdout.strip())
            if value > 0:
                return value
    except Exception as exc:
        logger.debug("ffprobe duration failed for %s: %s", audio_path, exc)

    # Fallback: decode to null and read the last reported timestamp.
    try:
        result = run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-i",
                str(audio_path),
                "-f",
                "null",
                "-",
            ],
            _SILENCE_TIMEOUT,
        )
        decoded = parse_last_time(result.stderr or "")
        if decoded is not None and decoded > 0:
            logger.info("Duration of %s resolved by decoding: %.1fs", audio_path, decoded)
            return decoded
    except Exception as exc:
        logger.debug("ffmpeg decode duration failed for %s: %s", audio_path, exc)

    return None


def parse_last_time(stderr: str) -> float | None:
    """Return the largest ``time=HH:MM:SS.ms`` value in ffmpeg output, in seconds."""
    best: float | None = None
    for hours, minutes, seconds in _TIME_RE.findall(stderr):
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if best is None or total > best:
            best = total
    return best


def parse_silence_log(stderr: str) -> list[tuple[float, float]]:
    """Parse ffmpeg ``silencedetect`` output into (start, end) second pairs.

    Lines arrive as ``silence_start: 12.3`` followed by ``silence_end: 13.1 |
    silence_duration: 0.8``. A trailing ``silence_start`` with no matching end
    (silence running to EOF) is dropped, since a seam there would be past the
    last chunk boundary anyway.
    """
    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending = float(start_match.group(1))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match and pending is not None:
            end = float(end_match.group(1))
            if end > pending:
                silences.append((max(0.0, pending), end))
            pending = None
    return silences


def detect_silences(
    audio_path: Path,
    *,
    noise_db: float = -30.0,
    min_silence: float = 0.4,
    run_fn: RunFn | None = None,
) -> list[tuple[float, float]]:
    """Return silent intervals in the recording, or [] if detection fails.

    Failure is non-fatal by design: without silences the chunk seams fall back
    to the arithmetic overlap midpoint, which is still correct, just slightly
    more likely to land mid-word.
    """
    run = run_fn or _default_run
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run(cmd, _SILENCE_TIMEOUT)
    except Exception as exc:
        logger.warning("Silence detection failed for %s: %s", audio_path, exc)
        return []
    if result.returncode != 0:
        logger.warning("Silence detection exited %s for %s", result.returncode, audio_path)
        return []
    return parse_silence_log(result.stderr or "")


def extract_segment(
    src: Path,
    dest: Path,
    start: float,
    duration: float,
    *,
    run_fn: RunFn | None = None,
) -> Path:
    """Copy ``duration`` seconds of ``src`` starting at ``start`` into ``dest``.

    Uses stream copy, so this is fast and lossless and never re-encodes. ``-t``
    (duration) rather than ``-to`` is deliberate: with input seeking (``-ss``
    before ``-i``) ``-to`` has meant different things across ffmpeg versions,
    while ``-t`` has always been the length of the output.
    """
    run = run_fn or _default_run
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(src),
        "-c",
        "copy",
        str(dest),
    ]
    result = run(cmd, _EXTRACT_TIMEOUT)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(
            f"ffmpeg failed to extract {start:.1f}s-{start + duration:.1f}s "
            f"from {src.name}: {' / '.join(tail)}"
        )
    return dest
