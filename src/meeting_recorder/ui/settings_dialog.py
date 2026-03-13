"""
Implements a tabbed settings dialog for the application using Gtk. It allows users to configure
transcription and summarization services (Gemini, Whisper, Ollama), manage API keys, set output
directories, choose recording quality, enable call detection, and customize AI prompts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from ..config import settings
from ..config.defaults import (
    GEMINI_MODELS,
    GEMINI_TRANSCRIPTION_PROMPT,
    LLM_TIMEOUT_OPTIONS,
    OLLAMA_DEFAULT_HOST,
    OLLAMA_MODEL_INFO,
    OLLAMA_MODELS,
    RECORDING_QUALITIES,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SERVICES,
    TRANSCRIPTION_SERVICES,
    WHISPER_HF_REPOS,
    WHISPER_MODEL_INFO,
    WHISPER_MODELS,
)
from ..utils.autostart import update_autostart, is_autostart_enabled, can_enable_autostart

logger = logging.getLogger(__name__)

_SERVICE_LABELS = {
    "gemini":  "Google Gemini",
    "whisper": "Whisper (local)",
    "ollama":  "Ollama (local)",
}


# ---------------------------------------------------------------------------
# Module-level helpers (no UI dependencies)
# ---------------------------------------------------------------------------

def _is_whisper_cached(model_name: str) -> bool:
    repo = WHISPER_HF_REPOS.get(model_name, f"Systran/faster-whisper-{model_name}")
    cache_dir = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / f"models--{repo.replace('/', '--')}"
    )
    return cache_dir.exists()


def _get_ollama_installed_models(host: str) -> list[str] | None:
    """Returns list of installed model names, or None if ollama unreachable."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return None


