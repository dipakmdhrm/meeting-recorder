"""
Settings dialog — 5 tabs: General, Platform, Models, API Keys, Prompts.
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
    LITELLM_SUMMARIZATION_MODELS,
    LITELLM_TRANSCRIPTION_MODELS,
    OLLAMA_DEFAULT_HOST,
    OLLAMA_MODEL_INFO,
    OLLAMA_MODELS,
    RECORDING_QUALITIES,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_PROVIDERS,
    TRANSCRIPTION_PROVIDERS,
    WHISPER_HF_REPOS,
    WHISPER_MODEL_INFO,
    WHISPER_MODELS,
)
from ..utils.autostart import update_autostart, is_autostart_enabled, can_enable_autostart

logger = logging.getLogger(__name__)

_PROVIDER_LABELS = {
    "gemini": "Google Gemini (Direct Audio Upload)",
    "elevenlabs": "ElevenLabs Scribe v2",
    "whisper": "Whisper (local)",
    "litellm": "LiteLLM (100+ providers)",
    "claude_code": "Claude Code CLI",
}

_SUGGESTED_ENV_KEYS = [
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "ELEVENLABS_API_KEY",
    "DEEPGRAM_API_KEY",
]


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
            "Close", Gtk.ResponseType.CLOSE,
            "Save", Gtk.ResponseType.APPLY,
        )
        self.set_default_size(620, 680)

        self._cfg = settings.load()

        # Model download tracking
        self._whisper_rows: dict[str, dict] = {}
        self._ollama_rows: dict[str, dict] = {}
        self._ollama_status_label: Gtk.Label | None = None

        # API key rows
        self._api_key_rows: list[dict] = []
        self._api_keys_box: Gtk.Box | None = None
        self._api_keys_error_label: Gtk.Label | None = None

        self._build_ui()

        self.connect("response", self._on_response)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        notebook = Gtk.Notebook()
        self.get_content_area().add(notebook)

        notebook.append_page(self._build_general_tab(), Gtk.Label(label="General"))
        notebook.append_page(self._build_platform_tab(), Gtk.Label(label="Platform"))
        notebook.append_page(self._build_models_tab(), Gtk.Label(label="Model Config"))
        notebook.append_page(self._build_api_keys_tab(), Gtk.Label(label="API Keys"))
        notebook.append_page(self._build_prompts_tab(), Gtk.Label(label="Prompts"))

        self.show_all()

        # Hide litellm model rows if not selected
        self._update_litellm_visibility()

        # Kick off background status checks after show_all so labels are realized
        self._refresh_local_model_statuses()

    # ------------------------------------------------------------------
    # General tab
    # ------------------------------------------------------------------

    def _build_general_tab(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)
        grid.set_margin_start(16)
        grid.set_margin_end(16)

        row = 0

        # Transcription provider
        grid.attach(Gtk.Label(label="Transcription provider:", xalign=0), 0, row, 1, 1)
        self._ts_combo = self._make_provider_combo(
            TRANSCRIPTION_PROVIDERS, self._cfg.get("transcription_provider", "gemini")
        )
        self._ts_combo.connect("changed", lambda *_: self._update_litellm_visibility())
        grid.attach(self._ts_combo, 1, row, 1, 1)
        row += 1

        # LiteLLM transcription model (visible only when litellm selected)
        self._litellm_ts_label = Gtk.Label(label="LiteLLM transcription model:", xalign=0)
        grid.attach(self._litellm_ts_label, 0, row, 1, 1)
        self._litellm_ts_combo = Gtk.ComboBoxText.new_with_entry()
        for m in LITELLM_TRANSCRIPTION_MODELS:
            self._litellm_ts_combo.append_text(m)
        entry = self._litellm_ts_combo.get_child()
        entry.set_text(self._cfg.get("litellm_transcription_model", "groq/whisper-large-v3"))
        self._litellm_ts_combo.set_hexpand(True)
        grid.attach(self._litellm_ts_combo, 1, row, 1, 1)
        row += 1

        # Summarization provider
        grid.attach(Gtk.Label(label="Summarization provider:", xalign=0), 0, row, 1, 1)
        self._ss_combo = self._make_provider_combo(
            SUMMARIZATION_PROVIDERS, self._cfg.get("summarization_provider", "litellm")
        )
        self._ss_combo.connect("changed", lambda *_: self._update_litellm_visibility())
        grid.attach(self._ss_combo, 1, row, 1, 1)
        row += 1

        # LiteLLM summarization model (visible only when litellm selected)
        self._litellm_ss_label = Gtk.Label(label="LiteLLM summarization model:", xalign=0)
        grid.attach(self._litellm_ss_label, 0, row, 1, 1)
        self._litellm_ss_combo = Gtk.ComboBoxText.new_with_entry()
        for m in LITELLM_SUMMARIZATION_MODELS:
            self._litellm_ss_combo.append_text(m)
        entry = self._litellm_ss_combo.get_child()
        entry.set_text(self._cfg.get("litellm_summarization_model", "gemini/gemini-2.5-flash"))
        self._litellm_ss_combo.set_hexpand(True)
        grid.attach(self._litellm_ss_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        # Output folder
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

        # Recording quality
        grid.attach(Gtk.Label(label="Recording quality:", xalign=0), 0, row, 1, 1)
        self._quality_combo = Gtk.ComboBoxText()
        for key, (label, _) in RECORDING_QUALITIES.items():
            self._quality_combo.append(key, label)
        self._quality_combo.set_active_id(self._cfg.get("recording_quality", "high"))
        grid.attach(self._quality_combo, 1, row, 1, 1)
        row += 1

        # Processing timeout
        grid.attach(Gtk.Label(label="Processing timeout:", xalign=0), 0, row, 1, 1)
        self._timeout_combo = Gtk.ComboBoxText()
        current_timeout = self._cfg.get("llm_request_timeout_minutes", 5)
        for minutes in LLM_TIMEOUT_OPTIONS:
            self._timeout_combo.append(str(minutes), f"{minutes} min")
        self._timeout_combo.set_active_id(str(current_timeout))
        if self._timeout_combo.get_active_id() is None:
            self._timeout_combo.set_active_id("5")
        grid.attach(self._timeout_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        # Start at system startup
        self._startup_switch = Gtk.Switch()
        self._startup_switch.set_active(is_autostart_enabled())
        self._startup_switch.set_halign(Gtk.Align.START)

        can_enable = can_enable_autostart()
        is_enabled = is_autostart_enabled()
        self._startup_switch.set_sensitive(is_enabled or can_enable)

        grid.attach(Gtk.Label(label="Start at system startup:", xalign=0), 0, row, 1, 1)
        grid.attach(self._startup_switch, 1, row, 1, 1)
        row += 1

        if not (is_enabled or can_enable):
            note = Gtk.Label(
                label="Note: To enable autostart, the app must first be installed via install.sh."
            )
            note.set_line_wrap(True)
            note.set_xalign(0)
            grid.attach(note, 0, row, 2, 1)
            row += 1

        # Enable call detection
        self._detection_switch = Gtk.Switch()
        self._detection_switch.set_active(
            self._cfg.get("call_detection_enabled", False)
        )
        self._detection_switch.set_halign(Gtk.Align.START)

        grid.attach(Gtk.Label(label="Enable call detection:", xalign=0), 0, row, 1, 1)
        grid.attach(self._detection_switch, 1, row, 1, 1)
        row += 1

        note_detection = Gtk.Label(
            label=(
                "When enabled, the app monitors running processes and audio streams\n"
                "to detect active calls and notify you to start recording.\n\n"
                "Note: May produce false positives for other apps that use the microphone."
            )
        )
        note_detection.set_line_wrap(True)
        note_detection.set_xalign(0)
        grid.attach(note_detection, 0, row, 2, 1)

        return grid

    def _update_litellm_visibility(self) -> None:
        ts_is_litellm = (self._ts_combo.get_active_id() == "litellm")
        self._litellm_ts_label.set_visible(ts_is_litellm)
        self._litellm_ts_combo.set_visible(ts_is_litellm)

        ss_is_litellm = (self._ss_combo.get_active_id() == "litellm")
        self._litellm_ss_label.set_visible(ss_is_litellm)
        self._litellm_ss_combo.set_visible(ss_is_litellm)

    # ------------------------------------------------------------------
    # Platform tab
    # ------------------------------------------------------------------

    def _build_platform_tab(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)
        grid.set_margin_start(16)
        grid.set_margin_end(16)

        row = 0

        # Audio backend
        grid.attach(Gtk.Label(label="Audio backend:", xalign=0), 0, row, 1, 1)
        self._audio_backend_combo = Gtk.ComboBoxText()
        for name in ("pulseaudio", "pipewire"):
            self._audio_backend_combo.append(name, name)
        self._audio_backend_combo.set_active_id(
            self._cfg.get("audio_backend", "pipewire")
        )
        grid.attach(self._audio_backend_combo, 1, row, 1, 1)
        row += 1

        # Separate audio tracks
        grid.attach(Gtk.Label(label="Separate audio tracks:", xalign=0), 0, row, 1, 1)
        self._separate_tracks_switch = Gtk.Switch()
        self._separate_tracks_switch.set_active(
            self._cfg.get("separate_audio_tracks", True)
        )
        self._separate_tracks_switch.set_halign(Gtk.Align.START)
        grid.attach(self._separate_tracks_switch, 1, row, 1, 1)
        row += 1

        sep_note = Gtk.Label(
            label="Records mic and system audio as separate files for better diarization.",
            xalign=0,
        )
        sep_note.set_line_wrap(True)
        grid.attach(sep_note, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        # Screen recording
        grid.attach(Gtk.Label(label="Screen recording:", xalign=0), 0, row, 1, 1)
        self._screen_recording_switch = Gtk.Switch()
        self._screen_recording_switch.set_active(
            self._cfg.get("screen_recording", False)
        )
        self._screen_recording_switch.set_halign(Gtk.Align.START)
        self._screen_recording_switch.connect("notify::active", self._on_screen_toggle)
        grid.attach(self._screen_recording_switch, 1, row, 1, 1)
        row += 1

        # Screen recorder
        self._screen_recorder_label = Gtk.Label(label="Screen recorder:", xalign=0)
        grid.attach(self._screen_recorder_label, 0, row, 1, 1)
        self._screen_recorder_combo = Gtk.ComboBoxText()
        for name in ("gpu-screen-recorder", "none"):
            self._screen_recorder_combo.append(name, name)
        self._screen_recorder_combo.set_active_id(
            self._cfg.get("screen_recorder", "none")
        )
        grid.attach(self._screen_recorder_combo, 1, row, 1, 1)
        row += 1

        # Monitors
        self._monitors_label = Gtk.Label(label="Monitors:", xalign=0)
        grid.attach(self._monitors_label, 0, row, 1, 1)
        self._monitors_entry = Gtk.Entry()
        self._monitors_entry.set_text(self._cfg.get("monitors", "all"))
        self._monitors_entry.set_hexpand(True)
        grid.attach(self._monitors_entry, 1, row, 1, 1)
        row += 1

        # FPS
        self._fps_label = Gtk.Label(label="FPS:", xalign=0)
        grid.attach(self._fps_label, 0, row, 1, 1)
        self._fps_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        self._fps_spin.set_value(self._cfg.get("screen_fps", 30))
        grid.attach(self._fps_spin, 1, row, 1, 1)
        row += 1

        # Screen recorder availability warning
        import shutil
        self._screen_warn_label = Gtk.Label(xalign=0)
        self._screen_warn_label.set_line_wrap(True)
        self._screen_warn_label.set_no_show_all(True)
        grid.attach(self._screen_warn_label, 0, row, 2, 1)

        self._update_screen_warning()

        return grid

    def _update_screen_warning(self) -> None:
        import shutil
        active = self._screen_recording_switch.get_active()
        if active and not shutil.which("gpu-screen-recorder"):
            self._screen_warn_label.set_markup(
                '<span foreground="red">gpu-screen-recorder is not installed. '
                'Install it: <b>yay -S gpu-screen-recorder</b></span>'
            )
            self._screen_warn_label.show()
        else:
            self._screen_warn_label.hide()

    def _on_screen_toggle(self, switch, *_) -> None:
        active = switch.get_active()
        self._update_screen_warning()
        for w in (self._screen_recorder_label, self._screen_recorder_combo,
                  self._monitors_label, self._monitors_entry,
                  self._fps_label, self._fps_spin):
            w.set_sensitive(active)

    # ------------------------------------------------------------------
    # Models tab (friend's Gemini/Whisper/Ollama sections preserved)
    # ------------------------------------------------------------------

    def _build_models_tab(self) -> Gtk.Widget:
        outer_scroll = Gtk.ScrolledWindow()
        outer_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        outer_scroll.add(vbox)

        config_note = Gtk.Label(xalign=0)
        config_note.set_markup(
            "These settings apply only when the corresponding provider is selected "
            "in the <b>General</b> tab (e.g. Gemini model is used when transcription "
            "provider is set to Google Gemini, Whisper/Ollama when Whisper or LiteLLM "
            "with ollama is selected)."
        )
        config_note.set_line_wrap(True)
        vbox.pack_start(config_note, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # --- Gemini section ---
        gemini_title = Gtk.Label(xalign=0)
        gemini_title.set_markup("<b>Gemini</b>")
        vbox.pack_start(gemini_title, False, False, 0)

        gemini_grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        gemini_grid.attach(Gtk.Label(label="Model:", xalign=0), 0, 0, 1, 1)
        self._gemini_model_combo = self._make_combo(
            GEMINI_MODELS, self._cfg.get("gemini_model", GEMINI_MODELS[0])
        )
        gemini_grid.attach(self._gemini_model_combo, 1, 0, 1, 1)

        gemini_note = Gtk.Label(
            label="Controls the model for direct Gemini transcription. "
                  "LiteLLM-routed gemini/ calls use the model from the General tab.",
            xalign=0,
        )
        gemini_note.set_line_wrap(True)
        gemini_grid.attach(gemini_note, 0, 1, 2, 1)

        vbox.pack_start(gemini_grid, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # --- Whisper section ---
        whisper_title = Gtk.Label(xalign=0)
        whisper_title.set_markup("<b>Whisper</b>")
        vbox.pack_start(whisper_title, False, False, 0)

        whisper_model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        whisper_model_box.pack_start(Gtk.Label(label="Whisper model:", xalign=0), False, False, 0)
        self._whisper_model_combo = self._make_combo(
            WHISPER_MODELS, self._cfg.get("whisper_model", WHISPER_MODELS[0])
        )
        whisper_model_box.pack_start(self._whisper_model_combo, False, False, 0)
        vbox.pack_start(whisper_model_box, False, False, 0)

        whisper_note = Gtk.Label(
            label="Models are downloaded from HuggingFace and cached locally.",
            xalign=0,
        )
        whisper_note.set_line_wrap(True)
        vbox.pack_start(whisper_note, False, False, 0)

        whisper_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        for col, text in enumerate(["Model", "Size", "Note", "Status", ""]):
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(f"<b>{text}</b>")
            whisper_grid.attach(lbl, col, 0, 1, 1)

        for r, model in enumerate(WHISPER_MODELS, start=1):
            info = WHISPER_MODEL_INFO.get(model, {})
            whisper_grid.attach(Gtk.Label(label=model, xalign=0), 0, r, 1, 1)
            whisper_grid.attach(Gtk.Label(label=info.get("size", ""), xalign=0), 1, r, 1, 1)
            whisper_grid.attach(Gtk.Label(label=info.get("note", ""), xalign=0), 2, r, 1, 1)

            status_lbl = Gtk.Label(label="Checking\u2026", xalign=0)
            whisper_grid.attach(status_lbl, 3, r, 1, 1)

            btn = Gtk.Button(label="Download")
            btn.connect("clicked", lambda _b, m=model: self._start_whisper_download(m))
            whisper_grid.attach(btn, 4, r, 1, 1)

            self._whisper_rows[model] = {"status": status_lbl, "btn": btn}

        vbox.pack_start(whisper_grid, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # --- Ollama section ---
        ollama_title = Gtk.Label(xalign=0)
        ollama_title.set_markup("<b>Ollama</b>")
        vbox.pack_start(ollama_title, False, False, 0)

        ollama_config_grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        ollama_config_grid.attach(Gtk.Label(label="Ollama model:", xalign=0), 0, 0, 1, 1)
        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._ollama_model_combo = Gtk.ComboBoxText.new_with_entry()
        for m in OLLAMA_MODELS:
            self._ollama_model_combo.append_text(m)
        self._ollama_model_combo.get_child().set_text(
            self._cfg.get("ollama_model", OLLAMA_MODELS[0])
        )
        self._ollama_model_combo.set_hexpand(True)
        model_box.pack_start(self._ollama_model_combo, True, True, 0)
        self._ollama_custom_dl_btn = Gtk.Button(label="Pull Model")
        self._ollama_custom_dl_btn.connect("clicked", self._on_ollama_pull_custom)
        model_box.pack_start(self._ollama_custom_dl_btn, False, False, 0)
        ollama_config_grid.attach(model_box, 1, 0, 1, 1)

        ollama_config_grid.attach(Gtk.Label(label="Ollama host:", xalign=0), 0, 1, 1, 1)
        self._ollama_host_entry = Gtk.Entry()
        self._ollama_host_entry.set_text(
            self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST)
        )
        self._ollama_host_entry.set_hexpand(True)
        ollama_config_grid.attach(self._ollama_host_entry, 1, 1, 1, 1)

        vbox.pack_start(ollama_config_grid, False, False, 0)

        self._ollama_status_label = Gtk.Label(
            label="Checking Ollama connection\u2026", xalign=0
        )
        vbox.pack_start(self._ollama_status_label, False, False, 0)

        ollama_note = Gtk.Label(
            label="Requires Ollama to be installed and running (ollama serve). "
                  "Use ollama_chat/ prefix in LiteLLM for summarization.",
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

            status_lbl = Gtk.Label(label="Checking\u2026", xalign=0)
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
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        note = Gtk.Label(
            label="API keys are passed directly to providers and also set as environment variables.",
            xalign=0,
        )
        note.set_line_wrap(True)
        vbox.pack_start(note, False, False, 0)

        # Error label for duplicate validation
        self._api_keys_error_label = Gtk.Label(xalign=0)
        self._api_keys_error_label.set_no_show_all(True)
        vbox.pack_start(self._api_keys_error_label, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self._api_keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(self._api_keys_box)
        vbox.pack_start(scroll, True, True, 0)

        # Load existing keys
        for env_name, value in self._cfg.get("api_keys", {}).items():
            self._add_api_key_row(env_name, value)

        # Add button
        add_btn = Gtk.Button(label="Add Key")
        add_btn.connect("clicked", lambda *_: self._add_api_key_row("", ""))
        add_btn.set_halign(Gtk.Align.START)
        vbox.pack_start(add_btn, False, False, 0)

        return vbox

    def _add_api_key_row(self, env_name: str = "", value: str = "") -> None:
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Env name combo with entry
        name_combo = Gtk.ComboBoxText.new_with_entry()
        for suggestion in _SUGGESTED_ENV_KEYS:
            name_combo.append_text(suggestion)
        entry = name_combo.get_child()
        entry.set_text(env_name)
        entry.set_placeholder_text("ENV_VAR_NAME")
        name_combo.set_hexpand(True)
        row_box.pack_start(name_combo, True, True, 0)

        # Value entry (password masked)
        val_entry = Gtk.Entry()
        val_entry.set_text(value)
        val_entry.set_visibility(False)
        val_entry.set_placeholder_text("API key value")
        val_entry.set_hexpand(True)
        row_box.pack_start(val_entry, True, True, 0)

        # Show/hide toggle
        toggle_btn = Gtk.Button(label="Show")
        toggle_btn.connect("clicked", lambda b: self._toggle_key_visibility(b, val_entry))
        row_box.pack_start(toggle_btn, False, False, 0)

        # Delete button
        del_btn = Gtk.Button(label="Delete")
        row_data = {"box": row_box, "name_combo": name_combo, "val_entry": val_entry}
        del_btn.connect("clicked", lambda *_: self._remove_api_key_row(row_data))
        row_box.pack_start(del_btn, False, False, 0)

        self._api_key_rows.append(row_data)
        self._api_keys_box.pack_start(row_box, False, False, 0)
        row_box.show_all()

    def _remove_api_key_row(self, row_data: dict) -> None:
        self._api_keys_box.remove(row_data["box"])
        self._api_key_rows.remove(row_data)

    def _toggle_key_visibility(self, button: Gtk.Button, entry: Gtk.Entry) -> None:
        visible = not entry.get_visibility()
        entry.set_visibility(visible)
        button.set_label("Hide" if visible else "Show")

    def _collect_api_keys(self) -> dict[str, str] | None:
        """Collect API keys from rows. Returns None if duplicates found."""
        keys: dict[str, str] = {}
        seen_names: dict[str, int] = {}
        has_dupes = False

        for i, row_data in enumerate(self._api_key_rows):
            name_entry = row_data["name_combo"].get_child()
            name = name_entry.get_text().strip()
            value = row_data["val_entry"].get_text().strip()
            if not name:
                continue
            if name in seen_names:
                has_dupes = True
            seen_names[name] = i
            keys[name] = value

        if has_dupes:
            self._api_keys_error_label.set_text("Duplicate environment variable names found!")
            self._api_keys_error_label.show()
            return None

        self._api_keys_error_label.hide()
        return keys

    # ------------------------------------------------------------------
    # Prompts tab (friend's, preserved with note update)
    # ------------------------------------------------------------------

    def _build_prompts_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        # Transcription prompt
        ts_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ts_label = Gtk.Label(xalign=0)
        ts_label.set_markup("<b>Transcription Prompt</b>")
        ts_label.set_hexpand(True)
        ts_reset = Gtk.Button(label="Reset to default")
        ts_reset.connect("clicked", lambda *_: self._reset_prompt("transcription"))
        ts_header.pack_start(ts_label, True, True, 0)
        ts_header.pack_start(ts_reset, False, False, 0)
        vbox.pack_start(ts_header, False, False, 0)

        prompt_note = Gtk.Label(
            label="Note: Transcription prompts apply to Gemini direct provider only. "
                  "Whisper, ElevenLabs, and LiteLLM transcription providers do not use custom prompts.",
            xalign=0,
        )
        prompt_note.set_line_wrap(True)
        vbox.pack_start(prompt_note, False, False, 0)

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
        ss_label = Gtk.Label(xalign=0)
        ss_label.set_markup("<b>Summarization Prompt</b>")
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
        threading.Thread(target=self._check_whisper_statuses, daemon=True).start()
        threading.Thread(target=self._check_ollama_statuses, daemon=True).start()

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
        threading.Thread(
            target=self._do_whisper_download, args=(model,), daemon=True
        ).start()

    def _do_whisper_download(self, model: str) -> None:
        try:
            from faster_whisper import WhisperModel
            WhisperModel(model, device="cpu", compute_type="int8")
            GLib.idle_add(self._set_whisper_ready, model)
        except Exception as exc:
            GLib.idle_add(self._set_whisper_error, model, str(exc))

    def _on_ollama_pull_custom(self, *_) -> None:
        model = self._ollama_model_combo.get_child().get_text().strip()
        if not model:
            return
        host = self._ollama_host_entry.get_text().strip() or OLLAMA_DEFAULT_HOST
        self._ollama_custom_dl_btn.set_label("Pulling\u2026")
        self._ollama_custom_dl_btn.set_sensitive(False)
        threading.Thread(
            target=self._do_ollama_pull_custom, args=(model, host), daemon=True
        ).start()

    def _do_ollama_pull_custom(self, model: str, host: str) -> None:
        """Fetch model info then pull. Updates button on completion."""
        try:
            # Try to pull — Ollama will download if not present
            payload = json.dumps({"name": model, "stream": True}).encode()
            req = urllib.request.Request(
                f"{host}/api/pull", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=None) as resp:
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    try:
                        data = json.loads(line.decode())
                    except json.JSONDecodeError:
                        continue
                    status = data.get("status", "")
                    total = data.get("total", 0)
                    completed = data.get("completed", 0)
                    if total and completed:
                        pct = int(completed / total * 100)
                        status = f"{status} {pct}%"
                    GLib.idle_add(self._set_custom_pull_status, status)
                    if data.get("status") == "success":
                        GLib.idle_add(self._set_custom_pull_done, "Ready")
                        return
            GLib.idle_add(self._set_custom_pull_done, "Ready")
        except Exception as exc:
            GLib.idle_add(self._set_custom_pull_done, f"Error: {str(exc)[:40]}")

    def _set_custom_pull_status(self, text: str) -> bool:
        self._ollama_custom_dl_btn.set_label(text[:25])
        return False

    def _set_custom_pull_done(self, text: str) -> bool:
        self._ollama_custom_dl_btn.set_label("Pull Model")
        self._ollama_custom_dl_btn.set_sensitive(True)
        if self._ollama_status_label:
            self._ollama_status_label.set_text(text)
        return False

    def _start_ollama_download(self, model: str, host: str) -> None:
        row = self._ollama_rows.get(model)
        if row:
            row["status"].set_text("Starting\u2026")
            row["btn"].set_sensitive(False)
        threading.Thread(
            target=self._do_ollama_download, args=(model, host), daemon=True
        ).start()

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
    # Helpers
    # ------------------------------------------------------------------

    def _make_provider_combo(self, items: list[str], active: str) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for item in items:
            combo.append(item, _PROVIDER_LABELS.get(item, item))
        combo.set_active_id(active)
        if combo.get_active_id() is None and items:
            combo.set_active(0)
        return combo

    def _make_combo(self, items: list[str], active: str) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for item in items:
            combo.append(item, item)
        combo.set_active_id(active)
        if combo.get_active_id() is None and items:
            combo.set_active(0)
        return combo

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

    def _on_response(self, dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id == Gtk.ResponseType.APPLY:
            self._save()
            self.stop_emission_by_name("response")  # prevent dialog close

    def _save(self) -> None:
        # Collect and validate API keys first
        api_keys = self._collect_api_keys()
        if api_keys is None:
            return  # duplicates found, save blocked

        cfg = settings.load()

        # General tab
        cfg["transcription_provider"] = self._ts_combo.get_active_id() or "gemini"
        cfg["summarization_provider"] = self._ss_combo.get_active_id() or "litellm"
        cfg["litellm_transcription_model"] = self._litellm_ts_combo.get_child().get_text().strip() or "groq/whisper-large-v3"
        cfg["litellm_summarization_model"] = self._litellm_ss_combo.get_child().get_text().strip() or "gemini/gemini-2.5-flash"
        cfg["output_folder"] = self._folder_entry.get_text().strip() or "~/meetings"
        cfg["recording_quality"] = self._quality_combo.get_active_id() or "high"
        cfg["llm_request_timeout_minutes"] = int(self._timeout_combo.get_active_id() or "5")
        cfg["call_detection_enabled"] = self._detection_switch.get_active()
        cfg["start_at_startup"] = self._startup_switch.get_active()

        # Platform tab
        cfg["audio_backend"] = self._audio_backend_combo.get_active_id() or "pipewire"
        cfg["separate_audio_tracks"] = self._separate_tracks_switch.get_active()
        cfg["screen_recording"] = self._screen_recording_switch.get_active()
        cfg["screen_recorder"] = self._screen_recorder_combo.get_active_id() or "none"
        cfg["monitors"] = self._monitors_entry.get_text().strip() or "all"
        cfg["screen_fps"] = int(self._fps_spin.get_value())

        # Models tab
        cfg["gemini_model"] = self._gemini_model_combo.get_active_id() or GEMINI_MODELS[0]
        cfg["whisper_model"] = self._whisper_model_combo.get_active_id() or WHISPER_MODELS[0]
        cfg["ollama_model"] = self._ollama_model_combo.get_child().get_text().strip() or OLLAMA_MODELS[0]
        cfg["ollama_host"] = self._ollama_host_entry.get_text().strip() or OLLAMA_DEFAULT_HOST

        # API Keys
        cfg["api_keys"] = api_keys

        # Prompts
        ts_buf = self._ts_prompt_view.get_buffer()
        ts_text = ts_buf.get_text(ts_buf.get_start_iter(), ts_buf.get_end_iter(), False).strip()
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
            settings.inject_api_keys(cfg)
            update_autostart(cfg["start_at_startup"])
            self._flash_saved()
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)

    def _flash_saved(self) -> None:
        btn = self.get_widget_for_response(Gtk.ResponseType.APPLY)
        if btn:
            btn.set_label("Saved!")
            btn.set_sensitive(False)
            GLib.timeout_add(1200, self._reset_save_btn, btn)

    def _reset_save_btn(self, btn: Gtk.Widget) -> bool:
        btn.set_label("Save")
        btn.set_sensitive(True)
        return False
