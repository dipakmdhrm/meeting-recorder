"""
The primary user interface and state coordinator for the Meeting Recorder application. It manages
the recording lifecycle (IDLE, RECORDING, PAUSED, COUNTDOWN), handles user interactions for
starting/stopping recordings, and monitors background processing jobs for transcription and
summarization.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from meeting_recorder.config import settings
from meeting_recorder.utils.filename import output_paths
from meeting_recorder.utils.meeting_scanner import Meeting, find_audio_file

from ..core.errors import error_presentation
from ..core.job import Job, JobStatus
from ..core.job_manager import JobManager
from ..core.recording_controller import PendingRecording, RecordingController
from ..core.state_machine import State
from ..core.task_runner import CancelToken, TaskRunner
from ..utils.glib_bridge import assert_main_thread, idle_call
from ..utils.gtk_compat import remove_all_children
from ..utils.recording_import import resolve_existing_recording_target
from .jobs_panel import JobsPanel
from .meeting_explorer import MeetingExplorer

logger = logging.getLogger(__name__)


# State and Job now live in core/ (pure, unit-tested); State is re-exported
# here for existing importers (app.py checks window state via this module).
__all__ = ["MainWindow", "State"]


def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _icon_label_button(icon_name: str, label: str) -> Gtk.Button:
    """Build a button showing both an icon and a label using Adw.ButtonContent
    (the libadwaita idiom for icon+label buttons)."""
    btn = Gtk.Button()
    btn.set_child(Adw.ButtonContent(icon_name=icon_name, label=label))
    return btn


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, runner: TaskRunner, **kwargs) -> None:
        super().__init__(title="Meeting Recorder", **kwargs)
        self.set_default_size(1100, 760)
        self.set_resizable(True)

        # All background work goes through the app-wide TaskRunner (results
        # and errors are routed back to the main thread; joined on exit).
        self._runner = runner
        self._recording_mode: str = "headphones"

        # The recording lifecycle (recorder, countdown, state) lives in the
        # headless controller; this window renders its state changes.
        self._controller = RecordingController(
            runner,
            on_state=self._apply_state,
            on_error=self._show_error,
            on_commit=self._on_recording_committed,
            on_saved=self._on_recording_saved,
            on_discarded=lambda: None,
            on_countdown=self._on_countdown_tick,
            on_timer=self._on_tick,
            on_recorder_error=self._on_recording_error,
        )

        # Jobs — owned by the JobManager (persisted to jobs.json so a crash
        # or quit mid-transcription re-offers the job on next start).
        self._job_manager = JobManager()
        self._jobs_panel = JobsPanel(
            on_cancel=self._on_cancel_job,
            on_retry=self._on_retry_job,
            on_open_folder=self._on_open_job_folder,
            on_dismiss=self._dismiss_job,
        )

        self._build_ui()
        self._restore_persisted_jobs()
        self._apply_state(State.IDLE, "")
        # GTK4 replaced the "delete-event" signal with "close-request"; the
        # handler still vetoes the close (returns True) and hides to the tray.
        self.connect("close-request", self._on_close_request)

    def _restore_persisted_jobs(self) -> None:
        """Re-offer jobs from the previous session (crash/quit recovery).

        Interrupted jobs come back as error rows with a Retry button —
        mirrors the Android app's recoverOrphanedRecordings() convention.
        """
        for job in self._job_manager.load_persisted():
            self._jobs_panel.add_job(job)
            self._jobs_panel.update_job(job)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Adw.ApplicationWindow has no separate titlebar slot — the whole window
        # content is one widget. We use an AdwToolbarView (header bar + content)
        # wrapped in an AdwToastOverlay so transient errors can appear as toasts.
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self._toast_overlay.set_child(toolbar_view)

        # View stack + switcher (replaces Gtk.Stack/StackSwitcher).
        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)
        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._on_settings_clicked)
        header.pack_end(settings_btn)
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self._stack)

        # -------------------------------------------------------------
        # View 1: Recorder
        # -------------------------------------------------------------
        recorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        recorder_box.set_margin_top(24)
        recorder_box.set_margin_bottom(24)
        recorder_box.set_margin_start(12)
        recorder_box.set_margin_end(12)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Timer label
        self._timer_label = Gtk.Label(label="00:00")
        self._timer_label.add_css_class("timer-label")
        self._timer_label.set_attributes(self._make_timer_attrs())
        vbox.append(self._timer_label)

        # Status label
        self._status_label = Gtk.Label(label="")
        self._status_label.set_wrap(True)
        self._status_label.set_xalign(0.5)
        self._status_label.add_css_class("dim-label")
        vbox.append(self._status_label)

        # Meeting title entry (boxed-list row for the Adwaita look)
        title_group = Adw.PreferencesGroup()
        self._title_row = Adw.EntryRow(title="Title (optional)")
        title_group.add(self._title_row)
        # Expose a Gtk.Entry-compatible shim so the rest of the code can keep
        # using get_text()/set_sensitive() on self._title_entry.
        self._title_entry = self._title_row
        vbox.append(title_group)

        # Button row
        self._button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=False
        )
        self._button_box.set_halign(Gtk.Align.CENTER)
        vbox.append(self._button_box)

        # Output paths (shown after "cancel and save")
        self._output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._output_box.set_visible(False)
        self._output_label = Gtk.Label(label="")
        self._output_label.set_wrap(True)
        self._output_label.set_xalign(0)
        self._output_label.add_css_class("dim-label")
        self._open_folder_btn = Gtk.Button(label="Open Output Folder")
        self._open_folder_btn.set_halign(Gtk.Align.CENTER)
        self._open_folder_btn.connect("clicked", self._on_open_folder)
        self._output_box.append(self._output_label)
        self._output_box.append(self._open_folder_btn)
        vbox.append(self._output_box)

        # Jobs section (hidden until there are jobs); rendering lives in
        # ui/jobs_panel.py.
        vbox.append(self._jobs_panel.widget)
        recorder_box.append(vbox)

        # Clamp keeps the recorder content centred at a comfortable width
        # instead of stretching across a wide window.
        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_child(recorder_box)
        recorder_scroll = Gtk.ScrolledWindow()
        recorder_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        recorder_scroll.set_child(clamp)

        self._stack.add_titled_with_icon(
            recorder_scroll, "recorder", "Record", "media-record-symbolic"
        )

        # -------------------------------------------------------------
        # View 2: Meeting Explorer
        # -------------------------------------------------------------
        self._explorer = MeetingExplorer(on_summarize=self._on_summarize_from_explorer)
        self._stack.add_titled_with_icon(
            self._explorer, "explorer", "Library", "view-list-symbolic"
        )
        self._stack.connect("notify::visible-child-name", self._on_stack_switched)

    def _on_stack_switched(self, stack, param):
        if stack.get_visible_child_name() == "explorer":
            self._explorer.refresh()

    def _make_timer_attrs(self):
        gi.require_version("Pango", "1.0")
        from gi.repository import Pango

        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_size_new_absolute(48 * Pango.SCALE))
        return attrs

    # ------------------------------------------------------------------
    # State rendering (the controller owns the state machine)
    # ------------------------------------------------------------------

    @property
    def _state(self) -> State:
        # app.py and the tray read the lifecycle state through this window.
        return self._controller.state

    def _apply_state(self, state: State, status: str) -> None:
        assert_main_thread()
        self._update_ui(status=status)
        self._notify_tray()

    def _update_ui(self, status: str = "", **kwargs) -> None:
        assert_main_thread()
        remove_all_children(self._button_box)

        state = self._state

        if state == State.IDLE:
            self._timer_label.set_text("00:00")
            self._status_label.set_text(status or "Ready to record")
            self._title_entry.set_sensitive(True)
            self._output_box.set_visible(False)

            idle_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            record_row.set_homogeneous(True)

            headphones_btn = _icon_label_button("media-record-symbolic", "Record (Headphones)")
            headphones_btn.set_tooltip_text(
                "Record mic + system audio. Use when wearing headphones."
            )
            headphones_btn.connect("clicked", lambda *_: self.on_record_headphones_clicked())
            headphones_btn.add_css_class("suggested-action")
            headphones_btn.add_css_class("pill")
            headphones_btn.set_hexpand(True)
            record_row.append(headphones_btn)

            speaker_btn = _icon_label_button("audio-input-microphone-symbolic", "Record (Speaker)")
            speaker_btn.set_tooltip_text("Record mic only. Use when on speaker to avoid echo.")
            speaker_btn.connect("clicked", lambda *_: self.on_record_speaker_clicked())
            speaker_btn.add_css_class("pill")
            speaker_btn.set_hexpand(True)
            record_row.append(speaker_btn)

            idle_vbox.append(record_row)

            existing_btn = _icon_label_button("document-open-symbolic", "Use Existing Recording")
            existing_btn.connect("clicked", lambda *_: self.on_use_existing_clicked())
            existing_btn.add_css_class("pill")
            existing_btn.set_halign(Gtk.Align.CENTER)
            idle_vbox.append(existing_btn)

            self._button_box.append(idle_vbox)

        elif state == State.RECORDING:
            self._status_label.set_text(status or "Recording…")
            self._title_entry.set_sensitive(False)
            self._output_box.set_visible(False)

            pause_btn = _icon_label_button("media-playback-pause-symbolic", "Pause")
            pause_btn.connect("clicked", lambda *_: self.on_pause_clicked())
            pause_btn.add_css_class("pill")
            self._button_box.append(pause_btn)

            stop_btn = _icon_label_button("media-playback-stop-symbolic", "Stop")
            stop_btn.connect("clicked", lambda *_: self.on_stop_clicked())
            stop_btn.add_css_class("destructive-action")
            stop_btn.add_css_class("pill")
            self._button_box.append(stop_btn)

            save_btn = Gtk.Button(label="Cancel (save recording)")
            save_btn.add_css_class("pill")
            save_btn.connect("clicked", lambda *_: self.on_cancel_save_clicked())
            self._button_box.append(save_btn)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.add_css_class("pill")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_clicked())
            self._button_box.append(cancel_btn)

        elif state == State.PAUSED:
            self._status_label.set_text(status or "Paused")
            self._title_entry.set_sensitive(False)

            resume_btn = _icon_label_button("media-playback-start-symbolic", "Resume")
            resume_btn.connect("clicked", lambda *_: self.on_resume_clicked())
            resume_btn.add_css_class("suggested-action")
            resume_btn.add_css_class("pill")
            self._button_box.append(resume_btn)

            stop_btn = _icon_label_button("media-playback-stop-symbolic", "Stop")
            stop_btn.connect("clicked", lambda *_: self.on_stop_clicked())
            stop_btn.add_css_class("destructive-action")
            stop_btn.add_css_class("pill")
            self._button_box.append(stop_btn)

            save_btn = Gtk.Button(label="Cancel (save recording)")
            save_btn.add_css_class("pill")
            save_btn.connect("clicked", lambda *_: self.on_cancel_save_clicked())
            self._button_box.append(save_btn)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.add_css_class("pill")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_clicked())
            self._button_box.append(cancel_btn)

        elif state == State.COUNTDOWN:
            self._title_entry.set_sensitive(False)
            self._output_box.set_visible(False)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_countdown_clicked())
            cancel_btn.add_css_class("destructive-action")
            cancel_btn.add_css_class("pill")
            self._button_box.append(cancel_btn)

    def _notify_tray(self) -> None:
        app = self.get_application()
        if not (app and hasattr(app, "_tray") and app._tray):
            return
        state_names = {
            State.IDLE: "idle",
            State.RECORDING: "recording",
            State.PAUSED: "paused",
            State.COUNTDOWN: "idle",
        }
        recording_state = state_names.get(self._state, "idle")
        tray_jobs = [
            (j.label, lambda j=j: idle_call(self._on_cancel_job, j))
            for j in self._job_manager.jobs
            if j.status is JobStatus.PROCESSING and not j.cancelled
        ]
        try:
            app._tray.update(recording_state, tray_jobs)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def on_record_headphones_clicked(self) -> None:
        self._recording_mode = "headphones"
        self._start_recording()

    def on_record_speaker_clicked(self) -> None:
        self._recording_mode = "speaker"
        self._start_recording()

    def _start_recording(self) -> None:
        assert_main_thread()
        if self._state != State.IDLE:
            return

        cfg = settings.load()
        key_missing = settings.api_key_error(cfg)
        if key_missing:
            self._show_error(key_missing)
            return

        title = self._title_entry.get_text().strip() or None
        self._controller.start(cfg, self._recording_mode, title)

    def on_use_existing_clicked(self) -> None:
        assert_main_thread()
        if self._state != State.IDLE:
            return

        cfg = settings.load()
        key_missing = settings.api_key_error(cfg)
        if key_missing:
            self._show_error(key_missing)
            return

        # GTK4 has no blocking FileChooserDialog.run(); Gtk.FileDialog.open()
        # is async, so everything that used to follow run() now happens in the
        # _opened callback below.
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Audio Recording")
        audio_filter = Gtk.FileFilter()
        audio_filter.set_name("Audio files")
        for pat in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac", "*.webm"):
            audio_filter.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(audio_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(audio_filter)
        dialog.open(self, None, lambda dlg, res: self._on_existing_chosen(dlg, res, cfg))

    def _on_existing_chosen(self, dialog, result, cfg) -> None:
        assert_main_thread()
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled or dismissed
        if not gfile:
            return
        filename = gfile.get_path()
        if not filename:
            return

        # If the selected file is already inside a meeting subdirectory,
        # process it in-place instead of copying to a new directory.
        output_folder = Path(os.path.expanduser(cfg.get("output_folder", "~/meetings")))
        reuse_in_place, paths = resolve_existing_recording_target(Path(filename), output_folder)
        if reuse_in_place:
            audio_path, transcript_path, notes_path = paths
        else:
            # File is from outside the meetings tree — create new directory & copy
            audio_path, transcript_path, notes_path = output_paths(
                cfg.get("output_folder", "~/meetings")
            )
            try:
                shutil.copy(filename, audio_path)
            except Exception as e:
                self._show_error(f"Failed to copy audio file: {e}")
                return

        job = self._job_manager.create(
            audio_path=audio_path,
            transcript_path=transcript_path,
            notes_path=notes_path,
            label=Path(filename).name,
        )
        self._jobs_panel.add_job(job)
        self._notify_tray()
        self._submit_pipeline_job(job)

    def _on_summarize_from_explorer(self, meeting: Meeting) -> None:
        """Handle a Summarize request from the meeting explorer."""
        assert_main_thread()

        cfg = settings.load()
        key_missing = settings.api_key_error(cfg)
        if key_missing:
            self._show_error(key_missing)
            return

        audio_path = find_audio_file(meeting.path)
        if not audio_path:
            self._show_error("No audio file found in meeting folder.")
            return

        if any(
            j.audio_path == audio_path and j.status is JobStatus.PROCESSING
            for j in self._job_manager.jobs
        ):
            self._show_error("This meeting is already being processed.")
            return

        transcript_path = meeting.path / "transcript.md"
        notes_path = meeting.path / "notes.md"

        job = self._job_manager.create(
            audio_path=audio_path,
            transcript_path=transcript_path,
            notes_path=notes_path,
            label=meeting.time_label,
        )
        self._jobs_panel.add_job(job)
        self._notify_tray()

        # Switch to the Record tab so the user sees job progress
        self._stack.set_visible_child_name("recorder")

        self._submit_pipeline_job(job)

    def on_pause_clicked(self) -> None:
        assert_main_thread()
        self._controller.pause()

    def on_resume_clicked(self) -> None:
        assert_main_thread()
        self._controller.resume()

    def on_stop_clicked(self) -> None:
        assert_main_thread()
        cfg = settings.load()
        self._controller.stop(cfg.get("processing_countdown_enabled", False))

    def _on_recording_committed(self, pending: PendingRecording) -> None:
        """Controller callback: the stopped recording should be processed."""
        assert_main_thread()
        job = self._job_manager.create(
            audio_path=pending.audio_path,
            transcript_path=pending.transcript_path,
            notes_path=pending.notes_path,
            label=pending.label,
        )
        self._jobs_panel.add_job(job)
        self._notify_tray()
        self._submit_recorded_job(job)

    def _on_recording_saved(self, pending: PendingRecording) -> None:
        """Controller callback: recording kept without transcription."""
        assert_main_thread()
        paths = []
        if pending.transcript_path and pending.transcript_path.exists():
            paths.append(f"Transcript: {pending.transcript_path}")
        if pending.notes_path and pending.notes_path.exists():
            paths.append(f"Notes: {pending.notes_path}")
        if pending.audio_path and pending.audio_path.exists():
            paths.append(f"Audio: {pending.audio_path}")
        if paths:
            self._output_label.set_text("\n".join(paths))
            self._output_box.set_visible(True)

    def _on_countdown_tick(self, remaining: int) -> None:
        assert_main_thread()
        self._status_label.set_text(f"Starting transcription in {remaining}s…")

    def on_cancel_countdown_clicked(self) -> None:
        assert_main_thread()
        self._controller.cancel_countdown()

    def on_cancel_save_clicked(self) -> None:
        assert_main_thread()
        self._controller.cancel_and_save()

    def on_cancel_clicked(self) -> None:
        assert_main_thread()
        self._controller.cancel_and_discard()

    # ------------------------------------------------------------------
    # Pipeline / job management
    # ------------------------------------------------------------------

    def _submit_pipeline_job(self, job: Job) -> None:
        """Run the AI pipeline for *job* in the background.

        The worker only reads the job; all job mutations happen in the
        main-thread callbacks, so no locking is needed.
        """
        self._runner.submit(
            self._pipeline_worker,
            job,
            on_done=lambda paths, j=job: self._on_pipeline_finished(j, paths),
            on_error=lambda exc, j=job: self._on_pipeline_failed(j, exc),
            description=f"pipeline: {job.label}",
        )

    def _submit_recorded_job(self, job: Job) -> None:
        """Like _submit_pipeline_job, but waits for the recorder to finish first."""
        self._runner.submit(
            self._recorded_job_worker,
            job,
            on_done=lambda paths, j=job: self._on_pipeline_finished(j, paths),
            on_error=lambda exc, j=job: self._on_pipeline_failed(j, exc),
            description=f"pipeline (after stop): {job.label}",
        )

    def _recorded_job_worker(self, job: Job):
        """Worker: wait for recorder.stop() to complete, then run the pipeline."""
        self._controller.wait_until_stopped()
        if job.cancelled:
            return None
        idle_call(self._update_job_status_text, job, "Transcribing…")
        return self._pipeline_worker(job)

    def _pipeline_worker(self, job: Job):
        """Worker: run transcription + summarisation. Returns final output paths,
        or None if the job was cancelled between stages."""
        from meeting_recorder.processing.pipeline import Pipeline, PipelineCancelled

        cfg = settings.load()
        pipeline = Pipeline(
            config=cfg,
            audio_path=job.audio_path,
            transcript_path=job.transcript_path,
            notes_path=job.notes_path,
            on_status=lambda msg: (
                idle_call(self._update_job_status_text, job, msg) if not job.cancelled else None
            ),
        )
        try:
            pipeline.run(cancel_token=job.token)
        except PipelineCancelled:
            logger.info("Pipeline for job %d stopped after cancellation", job.job_id)
            return None
        return pipeline.output_paths

    def _on_pipeline_finished(self, job: Job, paths) -> None:
        assert_main_thread()
        if job.cancelled or paths is None:
            return
        # Update job paths in case auto-title renamed the directory.
        audio_path, transcript_path, notes_path = paths
        if audio_path:
            job.audio_path = audio_path
        if transcript_path:
            job.transcript_path = transcript_path
        if notes_path:
            job.notes_path = notes_path
        self._job_manager.persist()  # auto-title may have moved the paths
        self._on_job_done(job)

    def _on_pipeline_failed(self, job: Job, exc: Exception) -> None:
        assert_main_thread()
        if not job.cancelled:
            self._on_job_error(job, str(exc))

    def _on_job_done(self, job: Job) -> None:
        assert_main_thread()
        self._job_manager.mark_done(job)
        self._jobs_panel.update_job(job)
        self._notify_tray()
        self._send_job_complete_notification(job)

    def _on_job_error(self, job: Job, msg: str) -> None:
        assert_main_thread()
        self._job_manager.mark_error(job, msg)
        self._jobs_panel.update_job(job)
        self._notify_tray()

    def _on_cancel_job(self, job: Job) -> None:
        assert_main_thread()
        job.cancelled = True
        job.token.cancel()
        self._dismiss_job(job)
        logger.info("Job %d cancelled by user", job.job_id)

    def _on_retry_job(self, job: Job) -> None:
        assert_main_thread()
        job.cancelled = False
        job.token = CancelToken()
        self._job_manager.mark_processing(job)
        self._jobs_panel.update_job(job)
        self._notify_tray()
        self._submit_pipeline_job(job)

    def _on_open_job_folder(self, job: Job) -> None:
        try:
            subprocess.Popen(["xdg-open", str(job.audio_path.parent)])
        except Exception:
            pass

    def _dismiss_job(self, job: Job) -> None:
        assert_main_thread()
        self._jobs_panel.remove_job(job)
        self._job_manager.remove(job)
        self._notify_tray()

    # ------------------------------------------------------------------
    # Recorder / pipeline callbacks (may arrive from background threads)
    # ------------------------------------------------------------------

    def _update_job_status_text(self, job: Job, msg: str) -> None:
        self._jobs_panel.set_status_text(job, msg)

    def _on_tick(self, elapsed: int) -> None:
        idle_call(self._update_timer, elapsed)

    def _update_timer(self, elapsed: int) -> None:
        assert_main_thread()
        self._timer_label.set_text(_format_time(elapsed))

    def _on_recording_error(self, msg: str) -> None:
        # Called from the recorder's monitor thread — hop to the main thread.
        idle_call(self._controller.abort_to_idle)
        idle_call(self._show_error, msg)

    def _send_job_complete_notification(self, job: Job) -> None:
        from .notifications import notify

        body_parts = []
        if job.transcript_path:
            body_parts.append(str(job.transcript_path))
        if job.notes_path:
            body_parts.append(str(job.notes_path))
        notify(
            summary="Meeting Recorded",
            body="\n".join(body_parts) if body_parts else "Processing complete.",
        )

    # ------------------------------------------------------------------
    # Error display
    # ------------------------------------------------------------------

    def _show_error(self, msg: str) -> None:
        assert_main_thread()
        logger.error("UI error shown: %s", msg)
        # One presentation policy for all errors (core/errors.py): actionable
        # configuration problems get a modal alert; transient/runtime errors
        # get a dismissable toast. Pipeline errors still appear in their row.
        if error_presentation(msg) == "dialog":
            alert = Gtk.AlertDialog()
            alert.set_modal(True)
            alert.set_message("Meeting Recorder")
            alert.set_detail(msg)
            alert.set_buttons(["OK"])
            alert.show(self)
        else:
            toast = Adw.Toast(title=msg)
            toast.set_timeout(0)  # stays until dismissed or replaced
            self._toast_overlay.add_toast(toast)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_settings_clicked(self, *_) -> None:
        # GTK4 has no blocking Gtk.Dialog.run(); the dialog is shown modeless and
        # the post-save reconfiguration happens in the on_saved callback.
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(parent=self, on_saved=self._after_settings_saved)
        dialog.present()

    def _after_settings_saved(self) -> None:
        app = self.get_application()
        if not app:
            return
        cfg = settings.load()
        if cfg.get("call_detection_enabled") and not app._call_detector:
            app._start_call_detector()
        elif not cfg.get("call_detection_enabled") and app._call_detector:
            app._call_detector.stop()
            app._call_detector = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_open_folder(self, *_) -> None:
        cfg = settings.load()
        folder = os.path.expanduser(cfg.get("output_folder", "~/meetings"))
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def prepare_quit(self) -> None:
        """Stop any active recording (keeping the audio) before the app quits.

        cancel_and_save() is a no-op unless recording/paused; the stop runs on
        the TaskRunner, and app.do_shutdown()'s bounded-grace join makes sure
        the segments are concatenated before the process exits.
        """
        self._controller.cancel_and_save()

    def present_window(self) -> None:
        """Show, raise and focus the window — used by the tray (left-click and
        the "Show Window" menu item). Re-shows the window if it was hidden to
        the tray and un-minimises it before presenting.

        GTK4 removed set_skip_taskbar_hint(), present_with_time() and
        Gtk.get_current_event_time(); focus is now mediated by the compositor,
        so present() is the supported path (left-click-to-focus is best-effort
        on Wayland/GNOME)."""
        self.set_visible(True)
        self.unminimize()
        self.present()

    def hide_to_tray(self) -> None:
        self.set_visible(False)

    def _on_close_request(self, *_) -> bool:
        self.hide_to_tray()
        return True
