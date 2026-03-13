from pathlib import Path
from meeting_recorder.platform.screen.base import ScreenRecorder, MonitorInfo
from meeting_recorder.platform.screen.none import NoOpScreenRecorder
import pytest


def test_monitor_info():
    m = MonitorInfo(name="DP-1", resolution="2560x1440", position="0x0")
    assert m.name == "DP-1"


def test_screen_recorder_is_abstract():
    with pytest.raises(TypeError):
        ScreenRecorder()


def test_noop_list_monitors():
    rec = NoOpScreenRecorder()
    assert rec.list_monitors() == []


def test_noop_start_does_nothing():
    rec = NoOpScreenRecorder()
    rec.start(["DP-1"], Path("/tmp"), 30)


def test_noop_stop_returns_empty():
    rec = NoOpScreenRecorder()
    assert rec.stop() == []


def test_noop_is_available():
    rec = NoOpScreenRecorder()
    assert rec.is_available() is True