def _ollama_model_installed(model: str, installed: list[str]) -> bool:
    return any(n == model or n.startswith(f"{model}:") for n in installed)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

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
        self.set_default_size(580, 620)

        self._cfg = settings.load()
        self._ok_btn = self.get_widget_for_response(Gtk.ResponseType.OK)

        # Dicts keyed by model name: {"status": Gtk.Label, "btn": Gtk.Button}
        self._whisper_rows: dict[str, dict] = {}
        self._ollama_rows: dict[str, dict] = {}
        self._ollama_status_label: Gtk.Label | None = None

        self._build_ui()
        self._validate()

        self.connect("response", self._on_response)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        notebook = Gtk.Notebook()
        self.get_content_area().add(notebook)

        notebook.append_page(self._build_services_tab(),     Gtk.Label(label="Services"))
        notebook.append_page(self._build_local_models_tab(), Gtk.Label(label="Local Models"))
        notebook.append_page(self._build_api_keys_tab(),     Gtk.Label(label="API Keys"))
        notebook.append_page(self._build_output_tab(),       Gtk.Label(label="Output"))
        notebook.append_page(self._build_general_tab(),      Gtk.Label(label="General"))
        notebook.append_page(self._build_prompts_tab(),      Gtk.Label(label="Prompts"))

        self.show_all()

        # Kick off background status checks after show_all so labels are realized
        self._refresh_local_model_statuses()

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

        # Gemini model
        self._gemini_model_label = Gtk.Label(label="Gemini model:", xalign=0)
        grid.attach(self._gemini_model_label, 0, row, 1, 1)
        self._gemini_model_combo = self._make_combo(
            GEMINI_MODELS, self._cfg.get("gemini_model", GEMINI_MODELS[0])
        )
        self._gemini_model_combo.connect("changed", self._on_gemini_model_changed)
        grid.attach(self._gemini_model_combo, 1, row, 1, 1)
        row += 1

        # LLM request timeout (Gemini)
        self._timeout_label = Gtk.Label(label="LLM request timeout:", xalign=0)
        grid.attach(self._timeout_label, 0, row, 1, 1)
        self._timeout_combo = Gtk.ComboBoxText()
        current_timeout = self._cfg.get("llm_request_timeout_minutes", 3)
        for minutes in LLM_TIMEOUT_OPTIONS:
            self._timeout_combo.append(str(minutes), f"{minutes} min")
        self._timeout_combo.set_active_id(str(current_timeout))
        if self._timeout_combo.get_active_id() is None:
            self._timeout_combo.set_active_id("3")
        grid.attach(self._timeout_combo, 1, row, 1, 1)
        row += 1

        # Whisper model
        self._whisper_model_label = Gtk.Label(label="Whisper model:", xalign=0)
        grid.attach(self._whisper_model_label, 0, row, 1, 1)
        self._whisper_model_combo = self._make_combo(
            WHISPER_MODELS, self._cfg.get("whisper_model", WHISPER_MODELS[0])
        )
        grid.attach(self._whisper_model_combo, 1, row, 1, 1)
        row += 1

        # Summarization service
        grid.attach(Gtk.Label(label="Summarization:", xalign=0), 0, row, 1, 1)
        self._ss_combo = self._make_combo(
            SUMMARIZATION_SERVICES, self._cfg.get("summarization_service", "gemini")
        )
        self._ss_combo.connect("changed", self._on_service_changed)
        grid.attach(self._ss_combo, 1, row, 1, 1)
        row += 1

        # Ollama model
        self._ollama_model_label = Gtk.Label(label="Ollama model:", xalign=0)
        grid.attach(self._ollama_model_label, 0, row, 1, 1)
        self._ollama_model_combo = self._make_combo(
            OLLAMA_MODELS, self._cfg.get("ollama_model", OLLAMA_MODELS[0])
        )
        grid.attach(self._ollama_model_combo, 1, row, 1, 1)
        row += 1

        # Ollama host
        self._ollama_host_label = Gtk.Label(label="Ollama host:", xalign=0)
        grid.attach(self._ollama_host_label, 0, row, 1, 1)
        self._ollama_host_entry = Gtk.Entry()
        self._ollama_host_entry.set_text(
            self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST)
        )
        self._ollama_host_entry.set_hexpand(True)
        grid.attach(self._ollama_host_entry, 1, row, 1, 1)
        row += 1

        # Warning label
        self._service_warning = Gtk.Label(label="")
        self._service_warning.get_style_context().add_class("error")
        self._service_warning.set_line_wrap(True)
        self._service_warning.set_xalign(0)
        grid.attach(self._service_warning, 0, row, 2, 1)

        self._update_service_row_sensitivity()

        return grid

    # ------------------------------------------------------------------
    # Local Models tab
    # ------------------------------------------------------------------

    def _build_local_models_tab(self) -> Gtk.Widget:
        outer_scroll = Gtk.ScrolledWindow()
        outer_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        outer_scroll.add(vbox)

        # --- Whisper section ---
        whisper_title = Gtk.Label(xalign=0)
        whisper_title.set_markup("<b>Whisper (Speech-to-Text)</b>")
        vbox.pack_start(whisper_title, False, False, 0)

        whisper_note = Gtk.Label(
            label="Models are downloaded from HuggingFace and cached locally.",
            xalign=0,
        )
        whisper_note.set_line_wrap(True)
        vbox.pack_start(whisper_note, False, False, 0)

        whisper_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        # Header row
        for col, text in enumerate(["Model", "Size", "Note", "Status", ""]):
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(f"<b>{text}</b>")
            whisper_grid.attach(lbl, col, 0, 1, 1)

        for r, model in enumerate(WHISPER_MODELS, start=1):
            info = WHISPER_MODEL_INFO.get(model, {})
            whisper_grid.attach(Gtk.Label(label=model, xalign=0), 0, r, 1, 1)
            whisper_grid.attach(Gtk.Label(label=info.get("size", ""), xalign=0), 1, r, 1, 1)
            whisper_grid.attach(Gtk.Label(label=info.get("note", ""), xalign=0), 2, r, 1, 1)

            status_lbl = Gtk.Label(label="Checking…", xalign=0)
            whisper_grid.attach(status_lbl, 3, r, 1, 1)

            btn = Gtk.Button(label="Download")
            btn.connect("clicked", lambda _b, m=model: self._start_whisper_download(m))
            whisper_grid.attach(btn, 4, r, 1, 1)

            self._whisper_rows[model] = {"status": status_lbl, "btn": btn}

        vbox.pack_start(whisper_grid, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # --- Ollama section ---
        ollama_title = Gtk.Label(xalign=0)
        ollama_title.set_markup("<b>Ollama (Summarization)</b>")
        vbox.pack_start(ollama_title, False, False, 0)

        self._ollama_status_label = Gtk.Label(
            label="Checking Ollama connection…", xalign=0
        )
        vbox.pack_start(self._ollama_status_label, False, False, 0)

        ollama_note = Gtk.Label(
            label="Requires Ollama to be installed and running (ollama serve).",
            xalign=0,
        )
        ollama_note.set_line_wrap(True)
        vbox.pack_start(ollama_note, False, False, 0)

        ollama_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        for col, text in enumerate(["Model", "Size", "Note", "Status", ""]):
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(f"<b>{text}</b>")
            ollama_grid.attach(lbl, col, 0, 1, 1)

        for r, model in enumerate(OLLAMA_MODELS, start=1):
            info = OLLAMA_MODEL_INFO.get(model, {})
            ollama_grid.attach(Gtk.Label(label=model, xalign=0), 0, r, 1, 1)
            ollama_grid.attach(Gtk.Label(label=info.get("size", ""), xalign=0), 1, r, 1, 1)
            ollama_grid.attach(Gtk.Label(label=info.get("note", ""), xalign=0), 2, r, 1, 1)

            status_lbl = Gtk.Label(label="Checking…", xalign=0)
            ollama_grid.attach(status_lbl, 3, r, 1, 1)

            btn = Gtk.Button(label="Download")
            btn.connect(
                "clicked",
                lambda _b, m=model: self._start_ollama_download(
                    m, self._ollama_host_entry.get_text().strip()
                ),
            )
            ollama_grid.attach(btn, 4, r, 1, 1)

            self._ollama_rows[model] = {"status": status_lbl, "btn": btn}

        vbox.pack_start(ollama_grid, False, False, 0)

        return outer_scroll

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
        browse_btn = Gtk.Button(label="Browse\u2026")
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
    # General tab
    # ------------------------------------------------------------------

    def _build_general_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        # Autostart
        self._startup_switch = Gtk.Switch()
        self._startup_switch.set_active(is_autostart_enabled())
        self._startup_switch.set_halign(Gtk.Align.START)

        can_enable = can_enable_autostart()
        is_enabled = is_autostart_enabled()
        self._startup_switch.set_sensitive(is_enabled or can_enable)

        hbox1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox1.pack_start(Gtk.Label(label="Start at system startup:"), False, False, 0)
        hbox1.pack_start(self._startup_switch, False, False, 0)
        box.pack_start(hbox1, False, False, 0)

        if not (is_enabled or can_enable):
            note_no_autostart = Gtk.Label(
                label="Note: To enable autostart, the app must first be installed via install.sh."
            )
            note_no_autostart.set_line_wrap(True)
            note_no_autostart.set_xalign(0)
            box.pack_start(note_no_autostart, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 4)

        # Call detection
        self._detection_switch = Gtk.Switch()
        self._detection_switch.set_active(
            self._cfg.get("call_detection_enabled", False)
        )
        self._detection_switch.set_halign(Gtk.Align.START)

        hbox2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox2.pack_start(Gtk.Label(label="Enable call detection:"), False, False, 0)
        hbox2.pack_start(self._detection_switch, False, False, 0)
        box.pack_start(hbox2, False, False, 0)

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

        whisper_note = Gtk.Label(
            label="Note: Transcription prompts apply to Gemini only. Whisper does not use prompts.",
            xalign=0,
        )
        whisper_note.set_line_wrap(True)
        vbox.pack_start(whisper_note, False, False, 0)

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
    # Background status checks
    # ------------------------------------------------------------------

    def _refresh_local_model_statuses(self) -> None:
        t = threading.Thread(target=self._check_whisper_statuses, daemon=True)
        t.start()
        t2 = threading.Thread(target=self._check_ollama_statuses, daemon=True)
        t2.start()

    def _check_whisper_statuses(self) -> None:
        for model in WHISPER_MODELS:
            if _is_whisper_cached(model):
                GLib.idle_add(self._set_whisper_ready, model)
            else:
                GLib.idle_add(self._set_whisper_not_downloaded, model)

    def _check_ollama_statuses(self) -> None:
        host = self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST)
        installed = _get_ollama_installed_models(host)
        if installed is None:
            GLib.idle_add(self._set_ollama_unreachable)
            return
        GLib.idle_add(self._set_ollama_reachable)
        for model in OLLAMA_MODELS:
            if _ollama_model_installed(model, installed):
                GLib.idle_add(self._set_ollama_ready, model)
            else:
                GLib.idle_add(self._set_ollama_not_downloaded, model)

    # ------------------------------------------------------------------
    # Download handlers
    # ------------------------------------------------------------------

    def _start_whisper_download(self, model: str) -> None:
        row = self._whisper_rows.get(model)
        if row:
            row["status"].set_text("Downloading\u2026")
            row["btn"].set_sensitive(False)
        t = threading.Thread(
            target=self._do_whisper_download, args=(model,), daemon=True
        )
        t.start()

    def _do_whisper_download(self, model: str) -> None:
        try:
            from faster_whisper import WhisperModel
            WhisperModel(model, device="cpu", compute_type="int8")
            GLib.idle_add(self._set_whisper_ready, model)
        except Exception as exc:
            GLib.idle_add(self._set_whisper_error, model, str(exc))

    def _start_ollama_download(self, model: str, host: str) -> None:
        row = self._ollama_rows.get(model)
        if row:
            row["status"].set_text("Starting\u2026")
            row["btn"].set_sensitive(False)
        t = threading.Thread(
            target=self._do_ollama_download, args=(model, host), daemon=True
        )
        t.start()

    def _do_ollama_download(self, model: str, host: str) -> None:
        payload = json.dumps({"name": model, "stream": True}).encode()
        req = urllib.request.Request(
            f"{host}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    try:
                        data = json.loads(line.decode())
                    except json.JSONDecodeError:
                        continue
                    status_text = data.get("status", "")
                    total = data.get("total", 0)
                    completed = data.get("completed", 0)
                    if total and completed:
                        pct = int(completed / total * 100)
                        status_text = f"{status_text} {pct}%"
                    GLib.idle_add(self._set_ollama_progress, model, status_text)
                    if data.get("status") == "success":
                        GLib.idle_add(self._set_ollama_ready, model)
                        return
            # If loop ended without "success", do a final status check
            host_val = self._ollama_host_entry.get_text().strip()
            installed = _get_ollama_installed_models(host_val)
            if installed and _ollama_model_installed(model, installed):
                GLib.idle_add(self._set_ollama_ready, model)
            else:
                GLib.idle_add(self._set_ollama_error, model, "Download may have failed")
        except Exception as exc:
            GLib.idle_add(self._set_ollama_error, model, str(exc))

    # ------------------------------------------------------------------
    # Row UI updaters (all called via GLib.idle_add, return False)
    # ------------------------------------------------------------------

    def _set_whisper_not_downloaded(self, model: str) -> bool:
        row = self._whisper_rows.get(model)
        if row:
            row["status"].set_text("Not downloaded")
            row["btn"].set_label("Download")
            row["btn"].set_sensitive(True)
        return False

    def _set_whisper_ready(self, model: str) -> bool:
        row = self._whisper_rows.get(model)
        if row:
            row["status"].set_text("Ready")
            row["btn"].set_label("Downloaded")
            row["btn"].set_sensitive(False)
        return False

    def _set_whisper_error(self, model: str, msg: str) -> bool:
        row = self._whisper_rows.get(model)
        if row:
            row["status"].set_text(msg[:60])
            row["btn"].set_label("Retry")
            row["btn"].set_sensitive(True)
        return False

    def _set_ollama_unreachable(self) -> bool:
        if self._ollama_status_label:
            self._ollama_status_label.set_text(
                "Ollama not reachable. Start it with: ollama serve"
            )
        for model in OLLAMA_MODELS:
            row = self._ollama_rows.get(model)
            if row:
                row["status"].set_text("Ollama offline")
                row["btn"].set_sensitive(True)
        return False

    def _set_ollama_reachable(self) -> bool:
        if self._ollama_status_label:
            self._ollama_status_label.set_text("Ollama is running.")
        return False

    def _set_ollama_not_downloaded(self, model: str) -> bool:
        row = self._ollama_rows.get(model)
        if row:
            row["status"].set_text("Not downloaded")
            row["btn"].set_label("Download")
            row["btn"].set_sensitive(True)
        return False

    def _set_ollama_progress(self, model: str, text: str) -> bool:
        row = self._ollama_rows.get(model)
        if row:
            row["status"].set_text(text[:40])
            row["btn"].set_sensitive(False)
        return False

    def _set_ollama_ready(self, model: str) -> bool:
        row = self._ollama_rows.get(model)
        if row:
            row["status"].set_text("Ready")
            row["btn"].set_label("Downloaded")
            row["btn"].set_sensitive(False)
        return False

    def _set_ollama_error(self, model: str, msg: str) -> bool:
        row = self._ollama_rows.get(model)
        if row:
            row["status"].set_text(msg[:60])
            row["btn"].set_label("Retry")
            row["btn"].set_sensitive(True)
        return False

    # ------------------------------------------------------------------
    # Service row sensitivity
    # ------------------------------------------------------------------

    def _update_service_row_sensitivity(self) -> None:
        ts = self._ts_combo.get_active_id() or "gemini"
        ss = self._ss_combo.get_active_id() or "gemini"
        uses_gemini = ts == "gemini" or ss == "gemini"
        uses_whisper = ts == "whisper"
        uses_ollama = ss == "ollama"

        self._gemini_model_label.set_sensitive(uses_gemini)
        self._gemini_model_combo.set_sensitive(uses_gemini)
        self._timeout_label.set_sensitive(uses_gemini)
        self._timeout_combo.set_sensitive(uses_gemini)
        self._whisper_model_label.set_sensitive(uses_whisper)
        self._whisper_model_combo.set_sensitive(uses_whisper)
        self._ollama_model_label.set_sensitive(uses_ollama)
        self._ollama_model_combo.set_sensitive(uses_ollama)
        self._ollama_host_label.set_sensitive(uses_ollama)
        self._ollama_host_entry.set_sensitive(uses_ollama)

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
        self._update_service_row_sensitivity()
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
        if ts == "gemini" and not gemini_key:
            warnings.append("Gemini API key is required for Gemini transcription.")
        if ss == "gemini" and not gemini_key:
            warnings.append("Gemini API key is required for Gemini summarization.")

        # Deduplicate (e.g. both ts and ss are gemini with no key)
        warnings = list(dict.fromkeys(warnings))

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
        cfg["llm_request_timeout_minutes"] = int(self._timeout_combo.get_active_id() or "3")
        cfg["whisper_model"] = self._whisper_model_combo.get_active_id() or WHISPER_MODELS[0]
        cfg["ollama_model"] = self._ollama_model_combo.get_active_id() or OLLAMA_MODELS[0]
        cfg["ollama_host"] = self._ollama_host_entry.get_text().strip() or OLLAMA_DEFAULT_HOST
        cfg["output_folder"] = self._folder_entry.get_text().strip() or "~/meetings"
        cfg["recording_quality"] = self._quality_combo.get_active_id() or "high"
        cfg["call_detection_enabled"] = self._detection_switch.get_active()
        cfg["start_at_startup"] = self._startup_switch.get_active()

        ts_buf = self._ts_prompt_view.get_buffer()
        ts_text = ts_buf.get_text(ts_buf.get_start_iter(), ts_buf.get_end_iter(), False).strip()
        # Store empty string if the user hasn't changed from the default — the provider
        # will fall back to the built-in constant, so future default updates take effect.
        cfg["transcription_prompt"] = (
            "" if ts_text == GEMINI_TRANSCRIPTION_PROMPT.strip() else ts_text
        )

        ss_buf = self._ss_prompt_view.get_buffer()
        ss_text = ss_buf.get_text(ss_buf.get_start_iter(), ss_buf.get_end_iter(), False).strip()
        cfg["summarization_prompt"] = (
            "" if ss_text == SUMMARIZATION_PROMPT.strip() else ss_text
        )

        try:
            settings.save(cfg)
            update_autostart(cfg["start_at_startup"])
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)
