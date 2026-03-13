from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from meeting_recorder.platform.audio.base import (
    AudioBackend, AudioDevice, CaptureOutputPaths, AudioResult, CaptureMode,
)
from meeting_recorder.audio.recorder import Recorder


@pytest.fixture
def mock_backend():
    backend = MagicMock(spec=AudioBackend)
    backend.get_default_source.return_value = AudioDevice(
        name="test-mic", description="Test Mic", is_default=True
    )
    backend.get_default_sink.return_value = AudioDevice(
        name="test-sink", description="Test Sink", is_default=True
    )
    backend.validate.return_value = (True, "")
    return backend


def test_recorder_accepts_backend(mock_backend, tmp_path):
    rec = Recorder(
        backend=mock_backend,
        output_dir=tmp_path,
        mode=CaptureMode.HEADPHONES,
        quality="2",
        separate_tracks=True,
        on_tick=lambda elapsed: None,
        on_error=lambda msg: None,
    )
    assert rec._backend is mock_backend


def test_recorder_start_calls_backend(mock_backend, tmp_path):
    rec = Recorder(
        backend=mock_backend,
        output_dir=tmp_path,
        mode=CaptureMode.HEADPHONES,
        quality="2",
        separate_tracks=True,
    )
    rec.start()
    mock_backend.start_capture.assert_called_once()
    call_args = mock_backend.start_capture.call_args
    paths = call_args[0][0]
    assert isinstance(paths, CaptureOutputPaths)
    assert paths.mic is not None
    assert paths.system is not None
    # Clean up timer thread
    rec._stop_event.set()


def test_recorder_speaker_mode_no_system_path(mock_backend, tmp_path):
    rec = Recorder(
        backend=mock_backend,
        output_dir=tmp_path,
        mode=CaptureMode.SPEAKER,
        quality="2",
        separate_tracks=True,
    )
    rec.start()
    call_args = mock_backend.start_capture.call_args
    paths = call_args[0][0]
    assert paths.system is None
    rec._stop_event.set()


def test_recorder_stop_returns_audio_result(mock_backend, tmp_path):
    rec = Recorder(
        backend=mock_backend,
        output_dir=tmp_path,
        mode=CaptureMode.HEADPHONES,
        quality="2",
        separate_tracks=True,
    )
    rec.start()

    # Create dummy segment files so stop doesn't fail
    seg_mic = tmp_path / "recording_mic_seg000.mp3"
    seg_mic.write_bytes(b"fake mp3 data")
    seg_sys = tmp_path / "recording_system_seg000.mp3"
    seg_sys.write_bytes(b"fake mp3 data")

    with patch("meeting_recorder.audio.recorder.subprocess.run"):
        result = rec.stop()

    mock_backend.stop_capture.assert_called()
    assert isinstance(result, AudioResult)
