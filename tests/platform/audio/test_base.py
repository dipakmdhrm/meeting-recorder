from pathlib import Path
from meeting_recorder.platform.audio.base import (
    AudioDevice,
    CaptureMode,
    CaptureOutputPaths,
    AudioResult,
    AudioBackend,
)
import pytest


def test_audio_device_fields():
    dev = AudioDevice(name="alsa_input.usb", description="USB Mic", is_default=True)
    assert dev.name == "alsa_input.usb"
    assert dev.description == "USB Mic"
    assert dev.is_default is True


def test_capture_mode_values():
    assert CaptureMode.HEADPHONES.value == "headphones"
    assert CaptureMode.SPEAKER.value == "speaker"


def test_capture_output_paths():
    paths = CaptureOutputPaths(mic=Path("/tmp/mic.mp3"), system=Path("/tmp/sys.mp3"))
    assert paths.mic == Path("/tmp/mic.mp3")
    assert paths.system == Path("/tmp/sys.mp3")


def test_capture_output_paths_speaker_mode():
    paths = CaptureOutputPaths(mic=Path("/tmp/mic.mp3"), system=None)
    assert paths.system is None


def test_audio_result():
    result = AudioResult(
        combined=Path("/tmp/recording.mp3"),
        mic=Path("/tmp/mic.mp3"),
        system=Path("/tmp/sys.mp3"),
    )
    assert result.combined == Path("/tmp/recording.mp3")


def test_audio_result_no_separate_tracks():
    result = AudioResult(combined=Path("/tmp/recording.mp3"), mic=None, system=None)
    assert result.mic is None
    assert result.system is None


def test_audio_backend_is_abstract():
    with pytest.raises(TypeError):
        AudioBackend()
