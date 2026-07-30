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

logger = logging.getLogger(__name__)


def idle_call(func, *args):
    """Marshal onto the GLib main loop. Imported lazily so the engine module is
    importable without PyGObject (the headless test env has no gi)."""
    from ..utils.glib_bridge import idle_call as _idle_call

    _idle_call(func, *args)


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
        processor_launcher: object | None = None,
    ) -> None:
        self._runner = runner
        self._on_change = on_change
        self._on_error_cb = on_error
        self._on_output = on_output

        self._status = "Ready to record"
        self._elapsed = 0
        self._countdown = 0
        self._job_status_text: dict[int, str] = {}
        # Each processing job runs in a short-lived child process so the heavy
        # Gemini/Whisper stack never accumulates in the daemon. Tracked by
        # job_id so a cancel can kill the child. Launcher is injectable for tests.
        self._processor_launcher = processor_launcher
        self._processors: dict[int, object] = {}

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
        return snapshot_to_json(wire_state, self._status, self._elapsed, self._countdown, job_dicts)

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
        # Kill the processing child if one is running for this job.
        handle = self._processors.pop(job_id, None)
        if handle is not None:
            handle.cancel()
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
    # Pipeline — runs in a short-lived child process (memory isolation)
    # ------------------------------------------------------------------

    def _submit_pipeline_job(self, job: Job) -> None:
        """Launch processing for a job whose audio is already on disk."""
        self._launch_processor(job)

    def _submit_recorded_job(self, job: Job) -> None:
        """Wait for the recorder to finish writing, then launch processing.

        The wait blocks (ffmpeg concat of segments), so it runs on the
        TaskRunner; the processor is then spawned back on the main thread.
        """

        def _wait_then_launch(j: Job):
            self._controller.wait_until_stopped()
            if j.cancelled:
                return
            idle_call(self._launch_processor, j)

        self._runner.submit(_wait_then_launch, job, description=f"await stop: {job.label}")

    def _launch_processor(self, job: Job) -> None:
        if job.cancelled:
            return
        self._set_job_status_text(job.job_id, "Transcribing…")
        handle = self._launcher().launch(
            str(job.audio_path),
            str(job.transcript_path),
            str(job.notes_path),
            on_status=lambda msg, jid=job.job_id: self._set_job_status_text(jid, msg),
            on_done=lambda paths, j=job: self._on_processing_done(j, paths),
            on_error=lambda msg, j=job: self._on_processing_error(j, msg),
        )
        self._processors[job.job_id] = handle

    def _launcher(self):
        # Built lazily (imports Gio) so the engine module stays importable
        # headless; tests inject a fake via the constructor.
        if self._processor_launcher is None:
            from .processor import ProcessorLauncher

            self._processor_launcher = ProcessorLauncher()
        return self._processor_launcher

    def _on_processing_done(self, job: Job, paths) -> None:
        self._processors.pop(job.job_id, None)
        if job.cancelled:
            return
        # Child returns [audio, transcript, notes] as strings/None; auto-title
        # may have moved the meeting directory, so adopt the returned paths.
        audio_path, transcript_path, notes_path = (paths + [None, None, None])[:3]
        if audio_path:
            job.audio_path = Path(audio_path)
        if transcript_path:
            job.transcript_path = Path(transcript_path)
        if notes_path:
            job.notes_path = Path(notes_path)
        self._job_manager.persist()
        self._job_manager.mark_done(job)
        self._job_status_text.pop(job.job_id, None)
        self._changed()
        self._notify_complete(job)

    def _on_processing_error(self, job: Job, msg: str) -> None:
        self._processors.pop(job.job_id, None)
        if job.cancelled:
            return
        self._job_manager.mark_error(job, msg)
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
