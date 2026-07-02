"""
Owns the background-job list and persists it across restarts.

Jobs used to live only in MainWindow memory: quitting (or crashing) while a
transcription ran silently lost the job — the recording stayed on disk but
nothing re-offered it. JobManager persists every change to
``$XDG_STATE_HOME/meeting-recorder/jobs.json`` (atomic write), and on startup
re-offers interrupted work: jobs that were PROCESSING when the app died come
back as ERROR rows ("interrupted") with a Retry button, ERROR jobs are
restored as-is, and DONE jobs are dropped (finished work is not re-shown).

Main-thread only, like all job mutations (see core/task_runner.py) — no
locking needed.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .job import Job, JobStatus

logger = logging.getLogger(__name__)

INTERRUPTED_MSG = "Interrupted — the app exited while this job was running"

_FORMAT_VERSION = 1


def restore_status(persisted: str) -> tuple[JobStatus, str | None] | None:
    """Pure policy: how a persisted job status is restored at startup.

    Returns (status, error_msg) for jobs to re-offer, or None to drop.
    """
    if persisted == JobStatus.PROCESSING.value:
        return (JobStatus.ERROR, INTERRUPTED_MSG)
    if persisted == JobStatus.ERROR.value:
        return (JobStatus.ERROR, None)
    return None  # DONE (or unknown) — nothing to re-offer


def _default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "meeting-recorder"


class JobManager:
    """Job list + jobs.json persistence. All methods are main-thread only."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir or _default_state_dir()
        self._file = self._state_dir / "jobs.json"
        self._jobs: list[Job] = []
        self._next_id = 0

    # ------------------------------------------------------------------

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs)

    def allocate_id(self) -> int:
        """Reserve a job id (used for pending jobs not yet committed)."""
        job_id = self._next_id
        self._next_id += 1
        return job_id

    def create(self, audio_path: Path, transcript_path: Path, notes_path: Path, label: str) -> Job:
        job = Job(
            job_id=self.allocate_id(),
            audio_path=audio_path,
            transcript_path=transcript_path,
            notes_path=notes_path,
            label=label,
        )
        self.add(job)
        return job

    def add(self, job: Job) -> None:
        self._jobs.append(job)
        self._persist()

    def remove(self, job: Job) -> None:
        if job in self._jobs:
            self._jobs.remove(job)
        self._persist()

    def mark_done(self, job: Job) -> None:
        job.status = JobStatus.DONE
        job.error_msg = None
        self._persist()

    def mark_error(self, job: Job, msg: str) -> None:
        job.status = JobStatus.ERROR
        job.error_msg = msg
        self._persist()

    def mark_processing(self, job: Job) -> None:
        job.status = JobStatus.PROCESSING
        job.error_msg = None
        self._persist()

    def persist(self) -> None:
        """Explicit persistence hook (e.g. after path updates from auto-title)."""
        self._persist()

    # ------------------------------------------------------------------

    def load_persisted(self) -> list[Job]:
        """Load jobs.json, restore re-offerable jobs, and return them.

        Interrupted (PROCESSING) jobs come back as ERROR + Retry; ERROR jobs
        are restored as-is; DONE jobs are dropped. Corrupt or missing state
        starts empty — never blocks startup.
        """
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s (%s); starting with no jobs", self._file, exc)
            return []

        restored: list[Job] = []
        for entry in data.get("jobs", []):
            try:
                decision = restore_status(str(entry["status"]))
                if decision is None:
                    continue
                status, forced_msg = decision
                job = Job(
                    job_id=int(entry["job_id"]),
                    audio_path=Path(entry["audio_path"]),
                    transcript_path=Path(entry["transcript_path"]),
                    notes_path=Path(entry["notes_path"]),
                    label=str(entry["label"]),
                    status=status,
                    error_msg=forced_msg or entry.get("error_msg"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed persisted job %r: %s", entry, exc)
                continue
            restored.append(job)

        self._jobs = restored
        self._next_id = max([int(data.get("next_id", 0))] + [j.job_id + 1 for j in restored])
        self._persist()  # drop DONE entries from disk right away
        return list(restored)

    def _persist(self) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _FORMAT_VERSION,
                "next_id": self._next_id,
                "jobs": [
                    {
                        "job_id": j.job_id,
                        "audio_path": str(j.audio_path),
                        "transcript_path": str(j.transcript_path),
                        "notes_path": str(j.notes_path),
                        "label": j.label,
                        "status": j.status.value,
                        "error_msg": j.error_msg,
                    }
                    for j in self._jobs
                    if not j.cancelled
                ],
            }
            tmp = self._file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._file)
        except OSError as exc:
            # Persistence is best-effort; the in-memory queue keeps working.
            logger.warning("Could not persist jobs to %s: %s", self._file, exc)
