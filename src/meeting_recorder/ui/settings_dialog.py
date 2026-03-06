"""
Implements a tabbed settings dialog for the application using Gtk. It allows users to configure transcription and summarization services (specifically Google Gemini), manage API keys, set output directories, choose recording quality, enable call detection, and customize AI prompts.
"""

from __future__ import annotations

import logging
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ..config import settings
from ..config.defaults import (
    GEMINI_MODELS,
    GEMINI_TRANSCRIPTION_PROMPT,
    RECORDING_QUALITIES,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SERVICES,
    TRANSCRIPTION_SERVICES,
)

logger = logging.getLogger(__name__)

_SERVICE_LABELS = {
    "gemini": "Google Gemini",
}


class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(
            title="Settings",
            transient_for=parent,
            modal=True,
            use_header_bar=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(520, 560)

        self._cfg = settings.load()
        self._ok_btn = self.get_widget_for_response(Gtk.ResponseType.OK)
        self._build_ui()
        self._validate()

        self.connect("response", self._on_response)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        notebook = Gtk.Notebook()
        self.get_content_area().add(notebook)

        notebook.append_page(self._build_services_tab(), Gtk.Label(label="Services"))
        notebook.append_page(self._build_api_keys_tab(), Gtk.Label(label="API Keys"))
        notebook.append_page(self._build_output_tab(), Gtk.Label(label="Output"))
        notebook.append_page(self._build_detection_tab(), Gtk.Label(label="Detection"))
        notebook.append_page(self._build_prompts_tab(), Gtk.Label(label="Prompts"))

        self.show_all()

    # ------------------------------------------------------------------
    # Services tab
    # ------------------------------------------------------------------

    def _build_services_tab(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)
        grid.set_margin_start(16)
        grid.set_margin_end(16)

        row = 0

        # Transcription service
        grid.attach(Gtk.Label(label="Transcription:", xalign=0), 0, row, 1, 1)
        self._ts_combo = self._make_combo(
            TRANSCRIPTION_SERVICES, self._cfg.get("transcription_service", "gemini")
        )
        self._ts_combo.connect("changed", self._on_service_changed)
        grid.attach(self._ts_combo, 1, row, 1, 1)
        row += 1

        # Gemini model (transcription side)
        grid.attach(Gtk.Label(label="Gemini model:", xalign=0), 0, row, 1, 1)
        self._gemini_model_combo = self._make_combo(
            GEMINI_MODELS, self._cfg.get("gemini_model", GEMINI_MODELS[0])
        )
        self._gemini_model_combo.connect("changed", self._on_gemini_model_changed)
        grid.attach(self._gemini_model_combo, 1, row, 1, 1)
        row += 1

        # Summarization service
        grid.attach(Gtk.Label(label="Summarization:", xalign=0), 0, row, 1, 1)
        self._ss_combo = self._make_combo(
            SUMMARIZATION_SERVICES, self._cfg.get("summarization_service", "gemini")
        )
        self._ss_combo.connect("changed", self._on_service_changed)
        grid.attach(self._ss_combo, 1, row, 1, 1)
        row += 1

        # Warning label for invalid combos
        self._service_warning = Gtk.Label(label="")
        self._service_warning.get_style_context().add_class("error")
        self._service_warning.set_line_wrap(True)
        self._service_warning.set_xalign(0)
        grid.attach(self._service_warning, 0, row, 2, 1)

        return grid

    # ------------------------------------------------------------------
    # API Keys tab
    # ------------------------------------------------------------------

    def _build_api_keys_tab(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)
        grid.set_margin_start(16)
        grid.set_margin_end(16)

        row = 0

        grid.attach(Gtk.Label(label="Gemini API key:", xalign=0), 0, row, 1, 1)
        self._gemini_key_entry = Gtk.Entry()
        self._gemini_key_entry.set_visibility(False)
        self._gemini_key_entry.set_text(self._cfg.get("gemini_api_key", ""))
        self._gemini_key_entry.set_hexpand(True)
        self._gemini_key_entry.connect("changed", lambda *_: self._validate())
        grid.attach(self._gemini_key_entry, 1, row, 1, 1)
        row += 1

        # Key warning
        self._key_warning = Gtk.Label(label="")
        self._key_warning.get_style_context().add_class("error")
        self._key_warning.set_line_wrap(True)
        self._key_warning.set_xalign(0)
        grid.attach(self._key_warning, 0, row, 2, 1)

        return grid

    # ------------------------------------------------------------------
    # Output tab
    # ------------------------------------------------------------------

    def _build_output_tab(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)
        grid.set_margin_start(16)
        grid.set_margin_end(16)

        row = 0
        grid.attach(Gtk.Label(label="Output folder:", xalign=0), 0, row, 1, 1)

        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._folder_entry = Gtk.Entry()
        self._folder_entry.set_text(self._cfg.get("output_folder", "~/meetings"))
        self._folder_entry.set_hexpand(True)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse_folder)
        folder_box.pack_start(self._folder_entry, True, True, 0)
        folder_box.pack_start(browse_btn, False, False, 0)
        grid.attach(folder_box, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Recording quality:", xalign=0), 0, row, 1, 1)
        self._quality_combo = Gtk.ComboBoxText()
        for key, (label, _) in RECORDING_QUALITIES.items():
            self._quality_combo.append(key, label)
        self._quality_combo.set_active_id(self._cfg.get("recording_quality", "high"))
        grid.attach(self._quality_combo, 1, row, 1, 1)
        row += 1

        return grid

    # ------------------------------------------------------------------
    # Detection tab
    # ------------------------------------------------------------------

    def _build_detection_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        self._detection_switch = Gtk.Switch()
        self._detection_switch.set_active(
            self._cfg.get("call_detection_enabled", False)
        )
        self._detection_switch.set_halign(Gtk.Align.START)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox.pack_start(Gtk.Label(label="Enable call detection:"), False, False, 0)
        hbox.pack_start(self._detection_switch, False, False, 0)
        box.pack_start(hbox, False, False, 0)

        note = Gtk.Label(
            label=(
                "When enabled, the app monitors running processes and audio streams\n"
                "to detect active calls and notify you to start recording.\n\n"
                "Note: May produce false positives for other apps that use the microphone."
            )
        )
        note.set_line_wrap(True)
        note.set_xalign(0)
        box.pack_start(note, False, False, 0)

        return box

    # ------------------------------------------------------------------
    # Prompts tab
    # ------------------------------------------------------------------

    def _build_prompts_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        # Transcription prompt
        ts_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ts_label = Gtk.Label(label="Transcription prompt:", xalign=0)
        ts_label.set_hexpand(True)
        ts_reset = Gtk.Button(label="Reset to default")
        ts_reset.connect("clicked", lambda *_: self._reset_prompt("transcription"))
        ts_header.pack_start(ts_label, True, True, 0)
        ts_header.pack_start(ts_reset, False, False, 0)
        vbox.pack_start(ts_header, False, False, 0)

        self._ts_prompt_view = Gtk.TextView()
        self._ts_prompt_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self._ts_prompt_view.set_monospace(True)
        stored_ts = self._cfg.get("transcription_prompt") or GEMINI_TRANSCRIPTION_PROMPT
        self._ts_prompt_view.get_buffer().set_text(stored_ts)
        ts_scroll = Gtk.ScrolledWindow()
        ts_scroll.set_min_content_height(180)
        ts_scroll.set_vexpand(True)
        ts_scroll.add(self._ts_prompt_view)
        vbox.pack_start(ts_scroll, True, True, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # Summarization prompt
        ss_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ss_label = Gtk.Label(label="Summarization prompt:", xalign=0)
        ss_label.set_hexpand(True)
        ss_reset = Gtk.Button(label="Reset to default")
        ss_reset.connect("clicked", lambda *_: self._reset_prompt("summarization"))
        ss_header.pack_start(ss_label, True, True, 0)
        ss_header.pack_start(ss_reset, False, False, 0)
        vbox.pack_start(ss_header, False, False, 0)

        self._ss_prompt_view = Gtk.TextView()
        self._ss_prompt_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self._ss_prompt_view.set_monospace(True)
        stored_ss = self._cfg.get("summarization_prompt") or SUMMARIZATION_PROMPT
        self._ss_prompt_view.get_buffer().set_text(stored_ss)
        ss_scroll = Gtk.ScrolledWindow()
        ss_scroll.set_min_content_height(180)
        ss_scroll.set_vexpand(True)
        ss_scroll.add(self._ss_prompt_view)
        vbox.pack_start(ss_scroll, True, True, 0)

        return vbox

    def _reset_prompt(self, which: str) -> None:
        if which == "transcription":
            self._ts_prompt_view.get_buffer().set_text(GEMINI_TRANSCRIPTION_PROMPT)
        else:
            self._ss_prompt_view.get_buffer().set_text(SUMMARIZATION_PROMPT)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_combo(self, items: list[str], active: str) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for item in items:
            combo.append(item, _SERVICE_LABELS.get(item, item))
        combo.set_active_id(active)
        if combo.get_active_id() is None and items:
            combo.set_active(0)
        return combo

    def _on_service_changed(self, *_) -> None:
        self._validate()

    def _on_gemini_model_changed(self, combo) -> None:
        self._cfg["gemini_model"] = combo.get_active_id() or GEMINI_MODELS[0]

    def _on_browse_folder(self, *_) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Select Output Folder",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        current = os.path.expanduser(self._folder_entry.get_text())
        if os.path.isdir(current):
            dialog.set_current_folder(current)
        if dialog.run() == Gtk.ResponseType.OK:
            self._folder_entry.set_text(dialog.get_filename())
        dialog.destroy()

    def _validate(self) -> None:
        """Validate and enable/disable OK button."""
        ts = self._ts_combo.get_active_id() or "gemini"
        ss = self._ss_combo.get_active_id() or "gemini"
        gemini_key = self._gemini_key_entry.get_text().strip()

        warnings = []

        # Missing key checks
        if ts == "gemini" and not gemini_key:
            warnings.append("Gemini API key is required for Gemini transcription.")
        if ss == "gemini" and not gemini_key:
            warnings.append("Gemini API key is required for Gemini summarization.")

        if warnings:
            self._service_warning.set_text("\n".join(warnings))
            self._key_warning.set_text("\n".join(warnings))
            self._ok_btn.set_sensitive(False)
        else:
            self._service_warning.set_text("")
            self._key_warning.set_text("")
            self._ok_btn.set_sensitive(True)

    def _on_response(self, dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id == Gtk.ResponseType.OK:
            self._save()

    def _save(self) -> None:
        cfg = settings.load()
        cfg["transcription_service"] = self._ts_combo.get_active_id() or "gemini"
        cfg["summarization_service"] = self._ss_combo.get_active_id() or "gemini"
        cfg["gemini_api_key"] = self._gemini_key_entry.get_text().strip()
        cfg["gemini_model"] = self._gemini_model_combo.get_active_id() or GEMINI_MODELS[0]
        cfg["output_folder"] = self._folder_entry.get_text().strip() or "~/meetings"
        cfg["recording_quality"] = self._quality_combo.get_active_id() or "high"
        cfg["call_detection_enabled"] = self._detection_switch.get_active()

        ts_buf = self._ts_prompt_view.get_buffer()
        ts_text = ts_buf.get_text(ts_buf.get_start_iter(), ts_buf.get_end_iter(), False).strip()
        # Store empty string if the user hasn't changed from the default — the provider
        # will fall back to the built-in constant, so future default updates take effect.
        cfg["transcription_prompt"] = "" if ts_text == GEMINI_TRANSCRIPTION_PROMPT.strip() else ts_text

        ss_buf = self._ss_prompt_view.get_buffer()
        ss_text = ss_buf.get_text(ss_buf.get_start_iter(), ss_buf.get_end_iter(), False).strip()
        cfg["summarization_prompt"] = "" if ss_text == SUMMARIZATION_PROMPT.strip() else ss_text
        try:
            settings.save(cfg)
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)
