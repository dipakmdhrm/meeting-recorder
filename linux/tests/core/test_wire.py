"""Tests for the daemon<->window snapshot wire (de)serialization."""

from pathlib import Path

from meeting_recorder.core.job import Job, JobStatus
from meeting_recorder.core.wire import (
    job_to_dict,
    snapshot_from_json,
    snapshot_to_json,
)


def _job(job_id=1, status=JobStatus.PROCESSING, error=None):
    return Job(
        job_id=job_id,
        audio_path=Path("/m/2026/July/30/10-00/recording.mp3"),
        transcript_path=Path("/m/2026/July/30/10-00/transcript.md"),
        notes_path=Path("/m/2026/July/30/10-00/notes.md"),
        label="10-00 Standup",
        status=status,
        error_msg=error,
    )


def test_job_to_dict_carries_render_fields():
    d = job_to_dict(_job(status=JobStatus.ERROR, error="boom"), status_text="Transcribing…")
    assert d["job_id"] == 1
    assert d["label"] == "10-00 Standup"
    assert d["status"] == "error"
    assert d["error_msg"] == "boom"
    assert d["audio_dir"] == "/m/2026/July/30/10-00"
    assert d["status_text"] == "Transcribing…"


def test_snapshot_round_trip():
    payload = snapshot_to_json(
        state="recording",
        status="Recording…",
        elapsed=42,
        countdown=0,
        job_dicts=[job_to_dict(_job(1)), job_to_dict(_job(2, status=JobStatus.DONE))],
    )
    snap = snapshot_from_json(payload)
    assert snap.state == "recording"
    assert snap.status == "Recording…"
    assert snap.elapsed == 42
    assert snap.countdown == 0
    assert [j.job_id for j in snap.jobs] == [1, 2]
    assert snap.jobs[0].status is JobStatus.PROCESSING
    assert snap.jobs[1].status is JobStatus.DONE


def test_job_view_exposes_jobs_panel_attributes():
    snap = snapshot_from_json(
        snapshot_to_json("idle", "", 0, 0, [job_to_dict(_job(3, status=JobStatus.ERROR, error="x"))])
    )
    view = snap.jobs[0]
    # Duck-typed surface JobsPanel reads.
    assert view.job_id == 3
    assert view.label == "10-00 Standup"
    assert view.status is JobStatus.ERROR
    assert view.error_msg == "x"
    assert view.audio_dir == "/m/2026/July/30/10-00"


def test_snapshot_from_empty_or_garbage_is_safe():
    for payload in ("", "not json", "{}", "null"):
        snap = snapshot_from_json(payload)
        assert snap.state == "idle"
        assert snap.jobs == []


def test_snapshot_tolerates_missing_keys():
    snap = snapshot_from_json('{"state": "paused"}')
    assert snap.state == "paused"
    assert snap.elapsed == 0
    assert snap.jobs == []
