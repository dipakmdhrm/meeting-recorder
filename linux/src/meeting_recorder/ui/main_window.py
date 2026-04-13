"""
The primary user interface and state coordinator for the Meeting Recorder application. It manages the recording lifecycle (IDLE, RECORDING, PAUSED, COUNTDOWN), handles user interactions for starting/stopping recordings, and monitors background processing jobs for transcription and summarization.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import traceback
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from meeting_recorder.config import settings
from ..utils.glib_bridge import assert_main_thread, idle_call
from meeting_recorder.utils.filename import output_paths
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


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(title="Meeting Recorder", **kwargs)
        self.set_default_size(1200, 800)
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
        self.connect("delete-event", self._on_delete)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        # Error info bar (recording errors only — pipeline errors go in job rows)
        self._info_bar = Gtk.InfoBar()
        self._info_bar.set_message_type(Gtk.MessageType.ERROR)
        self._info_bar_label = Gtk.Label(label="")
        self._info_bar_label.set_line_wrap(True)
        self._info_bar.get_content_area().add(self._info_bar_label)
        self._info_bar.add_button("Dismiss", Gtk.ResponseType.CLOSE)
        self._info_bar.connect("response", self._on_info_bar_response)
        self._info_bar.set_no_show_all(True)
        outer.pack_start(self._info_bar, False, False, 0)

        # Stack for views
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        outer.pack_start(self._stack, True, True, 0)

        # -------------------------------------------------------------
        # View 1: Recorder
        # -------------------------------------------------------------
        recorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(24)
        vbox.set_margin_bottom(24)
        vbox.set_margin_start(24)
        vbox.set_margin_end(24)
        recorder_box.pack_start(vbox, False, False, 0)

        # Timer label
        self._timer_label = Gtk.Label(label="00:00")
        self._timer_label.get_style_context().add_class("timer-label")
        self._timer_label.set_attributes(self._make_timer_attrs())
        vbox.pack_start(self._timer_label, False, False, 0)

        # Status label
        self._status_label = Gtk.Label(label="")
        self._status_label.set_line_wrap(True)
        self._status_label.set_xalign(0.5)
        vbox.pack_start(self._status_label, False, False, 0)

        # Meeting title entry
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_label = Gtk.Label(label="Title (optional):")
        title_label.set_xalign(0)
        self._title_entry = Gtk.Entry()
        self._title_entry.set_placeholder_text("e.g. Standup, Sprint Planning…")
        self._title_entry.set_hexpand(True)
        title_box.pack_start(title_label, False, False, 0)
        title_box.pack_start(self._title_entry, True, True, 0)
        vbox.pack_start(title_box, False, False, 0)

        # Button row
        self._button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=False
        )
        self._button_box.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(self._button_box, False, False, 0)

        # Output paths (shown after "cancel and save")
        self._output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._output_box.set_no_show_all(True)
        self._output_label = Gtk.Label(label="")
        self._output_label.set_line_wrap(True)
        self._output_label.set_xalign(0)
        self._open_folder_btn = Gtk.Button(label="Open Output Folder")
        self._open_folder_btn.connect("clicked", self._on_open_folder)
        self._output_box.pack_start(self._output_label, False, False, 0)
        self._output_box.pack_start(self._open_folder_btn, False, False, 0)
        vbox.pack_start(self._output_box, False, False, 0)

        # Jobs section (hidden until there are jobs)
        self._jobs_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._jobs_section.set_no_show_all(True)
        self._jobs_section.set_margin_start(24)
        self._jobs_section.set_margin_end(24)
        self._jobs_section.set_margin_bottom(12)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._jobs_section.pack_start(sep, False, False, 0)

        jobs_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        jobs_hdr.set_margin_top(8)
        hdr_label = Gtk.Label()
        hdr_label.set_markup("<b>Background Jobs</b>")
        hdr_label.set_xalign(0)
        jobs_hdr.pack_start(hdr_label, True, True, 0)
        self._jobs_section.pack_start(jobs_hdr, False, False, 0)

        self._jobs_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._jobs_list.set_margin_top(4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(180)
        scroll.set_propagate_natural_height(True)
        scroll.add(self._jobs_list)
        self._jobs_section.pack_start(scroll, False, False, 0)

        sep.show()
        jobs_hdr.show_all()
        scroll.show()
        self._jobs_list.show()

        recorder_box.pack_start(self._jobs_section, False, False, 0)

        self._stack.add_titled(recorder_box, "recorder", "Record")

        # -------------------------------------------------------------
        # View 2: Meeting Explorer
        # -------------------------------------------------------------
        self._explorer = MeetingExplorer()
        self._stack.add_titled(self._explorer, "explorer", "Library")
        self._stack.connect("notify::visible-child-name", self._on_stack_switched)

        # HeaderBar with settings button and stack switcher
        hb = Gtk.HeaderBar()
        hb.set_title("Meeting Recorder")
        hb.set_show_close_button(True)
        
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self._stack)
        hb.set_custom_title(switcher)

        settings_btn = Gtk.Button()
        settings_btn.set_image(
            Gtk.Image.new_from_icon_name("preferences-system", Gtk.IconSize.BUTTON)
        )
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._on_settings_clicked)
        hb.pack_end(settings_btn)
        self.set_titlebar(hb)

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
        for child in self._button_box.get_children():
            self._button_box.remove(child)

        state = self._state

        if state == State.IDLE:
            self._timer_label.set_text("00:00")
            self._status_label.set_text(status or "Ready to record")
            self._title_entry.set_sensitive(True)
            self._output_box.hide()

            idle_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            record_row.set_homogeneous(True)

            headphones_btn = Gtk.Button(label="Record (Headphones)")
            headphones_btn.set_image(
                Gtk.Image.new_from_icon_name("media-record", Gtk.IconSize.BUTTON)
            )
            headphones_btn.set_tooltip_text(
                "Record mic + system audio. Use when wearing headphones."
            )
            headphones_btn.connect("clicked", lambda *_: self.on_record_headphones_clicked())
            headphones_btn.get_style_context().add_class("suggested-action")
            record_row.pack_start(headphones_btn, True, True, 0)

            speaker_btn = Gtk.Button(label="Record (Speaker)")
            speaker_btn.set_image(
                Gtk.Image.new_from_icon_name("audio-volume-high", Gtk.IconSize.BUTTON)
            )
            speaker_btn.set_tooltip_text(
                "Record mic only. Use when on speaker to avoid echo."
            )
            speaker_btn.connect("clicked", lambda *_: self.on_record_speaker_clicked())
            record_row.pack_start(speaker_btn, True, True, 0)

            idle_vbox.pack_start(record_row, False, False, 0)

            existing_btn = Gtk.Button(label=" Use Existing Recording")
            existing_btn.set_image(
                Gtk.Image.new_from_icon_name("document-open", Gtk.IconSize.BUTTON)
            )
            existing_btn.connect("clicked", lambda *_: self.on_use_existing_clicked())
            idle_vbox.pack_start(existing_btn, False, False, 0)

            self._button_box.pack_start(idle_vbox, False, False, 0)

        elif state == State.RECORDING:
            self._status_label.set_text(status or "Recording…")
            self._title_entry.set_sensitive(False)
            self._output_box.hide()
            self._info_bar.hide()

            pause_btn = Gtk.Button(label=" Pause")
            pause_btn.set_image(
                Gtk.Image.new_from_icon_name("media-playback-pause", Gtk.IconSize.BUTTON)
            )
            pause_btn.connect("clicked", lambda *_: self.on_pause_clicked())
            self._button_box.pack_start(pause_btn, False, False, 0)

            stop_btn = Gtk.Button(label=" Stop")
            stop_btn.set_image(
                Gtk.Image.new_from_icon_name("media-playback-stop", Gtk.IconSize.BUTTON)
            )
            stop_btn.connect("clicked", lambda *_: self.on_stop_clicked())
            stop_btn.get_style_context().add_class("destructive-action")
            self._button_box.pack_start(stop_btn, False, False, 0)

            save_btn = Gtk.Button(label="Cancel (save recording)")
            save_btn.connect("clicked", lambda *_: self.on_cancel_save_clicked())
            self._button_box.pack_start(save_btn, False, False, 0)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_clicked())
            self._button_box.pack_start(cancel_btn, False, False, 0)

        elif state == State.PAUSED:
            self._status_label.set_text(status or "Paused")
            self._title_entry.set_sensitive(False)

            resume_btn = Gtk.Button(label=" Resume")
            resume_btn.set_image(
                Gtk.Image.new_from_icon_name("media-playback-start", Gtk.IconSize.BUTTON)
            )
            resume_btn.connect("clicked", lambda *_: self.on_resume_clicked())
            resume_btn.get_style_context().add_class("suggested-action")
            self._button_box.pack_start(resume_btn, False, False, 0)

            stop_btn = Gtk.Button(label=" Stop")
            stop_btn.set_image(
                Gtk.Image.new_from_icon_name("media-playback-stop", Gtk.IconSize.BUTTON)
            )
            stop_btn.connect("clicked", lambda *_: self.on_stop_clicked())
            stop_btn.get_style_context().add_class("destructive-action")
            self._button_box.pack_start(stop_btn, False, False, 0)

            save_btn = Gtk.Button(label="Cancel (save recording)")
            save_btn.connect("clicked", lambda *_: self.on_cancel_save_clicked())
            self._button_box.pack_start(save_btn, False, False, 0)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_clicked())
            self._button_box.pack_start(cancel_btn, False, False, 0)

        elif state == State.COUNTDOWN:
            self._title_entry.set_sensitive(False)
            self._output_box.hide()

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_countdown_clicked())
            cancel_btn.get_style_context().add_class("destructive-action")
            self._button_box.pack_start(cancel_btn, False, False, 0)

        self._button_box.show_all()

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

        dialog = Gtk.FileChooserDialog(
            title="Select Audio Recording",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        audio_filter = Gtk.FileFilter()
        audio_filter.set_name("Audio files")
        for pat in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac", "*.webm"):
            audio_filter.add_pattern(pat)
        dialog.add_filter(audio_filter)

        response = dialog.run()
        filename = dialog.get_filename()
        dialog.destroy()

        if response != Gtk.ResponseType.OK or not filename:
            return

        # Create a new session directory for the imported file
        new_audio_path, new_transcript_path, new_notes_path = output_paths(
            cfg.get("output_folder", "~/meetings")
        )
        
        # Copy the user-selected audio to the new session directory
        try:
            shutil.copy(filename, new_audio_path)
        except Exception as e:
            self._show_error(f"Failed to copy audio file: {e}")
            return

        job = _Job(
            job_id=self._next_job_id,
            audio_path=new_audio_path,
            transcript_path=new_transcript_path,
            notes_path=new_notes_path,
            label=Path(filename).name,
        )
        self._next_job_id += 1
        self._jobs.append(job)
        self._add_job_row(job)
        self._notify_tray()
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
                self._output_box.show_all()

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
            if row and row in self._jobs_list.get_children():
                self._jobs_list.remove(row)
        if job in self._jobs:
            self._jobs.remove(job)
        if not self._jobs:
            self._jobs_section.hide()
        self._notify_tray()

    # ------------------------------------------------------------------
    # Jobs panel UI
    # ------------------------------------------------------------------

    def _add_job_row(self, job: _Job) -> None:
        """Add a row for a new job to the jobs panel. Main thread only."""
        assert_main_thread()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        spinner = Gtk.Spinner()
        spinner.start()
        row.pack_start(spinner, False, False, 0)

        status_icon = Gtk.Image.new_from_icon_name("system-run", Gtk.IconSize.BUTTON)
        status_icon.set_no_show_all(True)
        row.pack_start(status_icon, False, False, 0)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        job_name_label = Gtk.Label(label=job.label)
        job_name_label.set_xalign(0)
        status_label = Gtk.Label(label="Processing…")
        status_label.set_xalign(0)
        label_box.pack_start(job_name_label, False, False, 0)
        label_box.pack_start(status_label, False, False, 0)
        row.pack_start(label_box, True, True, 0)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.pack_end(action_box, False, False, 0)

        self._job_widgets[job.job_id] = {
            "row": row,
            "spinner": spinner,
            "status_icon": status_icon,
            "status_label": status_label,
            "action_box": action_box,
        }
        self._rebuild_action_box(job)

        self._jobs_list.pack_start(row, False, False, 0)
        self._jobs_section.show()
        row.show_all()
        status_icon.hide()

    def _update_job_row(self, job: _Job) -> None:
        """Refresh icon, status text, and action buttons for a status change."""
        assert_main_thread()
        widgets = self._job_widgets.get(job.job_id)
        if not widgets:
            return

        spinner: Gtk.Spinner = widgets["spinner"]
        status_icon: Gtk.Image = widgets["status_icon"]
        status_label: Gtk.Label = widgets["status_label"]

        spinner.stop()
        spinner.hide()
        status_icon.show()

        if job.status == "done":
            status_icon.set_from_icon_name("emblem-ok-symbolic", Gtk.IconSize.BUTTON)
            status_label.set_text("Done")
        elif job.status == "error":
            status_icon.set_from_icon_name("dialog-error", Gtk.IconSize.BUTTON)
            err = (job.error_msg or "Error")[:60]
            status_label.set_text(f"Error: {err}")

        self._rebuild_action_box(job)

    def _rebuild_action_box(self, job: _Job) -> None:
        """Replace the action buttons in the job row for the current status."""
        widgets = self._job_widgets.get(job.job_id)
        if not widgets:
            return
        action_box: Gtk.Box = widgets["action_box"]
        for child in action_box.get_children():
            action_box.remove(child)

        if job.status == "processing":
            btn = Gtk.Button(label="Cancel")
            btn.connect("clicked", lambda *_, j=job: self._on_cancel_job(j))
            action_box.pack_start(btn, False, False, 0)
        elif job.status == "done":
            btn = Gtk.Button(label="Open Folder")
            btn.connect("clicked", lambda *_, j=job: self._on_open_job_folder(j))
            action_box.pack_start(btn, False, False, 0)
            action_box.pack_start(self._make_dismiss_btn(job), False, False, 0)
        elif job.status == "error":
            btn = Gtk.Button(label="Retry")
            btn.connect("clicked", lambda *_, j=job: self._on_retry_job(j))
            action_box.pack_start(btn, False, False, 0)
            action_box.pack_start(self._make_dismiss_btn(job), False, False, 0)

        action_box.show_all()

    def _make_dismiss_btn(self, job: _Job) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_image(
            Gtk.Image.new_from_icon_name("window-close", Gtk.IconSize.BUTTON)
        )
        btn.set_tooltip_text("Dismiss")
        btn.connect("clicked", lambda *_, j=job: self._dismiss_job(j))
        return btn

    def _update_job_status_text(self, job: _Job, msg: str) -> None:
        """Update the status text for a job (pipeline progress). Main thread only."""
        assert_main_thread()
        widgets = self._job_widgets.get(job.job_id)
        if widgets:
            widgets["status_label"].set_text(msg)

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
        self._info_bar_label.set_text(msg)
        self._info_bar_label.set_selectable(True)
        self._info_bar.show()
        self._info_bar_label.show()

    def _on_info_bar_response(self, bar: Gtk.InfoBar, response_id: int) -> None:
        bar.hide()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_settings_clicked(self, *_) -> None:
        from .settings_dialog import SettingsDialog
        dialog = SettingsDialog(parent=self)
        dialog.run()
        dialog.destroy()
        app = self.get_application()
        if app:
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

    def present(self) -> None:
        self.set_skip_taskbar_hint(False)
        super().present()

    def hide_to_tray(self) -> None:
        self.set_skip_taskbar_hint(True)
        self.hide()

    def _on_delete(self, *_) -> bool:
        self.hide_to_tray()
        return True
