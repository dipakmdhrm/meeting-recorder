"""ffmpeg command builder for mixing mic + system audio into MP3."""

from __future__ import annotations

from pathlib import Path

from ..config.defaults import AUDIO_CODEC, AUDIO_QUALITY, FFMPEG_THREAD_QUEUE_SIZE


def build_ffmpeg_command(
    mic_pipe: str,
    monitor_pipe: str,
    output_path: str | Path,
) -> list[str]:
    """
    Build ffmpeg command that reads two raw audio pipes into a stereo MP3.

    Channel layout:
      Left  (ch 0) = mic input    — the local speaker (you)
      Right (ch 1) = system audio — remote participants

    Both pipes carry raw s16le, 44100 Hz, mono audio from parec.
    amerge merges the two mono streams into a single stereo stream,
    preserving channel separation so AI models can distinguish speakers.
    """
    return [
        "ffmpeg",
        "-y",
        # Mic input from named pipe
        "-thread_queue_size", str(FFMPEG_THREAD_QUEUE_SIZE),
        "-f", "s16le",
        "-ar", "44100",
        "-ac", "1",
        "-i", str(mic_pipe),
        # System audio monitor input from named pipe
        "-thread_queue_size", str(FFMPEG_THREAD_QUEUE_SIZE),
        "-f", "s16le",
        "-ar", "44100",
        "-ac", "1",
        "-i", str(monitor_pipe),
        # Merge into stereo: mic → left, system → right
        "-filter_complex", "amerge=inputs=2",
        "-ac", "2",
        # Encode as MP3
        "-codec:a", AUDIO_CODEC,
        "-q:a", AUDIO_QUALITY,
        str(output_path),
    ]


def build_split_command(
    input_path: str | Path,
    segment_path_template: str,
    segment_duration_secs: int = 1200,  # 20 minutes
) -> list[str]:
    """
    Build ffmpeg command to split a large audio file into segments.
    segment_path_template should contain %03d, e.g. /tmp/chunk_%03d.mp3
    """
    return [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-f", "segment",
        "-segment_time", str(segment_duration_secs),
        "-c", "copy",
        str(segment_path_template),
    ]
