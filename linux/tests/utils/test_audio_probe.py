"""
Tests for the ffprobe/ffmpeg audio helpers.

The subprocess calls go through an injected ``run_fn``, so the command shapes,
the duration fallback chain and the log parsing are all covered without ffmpeg
being installed.
"""

import subprocess
from pathlib import Path

import pytest

from meeting_recorder.utils.audio_probe import (
    detect_silences,
    extract_segment,
    parse_last_time,
    parse_silence_log,
    probe_duration,
)

SILENCE_LOG = """\
ffmpeg version 6.0 Copyright (c) 2000-2023 the FFmpeg developers
[silencedetect @ 0x55f1c0] silence_start: 12.345
[silencedetect @ 0x55f1c0] silence_end: 13.001 | silence_duration: 0.656
[silencedetect @ 0x55f1c0] silence_start: 61.5
[silencedetect @ 0x55f1c0] silence_end: 63.25 | silence_duration: 1.75
size=N/A time=00:20:30.00 bitrate=N/A speed=99x
"""


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _Recorder:
    """Captures the commands it is asked to run and replays canned results."""

    def __init__(self, *results):
        self.calls: list[list[str]] = []
        self._results = list(results)

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        if not self._results:
            return _result()
        return self._results.pop(0)


class TestParseSilenceLog:
    def test_pairs_starts_with_ends(self):
        assert parse_silence_log(SILENCE_LOG) == [(12.345, 13.001), (61.5, 63.25)]

    def test_unterminated_silence_is_dropped(self):
        log = "silence_start: 10.0\n"
        assert parse_silence_log(log) == []

    def test_end_without_start_is_ignored(self):
        log = "silence_end: 10.0 | silence_duration: 1.0\n"
        assert parse_silence_log(log) == []

    def test_zero_length_silence_is_dropped(self):
        log = "silence_start: 10.0\nsilence_end: 10.0 | silence_duration: 0.0\n"
        assert parse_silence_log(log) == []

    def test_negative_start_is_clamped_to_zero(self):
        log = "silence_start: -0.5\nsilence_end: 1.0 | silence_duration: 1.5\n"
        assert parse_silence_log(log) == [(0.0, 1.0)]

    def test_empty_input(self):
        assert parse_silence_log("") == []


class TestParseLastTime:
    def test_largest_timestamp_wins(self):
        text = "time=00:00:10.00\ntime=00:20:30.50\ntime=00:05:00.00\n"
        assert parse_last_time(text) == pytest.approx(1230.5)

    def test_hours_are_counted(self):
        assert parse_last_time("time=02:03:04.00") == pytest.approx(7384.0)

    def test_no_timestamp(self):
        assert parse_last_time("nothing here") is None


class TestProbeDuration:
    def test_reads_ffprobe_output(self):
        run = _Recorder(_result(stdout="3600.25\n"))
        assert probe_duration(Path("a.mp3"), run_fn=run) == pytest.approx(3600.25)
        assert run.calls[0][0] == "ffprobe"
        assert "format=duration" in run.calls[0]

    def test_falls_back_to_decoding_when_the_header_has_no_duration(self):
        # A recording whose ffmpeg was killed mid-write: ffprobe reports
        # nothing, so the real length has to come from a decode pass.
        run = _Recorder(
            _result(returncode=1, stdout=""),
            _result(stderr="time=00:41:00.00 bitrate=N/A"),
        )
        assert probe_duration(Path("a.mp3"), run_fn=run) == pytest.approx(2460.0)
        assert run.calls[1][0] == "ffmpeg"
        assert run.calls[1][-2:] == ["-f", "null"] or "null" in run.calls[1]

    def test_zero_duration_is_treated_as_unknown(self):
        run = _Recorder(_result(stdout="0\n"), _result(stderr=""))
        assert probe_duration(Path("a.mp3"), run_fn=run) is None

    def test_unparseable_output_falls_through_to_none(self):
        run = _Recorder(_result(stdout="N/A\n"), _result(stderr="no time here"))
        assert probe_duration(Path("a.mp3"), run_fn=run) is None

    def test_subprocess_explosion_is_not_fatal(self):
        def boom(cmd, timeout):
            raise FileNotFoundError("ffprobe missing")

        assert probe_duration(Path("a.mp3"), run_fn=boom) is None


class TestDetectSilences:
    def test_parses_the_detector_output(self):
        run = _Recorder(_result(stderr=SILENCE_LOG))
        assert detect_silences(Path("a.mp3"), run_fn=run) == [
            (12.345, 13.001),
            (61.5, 63.25),
        ]

    def test_filter_arguments_are_passed_through(self):
        run = _Recorder(_result(stderr=""))
        detect_silences(Path("a.mp3"), noise_db=-25.0, min_silence=0.8, run_fn=run)
        assert "silencedetect=noise=-25.0dB:d=0.8" in run.calls[0]

    def test_failure_degrades_to_no_silences(self):
        run = _Recorder(_result(returncode=1, stderr="boom"))
        assert detect_silences(Path("a.mp3"), run_fn=run) == []

    def test_exception_degrades_to_no_silences(self):
        def boom(cmd, timeout):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

        assert detect_silences(Path("a.mp3"), run_fn=boom) == []


class TestExtractSegment:
    def test_uses_stream_copy_and_a_duration(self):
        run = _Recorder(_result())
        dest = extract_segment(Path("in.mp3"), Path("out.mp3"), 1185.0, 1230.0, run_fn=run)
        assert dest == Path("out.mp3")
        cmd = run.calls[0]
        assert cmd[0] == "ffmpeg"
        assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
        # -ss before -i (fast input seeking) and -t rather than -to, whose
        # meaning has varied across ffmpeg versions with input seeking.
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-ss") + 1] == "1185.000"
        assert cmd[cmd.index("-t") + 1] == "1230.000"
        assert "-to" not in cmd

    def test_failure_raises_with_the_time_range_and_ffmpeg_output(self):
        run = _Recorder(_result(returncode=1, stderr="line1\nInvalid data found"))
        with pytest.raises(RuntimeError) as excinfo:
            extract_segment(Path("in.mp3"), Path("out.mp3"), 10.0, 20.0, run_fn=run)
        message = str(excinfo.value)
        assert "10.0s-30.0s" in message
        assert "Invalid data found" in message
