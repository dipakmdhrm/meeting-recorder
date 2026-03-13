import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from meeting_recorder.platform.audio.pulseaudio import PulseAudioBackend
from meeting_recorder.platform.audio.base import CaptureOutputPaths


@pytest.fixture
def backend():
    return PulseAudioBackend()


class TestDeviceEnumeration:
    @patch("meeting_recorder.platform.audio.pulseaudio._run_pactl")
    def test_get_default_source(self, mock_pactl, backend):
        mock_pactl.return_value = "alsa_input.usb-0"
        device = backend.get_default_source()
        assert device is not None
        assert device.name == "alsa_input.usb-0"
        mock_pactl.assert_called_with("get-default-source")

    @patch("meeting_recorder.platform.audio.pulseaudio._run_pactl")
    def test_get_default_source_failure(self, mock_pactl, backend):
        mock_pactl.side_effect = subprocess.CalledProcessError(1, "pactl")
        device = backend.get_default_source()
        assert device is None

    @patch("meeting_recorder.platform.audio.pulseaudio._run_pactl")
    def test_get_default_sink(self, mock_pactl, backend):
        mock_pactl.return_value = "alsa_output.pci-0"
        device = backend.get_default_sink()
        assert device is not None
        assert device.name == "alsa_output.pci-0"


class TestCaptureCommands:
    @patch("meeting_recorder.platform.audio.pulseaudio.subprocess.Popen")
    @patch("meeting_recorder.platform.audio.pulseaudio._run_pactl")
    def test_start_capture_headphones(self, mock_pactl, mock_popen, backend):
        mock_pactl.side_effect = ["alsa_input.usb", "alsa_output.pci"]
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        paths = CaptureOutputPaths(
            mic=Path("/tmp/mic.mp3"),
            system=Path("/tmp/sys.mp3"),
        )
        backend.start_capture(paths, quality="2")
        assert mock_popen.call_count == 2

    @patch("meeting_recorder.platform.audio.pulseaudio.subprocess.Popen")
    @patch("meeting_recorder.platform.audio.pulseaudio._run_pactl")
    def test_start_capture_mic_only(self, mock_pactl, mock_popen, backend):
        mock_pactl.return_value = "alsa_input.usb"
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        paths = CaptureOutputPaths(mic=Path("/tmp/mic.mp3"), system=None)
        backend.start_capture(paths, quality="2")
        assert mock_popen.call_count == 1


class TestAvailability:
    @patch("meeting_recorder.platform.audio.pulseaudio.shutil.which")
    def test_is_available_true(self, mock_which, backend):
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        assert backend.is_available() is True

    @patch("meeting_recorder.platform.audio.pulseaudio.shutil.which")
    def test_is_available_no_pactl(self, mock_which, backend):
        mock_which.side_effect = lambda cmd: None if cmd == "pactl" else f"/usr/bin/{cmd}"
        assert backend.is_available() is False
