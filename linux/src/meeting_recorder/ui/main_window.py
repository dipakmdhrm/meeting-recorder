"""
The primary user interface and state coordinator for the Meeting Recorder application. It manages the recording lifecycle (IDLE, RECORDING, PAUSED, COUNTDOWN), handles user interactions for starting/stopping recordings, and monitors background processing jobs for transcription and summarization.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import traceback
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Gio, Adw

from meeting_recorder.config import settings
from ..utils.glib_bridge import assert_main_thread, idle_call
from ..utils.gtk_compat import remove_all_children
from ..utils.recording_import import resolve_existing_recording_target
from meeting_recorder.utils.filename import output_paths
from meeting_recorder.utils.meeting_scanner import Meeting, find_audio_file
from .meeting_explorer import MeetingExplorer

logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()
    COUNTDOWN = auto()


@dataclass
class _Job:
    job_id: int
    audio_path: Path
    transcript_path: Path
    notes_path: Path
    label: str
    status: str = "processing"   # "processing" | "done" | "error"
    error_msg: str | None = None
    cancelled: bool = False


def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _icon_label_button(icon_name: str, label: str) -> "Gtk.Button":
    """Build a button showing both an icon and a label using Adw.ButtonContent
    (the libadwaita idiom for icon+label buttons)."""
    btn = Gtk.Button()
    btn.set_child(Adw.ButtonContent(icon_name=icon_name, label=label))
    return btn


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(title="Meeting Recorder", **kwargs)
        self.set_default_size(1100, 760)
        self.set_resizable(True)

        self._state = State.IDLE
        self._recorder = None
        self._recording_mode: str = "headphones"
        self._audio_path: Path | None = None
        self._transcript_path: Path | None = None
        self._notes_path: Path | None = None

        # Used only for countdown cancellation.
        self._pipeline_gen = 0
        self._countdown_remaining: int = 0
        self._recorder_done = threading.Event()

        # Jobs
        self._jobs: list[_Job] = []
        self._next_job_id: int = 0
        self._pending_job: _Job | None = None
        self._job_widgets: dict[int, dict] = {}

        self._build_ui()
        self._transition(State.IDLE)
        # GTK4 replaced the "delete-event" signal with "close-request"; the
        # handler still vetoes the close (returns True) and hides to the tray.
        self.connect("close-request", self._on_close_request)

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

        # Jobs section (hidden until there are jobs). Job rows are AdwActionRows
        # added directly to the group's boxed list.
        self._jobs_section = Adw.PreferencesGroup(title="Background Jobs")
        self._jobs_section.set_visible(False)

        vbox.append(self._jobs_section)
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
    # State machine
    # ------------------------------------------------------------------

    def _transition(self, new_state: State, **kwargs) -> None:
        assert_main_thread()
        self._state = new_state
        self._update_ui(**kwargs)
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
            speaker_btn.set_tooltip_text(
                "Record mic only. Use when on speaker to avoid echo."
            )
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
            for j in self._jobs
            if j.status == "processing" and not j.cancelled
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
        ts = cfg.get("transcription_service", "gemini")
        ss = cfg.get("summarization_service", "gemini")
        key_missing = self._check_api_keys(cfg, ts, ss)
        if key_missing:
            self._show_error(key_missing)
            return

        from ..audio.devices import validate_devices
        ok, err = validate_devices()
        if not ok:
            self._show_error(f"Audio device error: {err}")
            return

        title = self._title_entry.get_text().strip() or None
        audio, transcript, notes = output_paths(
            cfg.get("output_folder", "~/meetings"), title
        )
        self._audio_path = audio
        self._transcript_path = transcript
        self._notes_path = notes

        from ..audio.recorder import Recorder, RecordingError
        from meeting_recorder.config.defaults import RECORDING_QUALITIES

        q_key = cfg.get("recording_quality", "high")
        _, q_val = RECORDING_QUALITIES.get(q_key, RECORDING_QUALITIES["high"])

        self._recorder = Recorder(
            output_path=audio,
            mode=self._recording_mode,
            quality=q_val,
            on_tick=self._on_tick,
            on_error=self._on_recording_error,
        )
        try:
            self._recorder.start()
        except RecordingError as exc:
            self._show_error(str(exc))
            return

        mode_label = "headphones" if self._recording_mode == "headphones" else "speaker"
        self._transition(State.RECORDING, status=f"Recording… ({mode_label} mode)")

    def on_use_existing_clicked(self) -> None:
        assert_main_thread()
        if self._state != State.IDLE:
            return

        cfg = settings.load()
        ts = cfg.get("transcription_service", "gemini")
        ss = cfg.get("summarization_service", "gemini")
        key_missing = self._check_api_keys(cfg, ts, ss)
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
        reuse_in_place, paths = resolve_existing_recording_target(
            Path(filename), output_folder
        )
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

        job = _Job(
            job_id=self._next_job_id,
            audio_path=audio_path,
            transcript_path=transcript_path,
            notes_path=notes_path,
            label=Path(filename).name,
        )
        self._next_job_id += 1
        self._jobs.append(job)
        self._add_job_row(job)
        self._notify_tray()
        threading.Thread(
            target=self._run_pipeline_for_job, args=(job,), daemon=True
        ).start()

    def _on_summarize_from_explorer(self, meeting: Meeting) -> None:
        """Handle a Summarize request from the meeting explorer."""
        assert_main_thread()

        cfg = settings.load()
        ts = cfg.get("transcription_service", "gemini")
        ss = cfg.get("summarization_service", "gemini")
        key_missing = self._check_api_keys(cfg, ts, ss)
        if key_missing:
            self._show_error(key_missing)
            return

        audio_path = find_audio_file(meeting.path)
        if not audio_path:
            self._show_error("No audio file found in meeting folder.")
            return

        if any(j.audio_path == audio_path and j.status == "processing" for j in self._jobs):
            self._show_error("This meeting is already being processed.")
            return

        transcript_path = meeting.path / "transcript.md"
        notes_path = meeting.path / "notes.md"

        job = _Job(
            job_id=self._next_job_id,
            audio_path=audio_path,
            transcript_path=transcript_path,
            notes_path=notes_path,
            label=meeting.time_label,
        )
        self._next_job_id += 1
        self._jobs.append(job)
        self._add_job_row(job)
        self._notify_tray()

        # Switch to the Record tab so the user sees job progress
        self._stack.set_visible_child_name("recorder")

        threading.Thread(
            target=self._run_pipeline_for_job, args=(job,), daemon=True
        ).start()

    def on_pause_clicked(self) -> None:
        assert_main_thread()
        if self._state != State.RECORDING or not self._recorder:
            return
        self._recorder.pause()
        self._transition(State.PAUSED)

    def on_resume_clicked(self) -> None:
        assert_main_thread()
        if self._state != State.PAUSED or not self._recorder:
            return
        self._recorder.resume()
        self._transition(State.RECORDING)

    def on_stop_clicked(self) -> None:
        assert_main_thread()
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return

        self._pipeline_gen += 1
        gen_id = self._pipeline_gen
        recorder = self._recorder
        self._recorder = None

        # Create a pending job — added to the list only after the countdown
        # expires so it can be cleanly discarded if the user cancels.
        self._pending_job = _Job(
            job_id=self._next_job_id,
            audio_path=self._audio_path,
            transcript_path=self._transcript_path,
            notes_path=self._notes_path,
            label=self._make_job_label(),
        )
        self._next_job_id += 1

        self._recorder_done.clear()
        threading.Thread(
            target=self._stop_recorder_bg, args=(recorder,), daemon=True
        ).start()

        cfg = settings.load()
        if cfg.get("processing_countdown_enabled", False):
            self._countdown_remaining = 5
            self._transition(State.COUNTDOWN, status="Starting transcription in 5s…")
            GLib.timeout_add(1000, self._countdown_tick, gen_id)
        else:
            job = self._pending_job
            self._pending_job = None
            if job is not None:
                self._jobs.append(job)
                self._add_job_row(job)
                threading.Thread(
                    target=self._wait_and_process_job, args=(job,), daemon=True
                ).start()
            self._transition(State.IDLE)

    def _make_job_label(self) -> str:
        time_part = (
            self._audio_path.parent.name if self._audio_path else "recording"
        )
        title = self._title_entry.get_text().strip()
        return f"{time_part} {title}".strip() if title else time_part

    def _stop_recorder_bg(self, recorder) -> None:
        try:
            recorder.stop()
        except Exception as exc:
            logger.error("Error stopping recorder: %s", exc)
        finally:
            self._recorder_done.set()

    def _countdown_tick(self, gen_id: int) -> bool:
        if gen_id != self._pipeline_gen:
            return GLib.SOURCE_REMOVE  # cancelled

        self._countdown_remaining -= 1

        if self._countdown_remaining > 0:
            self._status_label.set_text(
                f"Starting transcription in {self._countdown_remaining}s…"
            )
            return GLib.SOURCE_CONTINUE

        # Countdown expired — commit the pending job and return to IDLE.
        job = self._pending_job
        self._pending_job = None
        if job is not None:
            self._jobs.append(job)
            self._add_job_row(job)
            threading.Thread(
                target=self._wait_and_process_job, args=(job,), daemon=True
            ).start()
        self._transition(State.IDLE)
        return GLib.SOURCE_REMOVE

    def _wait_and_process_job(self, job: _Job) -> None:
        """Background: wait for recorder.stop() to complete, then run pipeline."""
        self._recorder_done.wait(timeout=35)
        if job.cancelled:
            return
        idle_call(self._update_job_status_text, job, "Transcribing…")
        self._run_pipeline_for_job(job)

    def on_cancel_countdown_clicked(self) -> None:
        assert_main_thread()
        if self._state != State.COUNTDOWN:
            return
        self._pipeline_gen += 1
        self._pending_job = None
        self._transition(State.IDLE, status="Transcription cancelled.")
        logger.info("Transcription cancelled during countdown.")

    def on_cancel_save_clicked(self) -> None:
        assert_main_thread()
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return
        recorder = self._recorder
        audio_path = self._audio_path
        transcript_path = self._transcript_path
        notes_path = self._notes_path
        self._recorder = None
        self._transition(State.IDLE, status="Stopping recording…")

        def _bg():
            try:
                recorder.stop()
            except Exception as exc:
                idle_call(self._show_error, f"Failed to stop recording: {exc}")
                return
            idle_call(_done)

        def _done():
            self._transition(State.IDLE, status="Recording saved (no transcription).")
            paths = []
            if transcript_path and transcript_path.exists():
                paths.append(f"Transcript: {transcript_path}")
            if notes_path and notes_path.exists():
                paths.append(f"Notes: {notes_path}")
            if audio_path and audio_path.exists():
                paths.append(f"Audio: {audio_path}")
            if paths:
                self._output_label.set_text("\n".join(paths))
                self._output_box.set_visible(True)

        threading.Thread(target=_bg, daemon=True).start()

    def on_cancel_clicked(self) -> None:
        assert_main_thread()
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return
        recorder = self._recorder
        audio_path = self._audio_path
        self._recorder = None
        self._transition(State.IDLE, status="Cancelling…")

        def _bg():
            try:
                recorder.stop()
            except Exception as exc:
                idle_call(self._show_error, f"Failed to stop recording: {exc}")
                return
            if audio_path and audio_path.exists():
                try:
                    audio_path.unlink()
                except Exception as exc:
                    logger.warning("Could not delete audio file: %s", exc)
            if audio_path:
                try:
                    audio_path.parent.rmdir()
                except Exception:
                    pass
            idle_call(self._transition, State.IDLE)

        threading.Thread(target=_bg, daemon=True).start()

    # ------------------------------------------------------------------
    # Pipeline / job management
    # ------------------------------------------------------------------

    def _run_pipeline_for_job(self, job: _Job) -> None:
        """Background: run transcription + summarisation for a job."""
        from meeting_recorder.processing.pipeline import Pipeline

        cfg = settings.load()
        pipeline = Pipeline(
            config=cfg,
            audio_path=job.audio_path,
            transcript_path=job.transcript_path,
            notes_path=job.notes_path,
            on_status=lambda msg: (
                idle_call(self._update_job_status_text, job, msg)
                if not job.cancelled else None
            ),
        )
        try:
            pipeline.run()
            # Update job paths in case auto-title renamed the directory
            audio_path, transcript_path, notes_path = pipeline.output_paths
            job.audio_path = audio_path
            if transcript_path:
                job.transcript_path = transcript_path
            if notes_path:
                job.notes_path = notes_path

            if not job.cancelled:
                idle_call(self._on_job_done, job)
        except Exception as exc:
            full = traceback.format_exc()
            logger.error("Pipeline failed for job %d:\n%s", job.job_id, full)
            if not job.cancelled:
                idle_call(self._on_job_error, job, str(exc))

    def _on_job_done(self, job: _Job) -> None:
        assert_main_thread()
        job.status = "done"
        self._update_job_row(job)
        self._notify_tray()
        self._send_job_complete_notification(job)

    def _on_job_error(self, job: _Job, msg: str) -> None:
        assert_main_thread()
        job.status = "error"
        job.error_msg = msg
        self._update_job_row(job)
        self._notify_tray()

    def _on_cancel_job(self, job: _Job) -> None:
        assert_main_thread()
        job.cancelled = True
        self._dismiss_job(job)
        logger.info("Job %d cancelled by user", job.job_id)

    def _on_retry_job(self, job: _Job) -> None:
        assert_main_thread()
        job.status = "processing"
        job.error_msg = None
        job.cancelled = False
        self._update_job_row(job)
        self._notify_tray()
        threading.Thread(
            target=self._run_pipeline_for_job, args=(job,), daemon=True
        ).start()

    def _on_open_job_folder(self, job: _Job) -> None:
        try:
            subprocess.Popen(["xdg-open", str(job.audio_path.parent)])
        except Exception:
            pass

    def _dismiss_job(self, job: _Job) -> None:
        assert_main_thread()
        widgets = self._job_widgets.pop(job.job_id, None)
        if widgets:
            row = widgets.get("row")
            if row is not None:
                self._jobs_section.remove(row)
        if job in self._jobs:
            self._jobs.remove(job)
        if not self._jobs:
            self._jobs_section.set_visible(False)
        self._notify_tray()

    # ------------------------------------------------------------------
    # Jobs panel UI
    # ------------------------------------------------------------------

    def _add_job_row(self, job: _Job) -> None:
        """Add a row for a new job to the jobs panel. Main thread only."""
        assert_main_thread()

        row = Adw.ActionRow(title=job.label, subtitle="Processing…")
        row.set_title_lines(1)

        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_valign(Gtk.Align.CENTER)
        row.add_prefix(spinner)

        status_icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        status_icon.set_valign(Gtk.Align.CENTER)
        status_icon.set_visible(False)
        row.add_prefix(status_icon)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        action_box.set_valign(Gtk.Align.CENTER)
        row.add_suffix(action_box)

        self._job_widgets[job.job_id] = {
            "row": row,
            "spinner": spinner,
            "status_icon": status_icon,
            "action_box": action_box,
        }
        self._rebuild_action_box(job)

        self._jobs_section.add(row)
        self._jobs_section.set_visible(True)

    def _update_job_row(self, job: _Job) -> None:
        """Refresh icon, status text, and action buttons for a status change."""
        assert_main_thread()
        widgets = self._job_widgets.get(job.job_id)
        if not widgets:
            return

        spinner: Gtk.Spinner = widgets["spinner"]
        status_icon: Gtk.Image = widgets["status_icon"]
        row: Adw.ActionRow = widgets["row"]

        spinner.stop()
        spinner.set_visible(False)
        status_icon.set_visible(True)

        if job.status == "done":
            status_icon.set_from_icon_name("emblem-ok-symbolic")
            row.set_subtitle("Done")
        elif job.status == "error":
            status_icon.set_from_icon_name("dialog-error-symbolic")
            err = (job.error_msg or "Error")[:60]
            row.set_subtitle(f"Error: {err}")

        self._rebuild_action_box(job)

    def _rebuild_action_box(self, job: _Job) -> None:
        """Replace the action buttons in the job row for the current status."""
        widgets = self._job_widgets.get(job.job_id)
        if not widgets:
            return
        action_box: Gtk.Box = widgets["action_box"]
        remove_all_children(action_box)

        if job.status == "processing":
            btn = Gtk.Button(label="Cancel")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, j=job: self._on_cancel_job(j))
            action_box.append(btn)
        elif job.status == "done":
            btn = Gtk.Button(label="Open Folder")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, j=job: self._on_open_job_folder(j))
            action_box.append(btn)
            action_box.append(self._make_dismiss_btn(job))
        elif job.status == "error":
            btn = Gtk.Button(label="Retry")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, j=job: self._on_retry_job(j))
            action_box.append(btn)
            action_box.append(self._make_dismiss_btn(job))

    def _make_dismiss_btn(self, job: _Job) -> Gtk.Button:
        btn = Gtk.Button(icon_name="window-close-symbolic")
        btn.add_css_class("flat")
        btn.set_tooltip_text("Dismiss")
        btn.connect("clicked", lambda *_, j=job: self._dismiss_job(j))
        return btn

    def _update_job_status_text(self, job: _Job, msg: str) -> None:
        """Update the status text for a job (pipeline progress). Main thread only."""
        assert_main_thread()
        widgets = self._job_widgets.get(job.job_id)
        if widgets:
            widgets["row"].set_subtitle(msg)

    # ------------------------------------------------------------------
    # Recorder / pipeline callbacks (may arrive from background threads)
    # ------------------------------------------------------------------

    def _on_tick(self, elapsed: int) -> None:
        idle_call(self._update_timer, elapsed)

    def _update_timer(self, elapsed: int) -> None:
        assert_main_thread()
        self._timer_label.set_text(_format_time(elapsed))

    def _on_recording_error(self, msg: str) -> None:
        idle_call(self._transition, State.IDLE)
        idle_call(self._show_error, msg)

    def _send_job_complete_notification(self, job: _Job) -> None:
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
        # Recording/setup errors surface as an Adwaita toast (transient,
        # dismissable). Pipeline errors still appear in their job row.
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

    def _check_api_keys(self, cfg: dict, ts: str, ss: str) -> str | None:
        if ts == "gemini" and not cfg.get("gemini_api_key"):
            return "Gemini API key is not configured. Please open Settings."
        if ss == "gemini" and not cfg.get("gemini_api_key"):
            return "Gemini API key is not configured. Please open Settings."
        return None

    def _on_open_folder(self, *_) -> None:
        cfg = settings.load()
        folder = os.path.expanduser(cfg.get("output_folder", "~/meetings"))
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

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
