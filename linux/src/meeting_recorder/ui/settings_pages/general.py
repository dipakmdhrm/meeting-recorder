"""Settings → General tab: startup, call detection, and recording options."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from meeting_recorder.config.defaults import RECORDING_QUALITIES

from ...utils.autostart import is_autostart_enabled
from .widgets import IdComboRow, make_scroll_page


class GeneralPage:
    """Builds the General tab and writes its values back on save via ``apply()``.

    ``parent_window`` is the settings window itself — the async
    ``Gtk.FileDialog`` folder picker needs a transient parent.
    """

    def __init__(self, cfg: dict, parent_window: Gtk.Window) -> None:
        self._cfg = cfg
        self._parent = parent_window
        self.widget = self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        scroll, box = make_scroll_page()

        general = Adw.PreferencesGroup(title="General")

        self._startup_switch = Adw.SwitchRow(title="Start at system startup")
        self._startup_switch.set_active(is_autostart_enabled())
        general.add(self._startup_switch)

        self._detection_switch = Adw.SwitchRow(
            title="Enable call detection",
            subtitle=(
                "Monitor running processes and audio streams to detect active "
                "calls and notify you to start recording. May produce false "
                "positives for other apps that use the microphone."
            ),
        )
        self._detection_switch.set_active(self._cfg.get("call_detection_enabled", False))
        general.add(self._detection_switch)
        box.append(general)

        recording = Adw.PreferencesGroup(title="Recording")

        self._auto_title_switch = Adw.SwitchRow(
            title="Auto-title recordings",
            subtitle="Automatically generate a short title based on meeting notes.",
        )
        self._auto_title_switch.set_active(self._cfg.get("auto_title", True))
        recording.add(self._auto_title_switch)

        self._countdown_switch = Adw.SwitchRow(
            title="Processing countdown",
            subtitle=(
                "Show a 5-second countdown after stopping a recording. Cancel "
                "during it to skip transcription and save the audio only."
            ),
        )
        self._countdown_switch.set_active(self._cfg.get("processing_countdown_enabled", False))
        recording.add(self._countdown_switch)

        q_ids = list(RECORDING_QUALITIES.keys())
        q_labels = [label for label, _ in RECORDING_QUALITIES.values()]
        self._quality_combo = IdComboRow(
            "Recording quality",
            q_ids,
            q_labels,
            self._cfg.get("recording_quality", "high"),
        )
        recording.add(self._quality_combo)

        self._folder_entry = Adw.EntryRow(title="Output folder")
        self._folder_entry.set_text(self._cfg.get("output_folder", "~/meetings"))
        browse_btn = Gtk.Button(icon_name="folder-open-symbolic")
        browse_btn.add_css_class("flat")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text("Browse…")
        browse_btn.connect("clicked", self._on_browse_folder)
        self._folder_entry.add_suffix(browse_btn)
        recording.add(self._folder_entry)
        box.append(recording)

        return scroll

    # ------------------------------------------------------------------
    # Folder picker
    # ------------------------------------------------------------------

    def _on_browse_folder(self, *_) -> None:
        # GTK4 has no blocking FileChooserDialog.run(); Gtk.FileDialog.select_folder()
        # is async — the entry is updated in the _done callback.
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Output Folder")
        current = os.path.expanduser(self._folder_entry.get_text())
        if os.path.isdir(current):
            dialog.set_initial_folder(Gio.File.new_for_path(current))

        def _done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled or dismissed
            if folder:
                self._folder_entry.set_text(folder.get_path())

        dialog.select_folder(self._parent, None, _done)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def apply(self, cfg: dict) -> None:
        """Write this tab's values into ``cfg`` (called by the dialog's save flow)."""
        cfg["output_folder"] = self._folder_entry.get_text().strip() or "~/meetings"
        cfg["recording_quality"] = self._quality_combo.get_active_id() or "high"
        cfg["call_detection_enabled"] = self._detection_switch.get_active()
        cfg["start_at_startup"] = self._startup_switch.get_active()
        cfg["auto_title"] = self._auto_title_switch.get_active()
        cfg["processing_countdown_enabled"] = self._countdown_switch.get_active()
