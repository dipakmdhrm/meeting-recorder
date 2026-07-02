"""
Tests for RecordingController — the full recording lifecycle without GTK.

Recorder, device validation, the countdown scheduler, and the TaskRunner's
main-thread scheduler are all injected fakes, so start/pause/stop/countdown/
cancel flows run synchronously and deterministically.
"""

import time
from pathlib import Path

from meeting_recorder.core.recording_controller import (
    PendingRecording,
    RecordingController,
    make_job_label,
)
from meeting_recorder.core.state_machine import State
from meeting_recorder.core.task_runner import TaskRunner


class FakeRecorder:
    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.started = False
        self.paused = False
        self.stopped = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("no audio device")
        self.started = True

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True


class ManualTicker:
    """Captures the countdown callback so tests can pump ticks by hand."""

    def __init__(self):
        self.callback = None

    def __call__(self, _interval_ms, callback):
        self.callback = callback

    def pump(self):
        """Fire one tick; clears the callback when it asks to stop."""
        assert self.callback is not None
        if not self.callback():
            self.callback = None


class Harness:
    def __init__(self, *, devices_ok=True, fail_start=False, tmp_path=Path("/tmp/x")):
        self.recorder = FakeRecorder(fail_start=fail_start)
        self.ticker = ManualTicker()
        self.states: list[tuple[State, str]] = []
        self.errors: list[str] = []
        self.commits: list[PendingRecording] = []
        self.saved: list[PendingRecording] = []
        self.discarded: list[bool] = []
        self.countdowns: list[int] = []
        self.tmp_path = tmp_path

        self.controller = RecordingController(
            TaskRunner(schedule_on_main=lambda cb: cb()),
            on_state=lambda s, m: self.states.append((s, m)),
            on_error=self.errors.append,
            on_commit=self.commits.append,
            on_saved=self.saved.append,
            on_discarded=lambda: self.discarded.append(True),
            on_countdown=self.countdowns.append,
            on_timer=lambda _s: None,
            on_recorder_error=lambda _m: None,
            recorder_factory=lambda **kw: self.recorder,
            validate_devices_fn=lambda: (devices_ok, "" if devices_ok else "no mic"),
            schedule_tick=self.ticker,
        )

    def cfg(self):
        return {"output_folder": str(self.tmp_path), "recording_quality": "high"}

    def start(self, mode="headphones", title=None):
        self.controller.start(self.cfg(), mode, title)

    def wait_recorder_stopped(self, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not self.recorder.stopped and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.recorder.stopped


class TestMakeJobLabel:
    def test_time_part_only(self):
        assert make_job_label(Path("/m/2026/July/02/10-30/rec.mp3"), None) == "10-30"

    def test_time_part_with_title(self):
        assert make_job_label(Path("/m/10-30/rec.mp3"), "standup") == "10-30 standup"


class TestStart:
    def test_happy_path_reaches_recording(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        assert h.recorder.started
        assert h.controller.state is State.RECORDING
        assert h.states[-1][0] is State.RECORDING
        assert h.errors == []

    def test_device_failure_stays_idle(self, tmp_path):
        h = Harness(devices_ok=False, tmp_path=tmp_path)
        h.start()
        assert h.controller.state is State.IDLE
        assert any("Audio device error" in e for e in h.errors)
        assert not h.recorder.started

    def test_recorder_start_failure_reports_and_stays_idle(self, tmp_path):
        h = Harness(fail_start=True, tmp_path=tmp_path)
        h.start()
        assert h.controller.state is State.IDLE
        assert any("no audio device" in e for e in h.errors)

    def test_start_ignored_while_recording(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        states_before = len(h.states)
        h.start()  # second click
        assert len(h.states) == states_before


class TestPauseResume:
    def test_pause_and_resume(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        h.controller.pause()
        assert h.controller.state is State.PAUSED
        assert h.recorder.paused
        h.controller.resume()
        assert h.controller.state is State.RECORDING
        assert not h.recorder.paused

    def test_pause_ignored_when_idle(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.controller.pause()
        assert h.controller.state is State.IDLE


class TestStopWithoutCountdown:
    def test_commits_immediately_and_stops_recorder(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start(title="standup")
        h.controller.stop(countdown_enabled=False)

        assert h.controller.state is State.IDLE
        assert len(h.commits) == 1
        assert h.commits[0].label.endswith("standup")
        h.wait_recorder_stopped()

    def test_wait_until_stopped_returns_after_stop(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        h.controller.stop(countdown_enabled=False)
        h.controller.wait_until_stopped(timeout=2)
        assert h.recorder.stopped


class TestCountdown:
    def test_counts_down_then_commits(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        h.controller.stop(countdown_enabled=True)
        assert h.controller.state is State.COUNTDOWN
        assert h.commits == []

        for _ in range(4):  # 5→4→3→2→1 ticks; commit on the 5th
            h.ticker.pump()
        assert h.commits == []
        assert h.countdowns == [4, 3, 2, 1]

        h.ticker.pump()
        assert len(h.commits) == 1
        assert h.controller.state is State.IDLE

    def test_cancel_countdown_discards_pending(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        h.controller.stop(countdown_enabled=True)
        h.ticker.pump()
        h.controller.cancel_countdown()

        assert h.controller.state is State.IDLE
        # a straggler tick fires but must not commit
        if h.ticker.callback:
            h.ticker.pump()
        assert h.commits == []


class TestCancelFlows:
    def test_cancel_and_save_reports_saved_recording(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start(title="keepme")
        h.controller.cancel_and_save()
        h.wait_recorder_stopped()

        deadline = time.monotonic() + 2
        while not h.saved and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(h.saved) == 1
        assert h.saved[0].label.endswith("keepme")
        assert h.commits == []
        assert h.controller.state is State.IDLE

    def test_cancel_and_discard_deletes_audio(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        # simulate the recorder having written audio
        pending = h.controller._pending
        pending.audio_path.parent.mkdir(parents=True, exist_ok=True)
        pending.audio_path.write_bytes(b"audio")

        h.controller.cancel_and_discard()
        deadline = time.monotonic() + 2
        while not h.discarded and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not pending.audio_path.exists()
        assert h.commits == []


class TestAbort:
    def test_abort_to_idle_resets_lifecycle(self, tmp_path):
        h = Harness(tmp_path=tmp_path)
        h.start()
        h.controller.abort_to_idle()
        assert h.controller.state is State.IDLE
        # a fresh start must work again
        h.recorder.started = False
        h.start()
        assert h.recorder.started
