"""Main window with state machine: IDLE → RECORDING → PAUSED → PROCESSING → IDLE."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from enum import Enum, auto
from pathlib import Path
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ..config import settings
from ..utils.glib_bridge import assert_main_thread, idle_call
from ..utils.filename import output_paths

logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()
    PROCESSING = auto()


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
        self.set_default_size(400, 300)
        self.set_resizable(False)

        self._state = State.IDLE
        self._recorder = None
        self._audio_path: Path | None = None
        self._transcript_path: Path | None = None
        self._notes_path: Path | None = None
        self._last_error: str | None = None
        self._pipeline_gen = 0  # incremented each time a pipeline starts/cancels

        self._build_ui()
        self._transition(State.IDLE)

        self.connect("delete-event", self._on_delete)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        # Error info bar
        self._info_bar = Gtk.InfoBar()
        self._info_bar.set_message_type(Gtk.MessageType.ERROR)
        self._info_bar_label = Gtk.Label(label="")
        self._info_bar_label.set_line_wrap(True)
        self._info_bar.get_content_area().add(self._info_bar_label)
        self._info_bar.add_button("Retry", Gtk.ResponseType.OK)
        self._info_bar.add_button("Dismiss", Gtk.ResponseType.CLOSE)
        self._info_bar.connect("response", self._on_info_bar_response)
        self._info_bar.set_no_show_all(True)
        outer.pack_start(self._info_bar, False, False, 0)

        # Main content area
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(24)
        vbox.set_margin_bottom(24)
        vbox.set_margin_start(24)
        vbox.set_margin_end(24)
        outer.pack_start(vbox, True, True, 0)

        # Timer label
        self._timer_label = Gtk.Label(label="00:00")
        self._timer_label.get_style_context().add_class("timer-label")
        attrs = self._make_timer_attrs()
        self._timer_label.set_attributes(attrs)
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
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            homogeneous=False,
        )
        self._button_box.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(self._button_box, False, False, 0)

        # Output paths (shown after processing)
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

        # Spinner
        self._spinner = Gtk.Spinner()
        self._spinner.set_no_show_all(True)
        vbox.pack_start(self._spinner, False, False, 0)

        # Settings button (top-right via HeaderBar)
        hb = Gtk.HeaderBar()
        hb.set_title("Meeting Recorder")
        hb.set_show_close_button(True)
        settings_btn = Gtk.Button()
        settings_btn.set_image(
            Gtk.Image.new_from_icon_name("preferences-system", Gtk.IconSize.BUTTON)
        )
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._on_settings_clicked)
        hb.pack_end(settings_btn)
        self.set_titlebar(hb)

    def _make_timer_attrs(self):
        import gi
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
        """Rebuild button row and visibility for current state."""
        assert_main_thread()

        # Clear button box
        for child in self._button_box.get_children():
            self._button_box.remove(child)

        state = self._state

        if state == State.IDLE:
            self._timer_label.set_text("00:00")
            self._status_label.set_text(status or "Ready to record")
            self._title_entry.set_sensitive(True)
            self._spinner.stop()
            self._spinner.hide()
            self._output_box.hide()
            record_btn = Gtk.Button(label=" Record")
            record_btn.set_image(
                Gtk.Image.new_from_icon_name("media-record", Gtk.IconSize.BUTTON)
            )
            record_btn.connect("clicked", lambda *_: self.on_record_clicked())
            record_btn.get_style_context().add_class("suggested-action")
            self._button_box.pack_start(record_btn, False, False, 0)

            existing_btn = Gtk.Button(label=" Use Existing Recording")
            existing_btn.set_image(
                Gtk.Image.new_from_icon_name("document-open", Gtk.IconSize.BUTTON)
            )
            existing_btn.connect("clicked", lambda *_: self.on_use_existing_clicked())
            self._button_box.pack_start(existing_btn, False, False, 0)

        elif state == State.RECORDING:
            self._status_label.set_text(status or "Recording…")
            self._title_entry.set_sensitive(False)
            self._spinner.stop()
            self._spinner.hide()
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

        elif state == State.PROCESSING:
            self._status_label.set_text(status or "Processing…")
            self._title_entry.set_sensitive(False)
            self._spinner.show()
            self._spinner.start()
            self._output_box.hide()

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_processing_clicked())
            self._button_box.pack_start(cancel_btn, False, False, 0)

        self._button_box.show_all()

    def _notify_tray(self) -> None:
        app = self.get_application()
        if app and hasattr(app, "_tray") and app._tray:
            state_names = {
                State.IDLE: "idle",
                State.RECORDING: "recording",
                State.PAUSED: "paused",
                State.PROCESSING: "processing",
            }
            try:
                app._tray.set_state(state_names.get(self._state, "idle"))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Button handlers (called from main thread or via idle_call)
    # ------------------------------------------------------------------

    def on_record_clicked(self) -> None:
        assert_main_thread()
        if self._state != State.IDLE:
            return

        cfg = settings.load()

        # Validate API keys
        ts = cfg.get("transcription_service", "gemini")
        ss = cfg.get("summarization_service", "gemini")
        key_missing = self._check_api_keys(cfg, ts, ss)
        if key_missing:
            self._show_error(key_missing)
            return

        # Validate audio devices
        from ..audio.devices import validate_devices
        ok, err = validate_devices()
        if not ok:
            self._show_error(f"Audio device error: {err}")
            return

        # Compute output paths
        title = self._title_entry.get_text().strip() or None
        audio, transcript, notes = output_paths(
            cfg.get("output_folder", "~/meetings"), title
        )
        self._audio_path = audio
        self._transcript_path = transcript
        self._notes_path = notes

        from ..audio.recorder import Recorder, RecordingError
        self._recorder = Recorder(
            output_path=audio,
            on_tick=self._on_tick,
            on_error=self._on_recording_error,
        )
        try:
            self._recorder.start()
        except RecordingError as exc:
            self._show_error(str(exc))
            return

        self._transition(State.RECORDING)

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

        audio_path = Path(filename)
        stem = audio_path.stem
        self._audio_path = audio_path
        self._transcript_path = audio_path.parent / f"{stem}_transcript.md"
        self._notes_path = audio_path.parent / f"{stem}_notes.md"

        self._pipeline_gen += 1
        gen_id = self._pipeline_gen
        self._transition(State.PROCESSING, status="Transcribing…")
        threading.Thread(target=self._run_pipeline, args=(gen_id,), daemon=True).start()

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
        self._transition(State.PROCESSING, status="Stopping recording…")
        recorder = self._recorder
        self._recorder = None
        gen_id = self._pipeline_gen
        threading.Thread(
            target=self._stop_and_process,
            args=(recorder, gen_id),
            daemon=True,
        ).start()

    def on_cancel_processing_clicked(self) -> None:
        """Abandon the in-flight pipeline; background thread results will be ignored."""
        assert_main_thread()
        if self._state != State.PROCESSING:
            return
        self._pipeline_gen += 1  # invalidate any in-flight pipeline callbacks
        self._transition(State.IDLE, status="Processing cancelled.")
        logger.info("Pipeline cancelled by user (gen=%d)", self._pipeline_gen)

    def on_cancel_save_clicked(self) -> None:
        """Stop recording and keep the audio file, but skip transcription/summarization."""
        assert_main_thread()
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return
        self._transition(State.PROCESSING, status="Stopping recording…")
        recorder = self._recorder
        self._recorder = None
        threading.Thread(
            target=self._stop_and_save_only,
            args=(recorder,),
            daemon=True,
        ).start()

    def on_cancel_clicked(self) -> None:
        """Stop recording and delete the audio file."""
        assert_main_thread()
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return
        self._transition(State.PROCESSING, status="Cancelling…")
        recorder = self._recorder
        self._recorder = None
        threading.Thread(
            target=self._stop_and_discard,
            args=(recorder,),
            daemon=True,
        ).start()

    def _stop_and_save_only(self, recorder) -> None:
        """Background: stop recorder, keep audio, return to IDLE."""
        try:
            recorder.stop()
        except Exception as exc:
            idle_call(self._show_error, f"Failed to stop recording: {exc}")
            return
        idle_call(self._on_cancel_save_done)

    def _stop_and_discard(self, recorder) -> None:
        """Background: stop recorder, delete audio file, return to IDLE."""
        try:
            recorder.stop()
        except Exception as exc:
            idle_call(self._show_error, f"Failed to stop recording: {exc}")
            return
        if self._audio_path and self._audio_path.exists():
            try:
                self._audio_path.unlink()
            except Exception as exc:
                logger.warning("Could not delete audio file: %s", exc)
        if self._audio_path:
            try:
                self._audio_path.parent.rmdir()  # only removes if empty
            except Exception:
                pass
        idle_call(self._transition, State.IDLE)

    def _on_cancel_save_done(self) -> None:
        assert_main_thread()
        self._transition(State.IDLE, status="Recording saved (no transcription).")
        self._show_output_paths()

    def _stop_and_process(self, recorder, gen_id: int) -> None:
        """Background: stop recorder, then run AI pipeline."""
        try:
            recorder.stop()
        except Exception as exc:
            if gen_id == self._pipeline_gen:
                idle_call(self._show_error, f"Failed to stop recording: {exc}")
            return

        if gen_id != self._pipeline_gen:
            return  # cancelled while stopping

        idle_call(self._update_status, "Transcribing…")
        self._run_pipeline(gen_id)

    def _run_pipeline(self, gen_id: int) -> None:
        """Background: run transcription + summarization pipeline."""
        import traceback
        from ..processing.pipeline import Pipeline

        cfg = settings.load()
        pipeline = Pipeline(
            config=cfg,
            audio_path=self._audio_path,
            transcript_path=self._transcript_path,
            notes_path=self._notes_path,
            on_status=lambda msg: idle_call(self._update_status, msg) if gen_id == self._pipeline_gen else None,
        )
        try:
            pipeline.run()
            if gen_id == self._pipeline_gen:
                idle_call(self._on_processing_done)
        except Exception as exc:
            full = traceback.format_exc()
            logger.error("Pipeline failed:\n%s", full)
            if gen_id == self._pipeline_gen:
                idle_call(self._on_processing_error, str(exc))

    # ------------------------------------------------------------------
    # Callbacks from recorder / pipeline (may be background threads)
    # ------------------------------------------------------------------

    def _on_tick(self, elapsed: int) -> None:
        idle_call(self._update_timer, elapsed)

    def _update_timer(self, elapsed: int) -> None:
        assert_main_thread()
        self._timer_label.set_text(_format_time(elapsed))

    def _update_status(self, msg: str) -> None:
        assert_main_thread()
        self._status_label.set_text(msg)

    def _on_recording_error(self, msg: str) -> None:
        idle_call(self._transition, State.IDLE)
        idle_call(self._show_error, msg)

    def _on_processing_done(self) -> None:
        assert_main_thread()
        self._transition(State.IDLE, status="Processing complete!")
        self._show_output_paths()
        self._send_complete_notification()

    def _on_processing_error(self, msg: str) -> None:
        assert_main_thread()
        self._last_error = msg
        self._transition(State.IDLE)
        self._show_error(f"Processing failed: {msg}")

    def _show_output_paths(self) -> None:
        assert_main_thread()
        paths = []
        if self._transcript_path and self._transcript_path.exists():
            paths.append(f"Transcript: {self._transcript_path}")
        if self._notes_path and self._notes_path.exists():
            paths.append(f"Notes: {self._notes_path}")
        if self._audio_path and self._audio_path.exists():
            paths.append(f"Audio: {self._audio_path}")
        self._output_label.set_text("\n".join(paths))
        self._output_box.show_all()

    def _send_complete_notification(self) -> None:
        from .notifications import notify
        body_parts = []
        if self._transcript_path:
            body_parts.append(str(self._transcript_path))
        if self._notes_path:
            body_parts.append(str(self._notes_path))
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
        # Explicitly show the label: set_no_show_all(True) on the InfoBar
        # prevents show_all() (even on itself) from propagating to children.
        self._info_bar_label.show()

    def _on_info_bar_response(self, bar: Gtk.InfoBar, response_id: int) -> None:
        if response_id == Gtk.ResponseType.OK:
            # Retry: dismiss and re-run pipeline if we have paths
            bar.hide()
            if self._audio_path and self._audio_path.exists():
                self._pipeline_gen += 1
                gen_id = self._pipeline_gen
                self._transition(State.PROCESSING, status="Retrying…")
                threading.Thread(target=self._run_pipeline, args=(gen_id,), daemon=True).start()
        else:
            bar.hide()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_settings_clicked(self, *_) -> None:
        from .settings_dialog import SettingsDialog
        dialog = SettingsDialog(parent=self)
        dialog.run()
        dialog.destroy()
        # Re-check call detection state
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
        """Return error string if required API keys are missing, else None."""
        if ts == "gemini" and not cfg.get("gemini_api_key"):
            return "Gemini API key is not configured. Please open Settings."
        if ts == "whisper" and not cfg.get("openai_api_key"):
            return "OpenAI API key is not configured. Please open Settings."
        if ss == "gemini" and not cfg.get("gemini_api_key"):
            return "Gemini API key is not configured. Please open Settings."
        if ss == "gpt4o" and not cfg.get("openai_api_key"):
            return "OpenAI API key is not configured. Please open Settings."
        return None

    def _on_open_folder(self, *_) -> None:
        cfg = settings.load()
        folder = os.path.expanduser(cfg.get("output_folder", "~/meetings"))
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def present(self) -> None:
        """Show window and restore taskbar entry."""
        self.set_skip_taskbar_hint(False)
        super().present()

    def hide_to_tray(self) -> None:
        """Hide window and remove from taskbar — app stays alive in system tray."""
        self.set_skip_taskbar_hint(True)
        self.hide()

    def _on_delete(self, *_) -> bool:
        # Hide to tray instead of closing; recording continues uninterrupted
        self.hide_to_tray()
        return True  # suppress destruction
