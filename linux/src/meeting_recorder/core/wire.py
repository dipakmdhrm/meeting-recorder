"""
Serialization of engine state across the daemon/UI process boundary.

The daemon owns the authoritative recording state and job queue; the window
process renders a copy fetched over D-Bus (``GetSnapshot``) and kept fresh by
``SnapshotChanged`` signals. This module is the pure, GTK-free translation
between the daemon's live objects and the JSON payload carried on the wire, so
it is unit-testable on both sides without a display or a bus.

The window renders job rows from ``JobView`` duck-typed objects — they expose
exactly the attributes ``ui/jobs_panel.py`` reads (``job_id``, ``label``,
``status`` as a ``JobStatus``, ``error_msg``) plus ``audio_dir`` for the
"Open Folder" action.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .job import Job, JobStatus


@dataclass
class JobView:
    """Read-only view of a daemon job, as rendered by the window's jobs panel."""

    job_id: int
    label: str
    status: JobStatus
    error_msg: str | None = None
    audio_dir: str = ""


@dataclass
class Snapshot:
    """Everything the window needs to render the recorder view."""

    state: str = "idle"  # idle | recording | paused | countdown
    status: str = ""
    elapsed: int = 0
    countdown: int = 0
    jobs: list[JobView] = field(default_factory=list)


def job_to_dict(job: Job, status_text: str | None = None) -> dict:
    """Serialize a daemon Job into a wire dict.

    ``status_text`` is the transient per-job progress line (e.g. "Transcribing…")
    the daemon tracks separately from the persisted job; it is not stored on Job.
    """
    return {
        "job_id": job.job_id,
        "label": job.label,
        "status": job.status.value,
        "error_msg": job.error_msg,
        "audio_dir": str(job.audio_path.parent) if job.audio_path else "",
        "status_text": status_text or "",
    }


def job_view_from_dict(data: dict) -> JobView:
    """Rebuild a JobView (render model) from a wire dict."""
    return JobView(
        job_id=int(data["job_id"]),
        label=data.get("label", ""),
        status=JobStatus(data.get("status", "processing")),
        error_msg=data.get("error_msg"),
        audio_dir=data.get("audio_dir", ""),
    )


def snapshot_to_json(
    state: str,
    status: str,
    elapsed: int,
    countdown: int,
    job_dicts: list[dict],
) -> str:
    """Serialize a full snapshot to the JSON string carried over D-Bus."""
    return json.dumps(
        {
            "state": state,
            "status": status,
            "elapsed": int(elapsed),
            "countdown": int(countdown),
            "jobs": job_dicts,
        }
    )


def snapshot_from_json(payload: str) -> Snapshot:
    """Parse a snapshot JSON payload into a Snapshot of JobViews.

    Tolerant of missing keys so a schema addition on the daemon side never
    hard-crashes an older window (mirrors the app's other lenient parsers).
    """
    try:
        data = json.loads(payload) if payload else {}
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):  # e.g. JSON "null" or a bare list
        data = {}
    return Snapshot(
        state=data.get("state", "idle"),
        status=data.get("status", ""),
        elapsed=int(data.get("elapsed", 0) or 0),
        countdown=int(data.get("countdown", 0) or 0),
        jobs=[job_view_from_dict(j) for j in data.get("jobs", [])],
    )
