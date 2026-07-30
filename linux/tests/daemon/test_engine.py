"""
Headless tests for the daemon Engine.

The RecordingController is replaced with a fake (so no recorder/GLib), the
TaskRunner with a recording stub (so pipeline submission is observed, not run),
and JobManager with a temp-dir instance. This covers the snapshot/job logic
lifted out of MainWindow — state naming, job-status-text tracking, the job-row
actions, and the API-key / duplicate guards.
"""

import json
from pathlib import Path

import pytest

from meeting_recorder.core.job_manager import JobManager
from meeting_recorder.core.state_machine import State
from meeting_recorder.daemon.engine import Engine


class FakeController:
    def __init__(self, **callbacks):
        self.cb = callbacks
        self.state = State.IDLE
        self.calls = []
        self.started_with = None

    def start(self, cfg, mode, title):
        self.started_with = (mode, title)
        self.state = State.RECORDING
        self.cb["on_state"](State.RECORDING, "Recording…")

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def stop(self, countdown_enabled):
        self.calls.append(("stop", countdown_enabled))

    def cancel_countdown(self):
        self.calls.append("cancel_countdown")

    def cancel_and_save(self):
        self.calls.append("cancel_and_save")

    def cancel_and_discard(self):
        self.calls.append("cancel_and_discard")


class RecordingRunner:
    """Records submissions without executing the worker (no real pipeline)."""

    def __init__(self):
        self.submissions = []

    def submit(self, fn, *args, on_done=None, on_error=None, description=""):
        self.submissions.append(description)


class FakeProcessorHandle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeLauncher:
    """Captures processor launches (and callbacks) without spawning a child."""

    def __init__(self):
        self.launches = []

    def launch(self, audio, transcript, notes, *, on_status, on_done, on_error):
        handle = FakeProcessorHandle()
        self.launches.append(
            {
                "audio": audio,
                "on_status": on_status,
                "on_done": on_done,
                "on_error": on_error,
                "handle": handle,
            }
        )
        return handle


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    changes = {"n": 0}
    errors = []
    outputs = []
    ctrl_holder = {}
    launcher = FakeLauncher()

    def factory(**cb):
        ctrl_holder["ctrl"] = FakeController(**cb)
        return ctrl_holder["ctrl"]

    eng = Engine(
        RecordingRunner(),
        on_change=lambda: changes.__setitem__("n", changes["n"] + 1),
        on_error=errors.append,
        on_output=outputs.append,
        job_manager=JobManager(),
        controller_factory=factory,
        processor_launcher=launcher,
    )
    eng._test = {
        "changes": changes,
        "errors": errors,
        "outputs": outputs,
        "ctrl": ctrl_holder,
        "launcher": launcher,
    }
    return eng


def _paths(tmp, name="10-00"):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "recording.mp3"), str(d / "transcript.md"), str(d / "notes.md")


def test_state_name_defaults_idle(engine):
    assert engine.state_name() == "idle"
    snap = json.loads(engine.snapshot_json())
    assert snap["state"] == "idle"
    assert snap["jobs"] == []


def test_import_existing_creates_job_and_launches_processor(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "my recording")
    snap = json.loads(engine.snapshot_json())
    assert len(snap["jobs"]) == 1
    assert snap["jobs"][0]["label"] == "my recording"
    # A processing child was launched (in-daemon threads no longer run the SDK).
    launches = engine._test["launcher"].launches
    assert len(launches) == 1
    assert launches[0]["audio"] == a


def test_processing_done_marks_job_done_and_adopts_paths(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = engine._job_manager.jobs[0].job_id
    # Simulate the child finishing with auto-title-renamed paths.
    renamed = str(tmp_path / "10-00_Retro" / "recording.mp3")
    engine._test["launcher"].launches[0]["on_done"]([renamed, t, n])
    from meeting_recorder.core.job import JobStatus

    job = engine._job_manager.jobs[0]
    assert job.status is JobStatus.DONE
    assert str(job.audio_path) == renamed
    assert job_id not in engine._processors


def test_cancel_job_kills_running_processor(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job = engine._job_manager.jobs[0]
    handle = engine._test["launcher"].launches[0]["handle"]
    engine.cancel_job(job.job_id)
    assert handle.cancelled is True
    assert job.job_id not in engine._processors
    assert engine._job_manager.jobs == []


def test_processing_error_marks_job_error(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    engine._test["launcher"].launches[0]["on_error"]("bad key")
    from meeting_recorder.core.job import JobStatus

    job = engine._job_manager.jobs[0]
    assert job.status is JobStatus.ERROR
    assert (job.error_msg or "") == "bad key"


def test_status_text_appears_in_snapshot(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = json.loads(engine.snapshot_json())["jobs"][0]["job_id"]
    engine._set_job_status_text(job_id, "Transcribing…")
    snap = json.loads(engine.snapshot_json())
    assert snap["jobs"][0]["status_text"] == "Transcribing…"


def test_dismiss_removes_job_and_status_text(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = json.loads(engine.snapshot_json())["jobs"][0]["job_id"]
    engine._set_job_status_text(job_id, "x")
    engine.dismiss_job(job_id)
    assert json.loads(engine.snapshot_json())["jobs"] == []
    assert job_id not in engine._job_status_text


def test_cancel_job_marks_cancelled_and_removes(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job = engine._job_manager.jobs[0]
    engine.cancel_job(job.job_id)
    assert job.cancelled is True
    assert job.token.cancelled is True
    assert engine._job_manager.jobs == []


def test_summarize_duplicate_guard(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    assert engine.summarize_meeting(a, t, n, "m") is None
    # Same audio still processing → rejected.
    assert engine.summarize_meeting(a, t, n, "m") == "This meeting is already being processed."


def test_job_folder_returns_parent(engine, tmp_path):
    a, t, n = _paths(tmp_path)
    engine.import_existing(a, t, n, "r")
    job_id = engine._job_manager.jobs[0].job_id
    assert engine.job_folder(job_id) == str(Path(a).parent)
    assert engine.job_folder(9999) is None


def test_start_recording_blocks_without_api_key(engine, monkeypatch):
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.load", lambda: {"output_folder": "~/m"}
    )
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.api_key_error", lambda cfg: "No API key set."
    )
    engine.start_recording("headphones")
    assert engine._test["errors"] == ["No API key set."]
    assert engine._test["ctrl"]["ctrl"].started_with is None


def test_start_recording_starts_with_key(engine, monkeypatch):
    monkeypatch.setattr(
        "meeting_recorder.daemon.engine.settings.load", lambda: {"output_folder": "~/m"}
    )
    monkeypatch.setattr("meeting_recorder.daemon.engine.settings.api_key_error", lambda cfg: None)
    engine.set_title("Weekly sync")
    engine.start_recording("speaker")
    assert engine._test["ctrl"]["ctrl"].started_with == ("speaker", "Weekly sync")
