import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from meeting_recorder.platform.audio.pipewire import PipeWireBackend
from meeting_recorder.platform.audio.base import CaptureOutputPaths


@pytest.fixture
def backend():
    return PipeWireBackend()


class TestDeviceEnumeration:
    @patch("meeting_recorder.platform.audio.pipewire.subprocess.run")
    def test_get_default_source(self, mock_run, backend):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=" Audio\n   Sources:\n *  46. alsa_input.usb-0  [vol: 1.00]\n\n"
        )
        device = backend.get_default_source()
        assert device is not None

    @patch("meeting_recorder.platform.audio.pipewire.subprocess.run")
    def test_get_default_sink(self, mock_run, backend):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=" Audio\n   Sinks:\n *  47. alsa_output.pci-0  [vol: 0.75]\n\n"
        )
        device = backend.get_default_sink()
        assert device is not None


class TestAvailability:
    @patch("meeting_recorder.platform.audio.pipewire.shutil.which")
    def test_is_available_true(self, mock_which, backend):
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        assert backend.is_available() is True

    @patch("meeting_recorder.platform.audio.pipewire.shutil.which")
    def test_is_available_no_wpctl(self, mock_which, backend):
        mock_which.side_effect = lambda cmd: None if cmd == "wpctl" else f"/usr/bin/{cmd}"
        assert backend.is_available() is False
