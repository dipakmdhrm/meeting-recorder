"""
The GTK-free recording engine that lives in the daemon.

This is the recording/job/pipeline logic lifted out of ``ui/main_window.py`` so
it can run without a display. It owns the ``RecordingController`` and
``JobManager`` and runs the AI pipeline through the app-wide ``TaskRunner``,
exactly as the window used to — but instead of touching GTK widgets it keeps a
plain snapshot (state, status, timer, jobs) and fires ``on_change`` whenever it
mutates. The daemon wires that callback to the tray and to the D-Bus service, so
the tray and the (separate) window process both render the same state.

Threading contract is unchanged from the window: public methods are main-thread
only; ``on_tick``/``on_recorder_error`` arrive on recorder worker threads and are
marshalled back with ``idle_call``; pipeline workers only read jobs, all job
mutation happens in main-thread callbacks.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from ..config import settings
from ..core.job import Job, JobStatus
from ..core.job_manager import JobManager
from ..core.recording_controller import PendingRecording, RecordingController
from ..core.state_machine import State
from ..core.task_runner import CancelToken, TaskRunner
from ..core.wire import job_to_dict, snapshot_to_json
from ..utils.glib_bridge import idle_call

logger = logging.getLogger(__name__)

_STATE_NAMES = {
    State.IDLE: "idle",
    State.RECORDING: "recording",
    State.PAUSED: "paused",
    State.COUNTDOWN: "idle",  # tray shows idle art during the stop countdown
}


class Engine:
    """Headless owner of the recording lifecycle and processing queue."""

    def __init__(
        self,
        runner: TaskRunner,
        *,
        on_change: Callable[[], None],
        on_error: Callable[[str], None],
        on_output: Callable[[str], None],
        job_manager: JobManager | None = None,
        controller_factory: Callable[..., object] | None = None,
    ) -> None:
        self._runner = runner
        self._on_change = on_change
        self._on_error_cb = on_error
        self._on_output = on_output

        self._status = "Ready to record"
        self._elapsed = 0
        self._countdown = 0
        self._job_status_text: dict[int, str] = {}

        # controller_factory / job_manager are injectable so the engine is
        # unit-testable headless (fake controller + runner + temp JobManager),
        # mirroring the RecordingController test harness.
        factory = controller_factory or (lambda **cb: RecordingController(runner, **cb))
        self._controller = factory(
            on_state=self._on_state,
            on_error=self._emit_error,
            on_commit=self._on_recording_committed,
            on_saved=self._on_recording_saved,
            on_discarded=lambda: None,
            on_countdown=self._on_countdown_tick,
            on_timer=self._on_tick,
            on_recorder_error=self._on_recording_error,
        )
        self._job_manager = job_manager or JobManager()

    # ------------------------------------------------------------------
    # Snapshot / query surface (read by the tray and the D-Bus service)
    # ------------------------------------------------------------------

    @property
    def state(self) -> State:
        return self._controller.state

    def state_name(self) -> str:
        return _STATE_NAMES.get(self._controller.state, "idle")

    def processing_jobs(self) -> list[Job]:
        return [
            j
            for j in self._job_manager.jobs
            if j.status is JobStatus.PROCESSING and not j.cancelled
        ]

    def snapshot_json(self) -> str:
        state = self._controller.state
        wire_state = "countdown" if state == State.COUNTDOWN else _STATE_NAMES.get(state, "idle")
        job_dicts = [
            job_to_dict(j, self._job_status_text.get(j.job_id)) for j in self._job_manager.jobs
        ]
        return snapshot_to_json(
            wire_state, self._status, self._elapsed, self._countdown, job_dicts
        )

    def restore_persisted_jobs(self) -> None:
        """Re-offer jobs from the previous session (crash/quit recovery)."""
        for _ in self._job_manager.load_persisted():
            pass
        self._changed()

    # ------------------------------------------------------------------
    # Recording commands (main-thread only)
    # ------------------------------------------------------------------

    def start_recording(self, mode: str) -> None:
        if self._controller.state != State.IDLE:
            return
        cfg = settings.load()
        key_missing = settings.api_key_error(cfg)
        if key_missing:
            self._emit_error(key_missing)
            return
        self._controller.start(cfg, mode, self._pending_title)

    # The window sends the meeting title with the start command; keep the last
    # one so a tray-initiated start still records without a title.
    _pending_title: str | None = None

    def set_title(self, title: str | None) -> None:
        self._pending_title = title or None

    def pause(self) -> None:
        self._controller.pause()

    def resume(self) -> None:
        self._controller.resume()

    def stop(self) -> None:
        cfg = settings.load()
        self._controller.stop(cfg.get("processing_countdown_enabled", False))

    def cancel_countdown(self) -> None:
        self._controller.cancel_countdown()

    def cancel_and_save(self) -> None:
        self._controller.cancel_and_save()

    def cancel_and_discard(self) -> None:
        self._controller.cancel_and_discard()

    def prepare_quit(self) -> None:
        """Stop any active recording (keeping audio) before the daemon exits."""
        self._controller.cancel_and_save()

    # ------------------------------------------------------------------
    # Job creation from the window (paths already resolved UI-side)
    # ------------------------------------------------------------------

    def import_existing(
        self, audio_path: str, transcript_path: str, notes_path: str, label: str
    ) -> None:
        """Process an existing audio file already resolved to meeting paths."""
        job = self._job_manager.create(
            audio_path=Path(audio_path),
            transcript_path=Path(transcript_path),
            notes_path=Path(notes_path),
            label=label,
        )
        self._changed()
        self._submit_pipeline_job(job)

    def summarize_meeting(
        self, audio_path: str, transcript_path: str, notes_path: str, label: str
    ) -> str | None:
        """Process a library meeting; returns an error string or None on success."""
        audio = Path(audio_path)
        if any(
            j.audio_path == audio and j.status is JobStatus.PROCESSING
            for j in self._job_manager.jobs
        ):
            return "This meeting is already being processed."
        job = self._job_manager.create(
            audio_path=audio,
            transcript_path=Path(transcript_path),
            notes_path=Path(notes_path),
            label=label,
        )
        self._changed()
        self._submit_pipeline_job(job)
        return None

    # ------------------------------------------------------------------
    # Job row actions
    # ------------------------------------------------------------------

    def cancel_job(self, job_id: int) -> None:
        job = self._find_job(job_id)
        if not job:
            return
        job.cancelled = True
        job.token.cancel()
        self.dismiss_job(job_id)
        logger.info("Job %d cancelled by user", job_id)

    def retry_job(self, job_id: int) -> None:
        job = self._find_job(job_id)
        if not job:
            return
        job.cancelled = False
        job.token = CancelToken()
        self._job_manager.mark_processing(job)
        self._changed()
        self._submit_pipeline_job(job)

    def dismiss_job(self, job_id: int) -> None:
        job = self._find_job(job_id)
        if not job:
            return
        self._job_manager.remove(job)
        self._job_status_text.pop(job_id, None)
        self._changed()

    def job_folder(self, job_id: int) -> str | None:
        job = self._find_job(job_id)
        if job and job.audio_path:
            return str(job.audio_path.parent)
        return None

    # ------------------------------------------------------------------
    # Controller callbacks (main-thread)
    # ------------------------------------------------------------------

    def _on_state(self, state: State, status: str) -> None:
        self._status = status or self._status
        if state == State.IDLE:
            self._elapsed = 0
            self._countdown = 0
        self._changed()

    def _on_recording_committed(self, pending: PendingRecording) -> None:
        job = self._job_manager.create(
            audio_path=pending.audio_path,
            transcript_path=pending.transcript_path,
            notes_path=pending.notes_path,
            label=pending.label,
        )
        self._changed()
        self._submit_recorded_job(job)

    def _on_recording_saved(self, pending: PendingRecording) -> None:
        paths = []
        if pending.transcript_path and pending.transcript_path.exists():
            paths.append(f"Transcript: {pending.transcript_path}")
        if pending.notes_path and pending.notes_path.exists():
            paths.append(f"Notes: {pending.notes_path}")
        if pending.audio_path and pending.audio_path.exists():
            paths.append(f"Audio: {pending.audio_path}")
        if paths:
            self._on_output("\n".join(paths))

    def _on_countdown_tick(self, remaining: int) -> None:
        self._countdown = remaining
        self._status = f"Starting transcription in {remaining}s…"
        self._changed()

    # --- recorder worker-thread callbacks (marshalled onto main thread) ---

    def _on_tick(self, elapsed: int) -> None:
        idle_call(self._update_timer, elapsed)

    def _update_timer(self, elapsed: int) -> None:
        self._elapsed = elapsed
        self._changed()

    def _on_recording_error(self, msg: str) -> None:
        idle_call(self._controller.abort_to_idle)
        idle_call(self._emit_error, msg)

    # ------------------------------------------------------------------
    # Pipeline (unchanged logic, lifted from MainWindow)
    # ------------------------------------------------------------------

    def _submit_pipeline_job(self, job: Job) -> None:
        self._runner.submit(
            self._pipeline_worker,
            job,
            on_done=lambda paths, j=job: self._on_pipeline_finished(j, paths),
            on_error=lambda exc, j=job: self._on_pipeline_failed(j, exc),
            description=f"pipeline: {job.label}",
        )

    def _submit_recorded_job(self, job: Job) -> None:
        self._runner.submit(
            self._recorded_job_worker,
            job,
            on_done=lambda paths, j=job: self._on_pipeline_finished(j, paths),
            on_error=lambda exc, j=job: self._on_pipeline_failed(j, exc),
            description=f"pipeline (after stop): {job.label}",
        )

    def _recorded_job_worker(self, job: Job):
        self._controller.wait_until_stopped()
        if job.cancelled:
            return None
        idle_call(self._set_job_status_text, job.job_id, "Transcribing…")
        return self._pipeline_worker(job)

    def _pipeline_worker(self, job: Job):
        from ..processing.pipeline import Pipeline, PipelineCancelled

        cfg = settings.load()
        pipeline = Pipeline(
            config=cfg,
            audio_path=job.audio_path,
            transcript_path=job.transcript_path,
            notes_path=job.notes_path,
            on_status=lambda msg: (
                idle_call(self._set_job_status_text, job.job_id, msg)
                if not job.cancelled
                else None
            ),
        )
        try:
            pipeline.run(cancel_token=job.token)
        except PipelineCancelled:
            logger.info("Pipeline for job %d stopped after cancellation", job.job_id)
            return None
        return pipeline.output_paths

    def _on_pipeline_finished(self, job: Job, paths) -> None:
        if job.cancelled or paths is None:
            return
        audio_path, transcript_path, notes_path = paths
        if audio_path:
            job.audio_path = audio_path
        if transcript_path:
            job.transcript_path = transcript_path
        if notes_path:
            job.notes_path = notes_path
        self._job_manager.persist()  # auto-title may have moved the paths
        self._job_manager.mark_done(job)
        self._job_status_text.pop(job.job_id, None)
        self._changed()
        self._notify_complete(job)

    def _on_pipeline_failed(self, job: Job, exc: Exception) -> None:
        if job.cancelled:
            return
        self._job_manager.mark_error(job, str(exc))
        self._changed()

    def _set_job_status_text(self, job_id: int, msg: str) -> None:
        self._job_status_text[job_id] = msg
        self._changed()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_job(self, job_id: int) -> Job | None:
        for j in self._job_manager.jobs:
            if j.job_id == job_id:
                return j
        return None

    def _notify_complete(self, job: Job) -> None:
        from ..ui.notifications import notify

        body_parts = []
        if job.transcript_path:
            body_parts.append(str(job.transcript_path))
        if job.notes_path:
            body_parts.append(str(job.notes_path))
        notify(
            summary="Meeting Recorded",
            body="\n".join(body_parts) if body_parts else "Processing complete.",
        )

    def _emit_error(self, msg: str) -> None:
        logger.error("Engine error: %s", msg)
        self._on_error_cb(msg)

    def _changed(self) -> None:
        try:
            self._on_change()
        except Exception:  # never let a listener break the engine
            logger.exception("Engine on_change listener failed")

    @staticmethod
    def output_folder() -> str:
        cfg = settings.load()
        return os.path.expanduser(cfg.get("output_folder", "~/meetings"))
