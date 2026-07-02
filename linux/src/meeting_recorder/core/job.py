"""
Background-job model for the AI processing queue.

A Job is created when a recording stops (or an existing file is imported) and
tracks one pipeline run. Job objects are mutated **only on the main thread**
(TaskRunner callbacks); workers just read them — that convention is what makes
them race-free without locks.

`actions_for_status()` is the pure policy for which buttons a job row offers,
extracted so it is unit-testable without GTK (same pattern as the tray's
`build_menu_model`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .task_runner import CancelToken


class JobStatus(Enum):
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    job_id: int
    audio_path: Path
    transcript_path: Path
    notes_path: Path
    label: str
    status: JobStatus = JobStatus.PROCESSING
    error_msg: str | None = None
    cancelled: bool = False
    # Cooperative cancellation for the pipeline worker: checked between
    # stages so a cancelled job stops instead of burning API quota.
    token: CancelToken = field(default_factory=CancelToken)


def actions_for_status(status: JobStatus) -> tuple[str, ...]:
    """Which action buttons a job row shows for *status*.

    Returns identifiers, not widgets, so the policy is testable headless:
    "cancel" | "open_folder" | "retry" | "dismiss".
    """
    if status is JobStatus.PROCESSING:
        return ("cancel",)
    if status is JobStatus.DONE:
        return ("open_folder", "dismiss")
    return ("retry", "dismiss")
