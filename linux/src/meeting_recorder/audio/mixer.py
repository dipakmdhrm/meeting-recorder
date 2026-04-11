"""
Provides utility functions to construct ffmpeg command-line arguments for audio capture. It supports mixing multiple PulseAudio sources (e.g., microphone and system output) into a single stereo stream, applying filters like highpass to improve voice clarity.
"""

from __future__ import annotations

from pathlib import Path


def build_ffmpeg_command(
    source: str,
    monitor: str,
    output_path: str | Path,
    quality: str = "2",
) -> list[str]:
    """
    Build ffmpeg command that reads two PulseAudio sources into a stereo MP3.

    Channel layout:
      Left  (ch 0) = mic input    — the local speaker
      Right (ch 1) = system audio — remote participants

    amerge produces a true stereo file with separate channels, preserving
    speaker separation for AI transcription.
    """
    # highpass=f=80  : cut sub-80 Hz rumble that makes mics sound muffled
    # No denoiser: afftdn/anlmdn are both too slow for real-time use and cause
    # the input thread queue to fill, making ffmpeg drop packets and produce a
    # file shorter than the wall-clock recording duration.
    filter_str = "[0:a]highpass=f=80[mic];[mic][1:a]amerge=inputs=2[out]"
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        # thread_queue_size buffers packets between the PulseAudio input thread and
        # the filter/encode thread. Without it the queue fills up and ffmpeg silently
        # drops audio packets, producing a file shorter than the wall-clock recording.
        "-thread_queue_size", "4096",
        "-f", "pulse", "-i", source,
        "-thread_queue_size", "4096",
        "-f", "pulse", "-i", monitor,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-acodec", "libmp3lame",
        "-q:a", quality,
        str(output_path),
    ]


def build_ffmpeg_command_mic_only(
    source: str,
    output_path: str | Path,
    quality: str = "2",
) -> list[str]:
    """
    Build ffmpeg command that records the microphone only (no system audio).
    Used when recording with speakers — capturing the monitor would cause echo
    since the speaker output is already picked up by the mic.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-thread_queue_size", "4096",
        "-f", "pulse", "-i", source,
        "-af", "highpass=f=80",
        "-acodec", "libmp3lame",
        "-q:a", quality,
        str(output_path),
    ]


