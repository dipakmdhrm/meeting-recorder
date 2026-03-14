import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from meeting_recorder.platform.screen.gpu_screen_recorder import GpuScreenRecorder
from meeting_recorder.platform.screen.base import MonitorInfo


@pytest.fixture
def recorder():
    return GpuScreenRecorder()


class TestMonitorDetection:
    @patch("meeting_recorder.platform.screen.gpu_screen_recorder.subprocess.run")
    def test_list_monitors_via_gpu_screen_recorder(self, mock_run, recorder):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="eDP-1|1920x1080\nHDMI-A-1|1920x1080\n"
        )
        monitors = recorder.list_monitors()
        assert len(monitors) == 2
        assert monitors[0].name == "eDP-1"
        assert monitors[0].resolution == "1920x1080"
        assert monitors[1].name == "HDMI-A-1"

    @patch("meeting_recorder.platform.screen.gpu_screen_recorder.subprocess.run")
    def test_list_monitors_fallback_kscreen_doctor(self, mock_run, recorder):
        mock_run.side_effect = [
            FileNotFoundError("gpu-screen-recorder not found"),
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="Output: 1 DP-1\n  Enabled\n  Resolution: 2560x1440\nOutput: 2 HDMI-A-1\n  Enabled\n"
            ),
        ]
        monitors = recorder.list_monitors()
        assert len(monitors) >= 1


class TestRecording:
    @patch("meeting_recorder.platform.screen.gpu_screen_recorder.subprocess.Popen")
    def test_start_spawns_per_monitor(self, mock_popen, recorder, tmp_path):
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="gpu-screen-recorder", timeout=1.5)
        mock_popen.return_value = mock_proc
        recorder.start(["DP-1", "HDMI-A-1"], tmp_path, fps=30)
        assert mock_popen.call_count == 2

    @patch("meeting_recorder.platform.screen.gpu_screen_recorder.subprocess.Popen")
    def test_stop_sends_sigint(self, mock_popen, recorder, tmp_path):
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="gpu-screen-recorder", timeout=1.5)
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        recorder.start(["DP-1"], tmp_path, fps=30)
        recorder.stop()
        mock_proc.send_signal.assert_called()


class TestAvailability:
    @patch("meeting_recorder.platform.screen.gpu_screen_recorder.shutil.which")
    def test_is_available(self, mock_which, recorder):
        mock_which.return_value = "/usr/bin/gpu-screen-recorder"
        assert recorder.is_available() is True

    @patch("meeting_recorder.platform.screen.gpu_screen_recorder.shutil.which")
    def test_not_available(self, mock_which, recorder):
        mock_which.return_value = None
        assert recorder.is_available() is False
