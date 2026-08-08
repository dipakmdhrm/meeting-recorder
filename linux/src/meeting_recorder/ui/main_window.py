"""
The recorder window — a thin client of the daemon Engine (daemon/UI split).

The window no longer owns the recording lifecycle, job queue, or pipeline: those
live in the GTK-free daemon. This window renders a Snapshot fetched over D-Bus
(``EngineProxy``) and kept fresh by SnapshotChanged signals, and forwards button
clicks back to the engine. Errors and the "recording saved" output arrive as
Error/Output signals. File/meeting selection (which needs GTK dialogs) happens
here, then the resolved paths are handed to the engine to process.
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
from ..core.window_close import CLOSE_HIDE, resolve_close_action
from ..core.wire import Snapshot, snapshot_from_json
from ..utils.gtk_compat import remove_all_children
from ..utils.recording_import import resolve_existing_recording_target
from .jobs_panel import JobsPanel
from .meeting_explorer import MeetingExplorer

logger = logging.getLogger(__name__)

__all__ = ["MainWindow"]


def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _icon_label_button(icon_name: str, label: str) -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_child(Adw.ButtonContent(icon_name=icon_name, label=label))
    return btn


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, engine, **kwargs) -> None:
        super().__init__(title="Meeting Recorder", **kwargs)
        self.set_default_size(1100, 760)
        self.set_resizable(True)

        # The daemon-side engine, reached over D-Bus. This window only renders
        # its snapshots and forwards commands.
        self._engine = engine
        self._recording_mode: str = "headphones"
        self._snapshot = Snapshot()

        self._jobs_panel = JobsPanel(
            on_cancel=lambda jv: self._engine.cancel_job(jv.job_id),
            on_retry=lambda jv: self._engine.retry_job(jv.job_id),
            on_open_folder=self._on_open_job_folder,
            on_dismiss=lambda jv: self._engine.dismiss_job(jv.job_id),
        )

        self._build_ui()
        # Paint the current daemon state immediately, then live-update on signals.
        self.apply_snapshot_json(self._engine.get_snapshot())
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        toolbar_view = Adw.ToolbarView()
        self._toast_overlay.set_child(toolbar_view)

        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)
        header.pack_end(self._build_gear_menu_button())
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self._stack)

        # View 1: Recorder
        recorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        recorder_box.set_margin_top(24)
        recorder_box.set_margin_bottom(24)
        recorder_box.set_margin_start(12)
        recorder_box.set_margin_end(12)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self._timer_label = Gtk.Label(label="00:00")
        self._timer_label.add_css_class("timer-label")
        self._timer_label.set_attributes(self._make_timer_attrs())
        vbox.append(self._timer_label)

        self._status_label = Gtk.Label(label="")
        self._status_label.set_wrap(True)
        self._status_label.set_xalign(0.5)
        self._status_label.add_css_class("dim-label")
        vbox.append(self._status_label)

        title_group = Adw.PreferencesGroup()
        self._title_row = Adw.EntryRow(title="Title (optional)")
        title_group.add(self._title_row)
        self._title_entry = self._title_row
        vbox.append(title_group)

        self._button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=False
        )
        self._button_box.set_halign(Gtk.Align.CENTER)
        vbox.append(self._button_box)

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

        vbox.append(self._jobs_panel.widget)
        recorder_box.append(vbox)

        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_child(recorder_box)
        recorder_scroll = Gtk.ScrolledWindow()
        recorder_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        recorder_scroll.set_child(clamp)

        self._stack.add_titled_with_icon(
            recorder_scroll, "recorder", "Record", "media-record-symbolic"
        )

        # View 2: Meeting Explorer
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
    # Snapshot rendering (state lives in the daemon)
    # ------------------------------------------------------------------

    def apply_snapshot_json(self, payload: str) -> None:
        """Signal handler: parse a daemon snapshot and render it."""
        self._apply_snapshot(snapshot_from_json(payload))

    def _apply_snapshot(self, snap: Snapshot) -> None:
        self._snapshot = snap
        self._update_ui()
        self._jobs_panel.render(snap.jobs)

    def _update_ui(self) -> None:
        remove_all_children(self._button_box)
        snap = self._snapshot
        state = snap.state

        self._timer_label.set_text(_format_time(snap.elapsed))

        if state == "idle":
            self._timer_label.set_text("00:00")
            self._status_label.set_text(snap.status or "Ready to record")
            self._title_entry.set_sensitive(True)

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

        elif state == "recording":
            self._status_label.set_text(snap.status or "Recording…")
            self._title_entry.set_sensitive(False)
            self._output_box.set_visible(False)

            pause_btn = _icon_label_button("media-playback-pause-symbolic", "Pause")
            pause_btn.connect("clicked", lambda *_: self.on_pause_clicked())
            pause_btn.add_css_class("pill")
            self._button_box.append(pause_btn)
            self._append_stop_cancel_buttons()

        elif state == "paused":
            self._status_label.set_text(snap.status or "Paused")
            self._title_entry.set_sensitive(False)

            resume_btn = _icon_label_button("media-playback-start-symbolic", "Resume")
            resume_btn.connect("clicked", lambda *_: self.on_resume_clicked())
            resume_btn.add_css_class("suggested-action")
            resume_btn.add_css_class("pill")
            self._button_box.append(resume_btn)
            self._append_stop_cancel_buttons()

        elif state == "countdown":
            self._status_label.set_text(snap.status or "")
            self._title_entry.set_sensitive(False)
            self._output_box.set_visible(False)

            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", lambda *_: self.on_cancel_countdown_clicked())
            cancel_btn.add_css_class("destructive-action")
            cancel_btn.add_css_class("pill")
            self._button_box.append(cancel_btn)

    def _append_stop_cancel_buttons(self) -> None:
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

    def show_output(self, text: str) -> None:
        """Engine Output signal: recording saved without transcription."""
        self._output_label.set_text(text)
        self._output_box.set_visible(True)

    # ------------------------------------------------------------------
    # Button handlers -> engine
    # ------------------------------------------------------------------

    def on_record_headphones_clicked(self) -> None:
        self._recording_mode = "headphones"
        self._start_recording()

    def on_record_speaker_clicked(self) -> None:
        self._recording_mode = "speaker"
        self._start_recording()

    def _start_recording(self) -> None:
        title = self._title_entry.get_text().strip()
        self._engine.set_title(title)
        self._engine.start_recording(self._recording_mode)

    def on_pause_clicked(self) -> None:
        self._engine.pause()

    def on_resume_clicked(self) -> None:
        self._engine.resume()

    def on_stop_clicked(self) -> None:
        self._engine.stop()

    def on_cancel_countdown_clicked(self) -> None:
        self._engine.cancel_countdown()

    def on_cancel_save_clicked(self) -> None:
        self._engine.cancel_save()

    def on_cancel_clicked(self) -> None:
        self._engine.cancel()

    # ------------------------------------------------------------------
    # Use Existing / Summarize (GTK selection here, processing in the daemon)
    # ------------------------------------------------------------------

    def on_use_existing_clicked(self) -> None:
        cfg = settings.load()
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
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        if not gfile:
            return
        filename = gfile.get_path()
        if not filename:
            return

        output_folder = Path(os.path.expanduser(cfg.get("output_folder", "~/meetings")))
        reuse_in_place, paths = resolve_existing_recording_target(Path(filename), output_folder)
        if reuse_in_place:
            audio_path, transcript_path, notes_path = paths
        else:
            audio_path, transcript_path, notes_path = output_paths(
                cfg.get("output_folder", "~/meetings")
            )
            try:
                shutil.copy(filename, audio_path)
            except Exception as e:
                self.show_error(f"Failed to copy audio file: {e}")
                return

        self._engine.import_existing(
            str(audio_path), str(transcript_path), str(notes_path), Path(filename).name
        )

    def _on_summarize_from_explorer(self, meeting: Meeting) -> None:
        audio_path = find_audio_file(meeting.path)
        if not audio_path:
            self.show_error("No audio file found in meeting folder.")
            return
        transcript_path = meeting.path / "transcript.md"
        notes_path = meeting.path / "notes.md"
        err = self._engine.summarize_meeting(
            str(audio_path), str(transcript_path), str(notes_path), meeting.time_label
        )
        if err:
            self.show_error(err)
            return
        self._stack.set_visible_child_name("recorder")

    def _on_open_job_folder(self, job_view) -> None:
        folder = getattr(job_view, "audio_dir", "") or ""
        if folder:
            try:
                subprocess.Popen(["xdg-open", folder])
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Error display (Engine Error signal)
    # ------------------------------------------------------------------

    def show_error(self, msg: str) -> None:
        logger.error("UI error shown: %s", msg)
        if error_presentation(msg) == "dialog":
            alert = Gtk.AlertDialog()
            alert.set_modal(True)
            alert.set_message("Meeting Recorder")
            alert.set_detail(msg)
            alert.set_buttons(["OK"])
            alert.show(self)
        else:
            toast = Adw.Toast(title=msg)
            toast.set_timeout(0)
            self._toast_overlay.add_toast(toast)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _build_gear_menu_button(self) -> Gtk.MenuButton:
        """The header-bar gear: a menu with Preferences (settings) and About."""
        menu = Gio.Menu()
        menu.append("Preferences", "gear.preferences")
        menu.append("About Meeting Recorder", "gear.about")

        actions = Gio.SimpleActionGroup()
        preferences = Gio.SimpleAction.new("preferences", None)
        preferences.connect("activate", self._on_settings_clicked)
        actions.add_action(preferences)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about_clicked)
        actions.add_action(about)
        self.insert_action_group("gear", actions)

        button = Gtk.MenuButton(icon_name="preferences-system-symbolic")
        button.set_tooltip_text("Menu")
        button.set_menu_model(menu)
        return button

    def _on_settings_clicked(self, *_) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            parent=self, on_saved=self._after_settings_saved, engine=self._engine
        )
        dialog.present()

    def _on_about_clicked(self, *_) -> None:
        from ..core import app_info

        version = app_info.resolve_version()
        if hasattr(Adw, "AboutDialog"):
            about = Adw.AboutDialog(
                application_name=app_info.APP_NAME,
                application_icon="meeting-recorder",
                developer_name=app_info.DEVELOPER_NAME,
                comments=app_info.DESCRIPTION,
                website=app_info.REPOSITORY,
                issue_url=app_info.ISSUE_URL,
                developers=app_info.DEVELOPERS,
                copyright=app_info.COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
            )
            if version:
                about.set_version(version)
            about.present(self)
        elif hasattr(Adw, "AboutWindow"):
            about = Adw.AboutWindow(
                transient_for=self,
                application_name=app_info.APP_NAME,
                application_icon="meeting-recorder",
                developer_name=app_info.DEVELOPER_NAME,
                comments=app_info.DESCRIPTION,
                website=app_info.REPOSITORY,
                issue_url=app_info.ISSUE_URL,
                developers=app_info.DEVELOPERS,
                copyright=app_info.COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
            )
            if version:
                about.set_version(version)
            about.present()
        else:
            about = Gtk.AboutDialog(
                transient_for=self,
                modal=True,
                program_name=app_info.APP_NAME,
                logo_icon_name="meeting-recorder",
                comments=app_info.DESCRIPTION,
                website=app_info.REPOSITORY,
                authors=app_info.DEVELOPERS,
                copyright=app_info.COPYRIGHT,
                license_type=Gtk.License.MIT_X11,
            )
            if version:
                about.set_version(version)
            about.present()

    def _after_settings_saved(self) -> None:
        # The daemon owns call detection; ask it to reconcile with the new config.
        self._engine.reload_config()

    # ------------------------------------------------------------------
    # Helpers / window lifecycle
    # ------------------------------------------------------------------

    def _on_open_folder(self, *_) -> None:
        folder = self._engine.output_folder() or os.path.expanduser(
            settings.load().get("output_folder", "~/meetings")
        )
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def present_window(self) -> None:
        """Show, raise and focus the window — the Engine PresentWindow signal
        and the tray (left-click / the "Open" menu item) route here.

        GTK4 removed set_skip_taskbar_hint(), present_with_time() and
        Gtk.get_current_event_time(); focus is now mediated by the compositor,
        so present() is the supported path (left-click-to-focus is best-effort
        on Wayland/GNOME)."""
        self.set_visible(True)
        self.unminimize()
        self.present()

    def open_use_existing(self) -> None:
        """Engine OpenUseExisting signal: present the window and pop the picker."""
        self.present_window()
        self.on_use_existing_clicked()

    def _on_close_request(self, *_) -> bool:
        # By default closing exits this process; the daemon keeps running
        # (recording/jobs continue) and will respawn a window on demand. When the
        # user opts into "keep window in memory", hide instead of exit so the
        # process stays resident and the next Open is an instant present.
        if resolve_close_action(settings.load()) == CLOSE_HIDE:
            self.set_visible(False)
            return True  # veto the destroy; the window lives on, hidden
        return False
