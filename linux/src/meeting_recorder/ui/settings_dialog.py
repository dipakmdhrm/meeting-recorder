"""
Tabbed settings dialog (General / Models / Prompts).

All I/O is injected so the dialog can be exercised in tests without a real
filesystem, network, or GTK main loop:

    dialog = SettingsDialog(
        parent,
        store=FakeStore({}),
        whisper_checker=WhisperStatusChecker(cache_root=tmp_path),
        ollama_client=OllamaClient(http_open=fake_http),
        ollama_installer=OllamaInstaller(which_fn=lambda _: None, shell_fn=lambda _: 0),
        cuda_installer=CudaInstaller(which_fn=lambda _: None, shell_fn=lambda _: 0),
        dispatcher=lambda fn, *a: fn(*a),   # synchronous — no GTK loop needed
    )
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk

from meeting_recorder.config import settings
from meeting_recorder.config.defaults import (
    GEMINI_MODELS,
    GEMINI_TRANSCRIPTION_PROMPT,
    LLM_TIMEOUT_OPTIONS,
    OLLAMA_DEFAULT_HOST,
    OLLAMA_MODEL_INFO,
    OLLAMA_MODELS,
    RECORDING_QUALITIES,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SERVICES,
    TITLE_PROMPT,
    TRANSCRIPTION_SERVICES,
    WHISPER_CPP_BACKENDS,
    WHISPER_CPP_MODEL_INFO,
    WHISPER_CPP_MODELS,
    WHISPER_MODEL_INFO,
    WHISPER_MODELS,
)
from ..services.ollama_service import OllamaClient
from ..services.system_installer import (
    CudaInstaller,
    OllamaInstaller,
    RocmInstaller,
    WhisperEngineInstaller,
    detect_gpu_vendor,
)
from ..services.whisper_cpp_service import (
    WhisperCppBuilder,
    WhisperCppModelDownloader,
    WhisperCppStatusChecker,
    detect_gpu_backend,
)
from ..services.whisper_service import WhisperDownloader, WhisperStatusChecker
from ..utils.autostart import is_autostart_enabled, update_autostart
from .model_row_grid import ModelRowGrid
from .settings_visibility import compute_section_visibility

logger = logging.getLogger(__name__)

_SERVICE_LABELS = {
    "gemini":      "Google Gemini",
    "whisper":     "Whisper (local)",
    "whisper_cpp": "whisper.cpp (local, GPU)",
    "ollama":      "Ollama (local)",
}

_PROMPT_DEFAULTS = {
    "transcription": GEMINI_TRANSCRIPTION_PROMPT,
    "summarization": SUMMARIZATION_PROMPT,
    "title":         TITLE_PROMPT,
}


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class SettingsDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        *,
        store=settings,
        whisper_checker: WhisperStatusChecker | None = None,
        whisper_downloader: WhisperDownloader | None = None,
        ollama_client: OllamaClient | None = None,
        ollama_installer: OllamaInstaller | None = None,
        cuda_installer: CudaInstaller | None = None,
        rocm_installer: RocmInstaller | None = None,
        whisper_engine_installer: WhisperEngineInstaller | None = None,
        whisper_cpp_builder: WhisperCppBuilder | None = None,
        whisper_cpp_checker: WhisperCppStatusChecker | None = None,
        whisper_cpp_downloader: WhisperCppModelDownloader | None = None,
        gpu_vendor: str | None = None,
        dispatcher: Callable = GLib.idle_add,
        on_saved: Callable | None = None,
    ) -> None:
        super().__init__(
            title="Settings",
            transient_for=parent,
            modal=True,
            use_header_bar=True,
        )
        # GTK4 removed the Gtk.STOCK_* constants — use plain labels.
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.OK)
        self.set_default_size(580, 620)

        # Called after a successful save (GTK4 has no blocking run(), so the
        # caller can't act on the result inline — see MainWindow._on_settings_clicked).
        self._on_saved = on_saved

        # --- injected dependencies (real defaults for production) ---
        self._store           = store
        self._cfg             = store.load()
        self._dispatch        = dispatcher
        self._whisper_checker = whisper_checker    or WhisperStatusChecker()
        self._whisper_dl      = whisper_downloader or WhisperDownloader()
        self._ollama          = ollama_client      or OllamaClient()
        self._ollama_inst     = ollama_installer   or OllamaInstaller()
        self._cuda_inst       = cuda_installer     or CudaInstaller()
        self._rocm_inst       = rocm_installer     or RocmInstaller()
        # Maps a detected GPU vendor to the runtime installer that serves it.
        self._gpu_installers  = {"nvidia": self._cuda_inst, "amd": self._rocm_inst}
        self._whisper_eng_inst = whisper_engine_installer or WhisperEngineInstaller()
        self._wcpp_builder    = whisper_cpp_builder    or WhisperCppBuilder()
        self._wcpp_checker    = whisper_cpp_checker    or WhisperCppStatusChecker()
        self._wcpp_dl         = whisper_cpp_downloader or WhisperCppModelDownloader()
        self._gpu_vendor      = gpu_vendor if gpu_vendor is not None else detect_gpu_vendor()

        # --- widget references populated during build ---
        self._whisper_grid: ModelRowGrid | None = None
        self._whisper_model_combo: Gtk.ComboBoxText | None = None
        self._wcpp_grid: ModelRowGrid | None = None
        self._wcpp_model_combo: Gtk.ComboBoxText | None = None
        self._wcpp_backend_combo: Gtk.ComboBoxText | None = None
        self._ollama_grid:  ModelRowGrid | None = None
        self._ollama_status_label: Gtk.Label | None = None
        self._prompt_views: dict[str, Gtk.TextView] = {}

        self._build_ui()
        self.connect("response", self._on_response)

    # ------------------------------------------------------------------
    # Top-level layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)
        self.get_content_area().append(notebook)
        notebook.append_page(self._build_general_tab(), Gtk.Label(label="General"))
        notebook.append_page(self._build_models_tab(),  Gtk.Label(label="Models"))
        notebook.append_page(self._build_prompts_tab(), Gtk.Label(label="Prompts"))
        # GTK4 widgets are visible by default; the dialog is shown by the caller
        # via present().
        # Apply initial visibility based on selected services.
        self._update_models_visibility()
        # Kick off background status checks.
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

        self._startup_switch = Gtk.Switch()
        self._startup_switch.set_active(is_autostart_enabled())
        self._startup_switch.set_halign(Gtk.Align.START)
        grid.attach(Gtk.Label(label="Start at system startup:", xalign=0), 0, row, 1, 1)
        grid.attach(self._startup_switch, 1, row, 1, 1)
        row += 1

        self._detection_switch = Gtk.Switch()
        self._detection_switch.set_active(self._cfg.get("call_detection_enabled", False))
        self._detection_switch.set_halign(Gtk.Align.START)
        grid.attach(Gtk.Label(label="Enable call detection:", xalign=0), 0, row, 1, 1)
        grid.attach(self._detection_switch, 1, row, 1, 1)
        row += 1

        note = Gtk.Label(
            label=(
                "When enabled, the app monitors running processes and audio streams\n"
                "to detect active calls and notify you to start recording.\n\n"
                "Note: May produce false positives for other apps that use the microphone."
            )
        )
        note.set_wrap(True)
        note.set_xalign(0)
        grid.attach(note, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        self._auto_title_switch = Gtk.Switch()
        self._auto_title_switch.set_active(self._cfg.get("auto_title", True))
        self._auto_title_switch.set_halign(Gtk.Align.START)
        grid.attach(Gtk.Label(label="Auto-title recordings:", xalign=0), 0, row, 1, 1)
        grid.attach(self._auto_title_switch, 1, row, 1, 1)
        row += 1

        auto_note = Gtk.Label(label="Automatically generate a short title based on meeting notes.")
        auto_note.set_wrap(True)
        auto_note.set_xalign(0)
        grid.attach(auto_note, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        self._countdown_switch = Gtk.Switch()
        self._countdown_switch.set_active(self._cfg.get("processing_countdown_enabled", False))
        self._countdown_switch.set_halign(Gtk.Align.START)
        grid.attach(Gtk.Label(label="Processing countdown:", xalign=0), 0, row, 1, 1)
        grid.attach(self._countdown_switch, 1, row, 1, 1)
        row += 1

        countdown_note = Gtk.Label(
            label=(
                "Show a 5-second countdown after stopping a recording.\n"
                "Cancel during the countdown to skip transcription and save the audio only."
            )
        )
        countdown_note.set_wrap(True)
        countdown_note.set_xalign(0)
        grid.attach(countdown_note, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label="Output folder:", xalign=0), 0, row, 1, 1)
        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._folder_entry = Gtk.Entry()
        self._folder_entry.set_text(self._cfg.get("output_folder", "~/meetings"))
        self._folder_entry.set_hexpand(True)
        browse_btn = Gtk.Button(label="Browse\u2026")
        browse_btn.connect("clicked", self._on_browse_folder)
        folder_box.append(self._folder_entry)
        folder_box.append(browse_btn)
        grid.attach(folder_box, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Recording quality:", xalign=0), 0, row, 1, 1)
        self._quality_combo = Gtk.ComboBoxText()
        for key, (label, _) in RECORDING_QUALITIES.items():
            self._quality_combo.append(key, label)
        self._quality_combo.set_active_id(self._cfg.get("recording_quality", "high"))
        grid.attach(self._quality_combo, 1, row, 1, 1)

        return grid

    # ------------------------------------------------------------------
    # Models tab — composed from four independent section builders
    # ------------------------------------------------------------------

    def _build_models_tab(self) -> Gtk.Widget:
        outer_scroll = Gtk.ScrolledWindow()
        outer_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        outer_scroll.set_child(vbox)

        # --- Services selectors ---
        services_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        services_grid.attach(Gtk.Label(label="Transcription service:", xalign=0), 0, 0, 1, 1)
        self._ts_combo = self._make_combo(
            TRANSCRIPTION_SERVICES, self._cfg.get("transcription_service", "gemini")
        )
        self._ts_combo.connect("changed", lambda *_: self._update_models_visibility())
        services_grid.attach(self._ts_combo, 1, 0, 1, 1)

        services_grid.attach(Gtk.Label(label="Summarization service:", xalign=0), 0, 1, 1, 1)
        self._ss_combo = self._make_combo(
            SUMMARIZATION_SERVICES, self._cfg.get("summarization_service", "gemini")
        )
        self._ss_combo.connect("changed", lambda *_: self._update_models_visibility())
        services_grid.attach(self._ss_combo, 1, 1, 1, 1)

        vbox.append(services_grid)
        vbox.append(Gtk.Separator())

        # --- Model sections (stored for show/hide), each with a trailing separator ---
        self._gemini_section_widget = self._build_gemini_section()
        self._gemini_sep = Gtk.Separator()
        self._whisper_section_widget = self._build_whisper_section()
        self._whisper_sep = Gtk.Separator()
        self._wcpp_section_widget = self._build_whisper_cpp_section()
        self._wcpp_sep = Gtk.Separator()
        self._ollama_section_widget = self._build_ollama_section()
        self._ollama_sep = Gtk.Separator()
        self._gpu_section_widget = self._build_gpu_section()

        for widget, sep in [
            (self._gemini_section_widget,  self._gemini_sep),
            (self._whisper_section_widget, self._whisper_sep),
            (self._wcpp_section_widget,    self._wcpp_sep),
            (self._ollama_section_widget,  self._ollama_sep),
            (self._gpu_section_widget,     None),
        ]:
            vbox.append(widget)
            if sep is not None:
                vbox.append(sep)

        return outer_scroll

    def _update_models_visibility(self) -> None:
        ts = self._ts_combo.get_active_id() or "gemini"
        ss = self._ss_combo.get_active_id() or "gemini"
        vis = compute_section_visibility(ts, ss)

        self._gemini_section_widget.set_visible(vis["gemini"])
        self._gemini_sep.set_visible(vis["gemini_sep"])
        self._whisper_section_widget.set_visible(vis["whisper"])
        self._whisper_sep.set_visible(vis["whisper_sep"])
        self._wcpp_section_widget.set_visible(vis["wcpp"])
        self._wcpp_sep.set_visible(vis["wcpp_sep"])
        self._ollama_section_widget.set_visible(vis["ollama"])
        self._ollama_sep.set_visible(vis["ollama_sep"])
        self._gpu_section_widget.set_visible(vis["gpu"])

    def _build_gemini_section(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>Gemini</b>")
        vbox.append(title)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        grid.attach(Gtk.Label(label="API key:", xalign=0), 0, 0, 1, 1)
        self._gemini_key_entry = Gtk.Entry()
        self._gemini_key_entry.set_text(self._cfg.get("gemini_api_key", ""))
        self._gemini_key_entry.set_hexpand(True)
        grid.attach(self._gemini_key_entry, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Transcription model:", xalign=0), 0, 1, 1, 1)
        self._gemini_ts_model_combo = self._make_combo(
            GEMINI_MODELS, self._cfg.get("gemini_transcription_model", GEMINI_MODELS[0])
        )
        grid.attach(self._gemini_ts_model_combo, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Summarization model:", xalign=0), 0, 2, 1, 1)
        self._gemini_ss_model_combo = self._make_combo(
            GEMINI_MODELS, self._cfg.get("gemini_summarization_model", GEMINI_MODELS[0])
        )
        grid.attach(self._gemini_ss_model_combo, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Processing timeout:", xalign=0), 0, 3, 1, 1)
        self._timeout_combo = Gtk.ComboBoxText()
        current_timeout = self._cfg.get("llm_request_timeout_minutes", 3)
        for minutes in LLM_TIMEOUT_OPTIONS:
            self._timeout_combo.append(str(minutes), f"{minutes} min")
        self._timeout_combo.set_active_id(str(current_timeout))
        if self._timeout_combo.get_active_id() is None:
            self._timeout_combo.set_active_id("3")
        grid.attach(self._timeout_combo, 1, 3, 1, 1)

        vbox.append(grid)
        return vbox

    def _build_whisper_section(self) -> Gtk.Widget:
        self._whisper_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>Whisper</b>")
        self._whisper_vbox.append(title)

        # The faster-whisper engine is opt-in (not in the base install). Show an
        # install button until it is present, then the model download UI.
        if not self._whisper_eng_inst.is_available():
            self._build_whisper_installer()
        else:
            self._build_whisper_ui()

        return self._whisper_vbox

    def _build_whisper_installer(self) -> None:
        self._whisper_install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._whisper_vbox.append(self._whisper_install_box)

        info = Gtk.Label(
            xalign=0,
            label=(
                "The Whisper engine (faster-whisper) is not installed. It enables local "
                "transcription on NVIDIA GPUs or CPU. Install it to use this option."
            ),
        )
        info.set_wrap(True)
        self._whisper_install_box.append(info)

        self._whisper_install_button = Gtk.Button(label="Install Whisper engine")
        self._whisper_install_button.connect("clicked", self._on_install_whisper_engine)
        self._whisper_install_box.append(self._whisper_install_button)

    def _build_whisper_ui(self) -> None:
        self._whisper_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._whisper_vbox.append(self._whisper_config_box)

        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        model_box.append(Gtk.Label(label="Whisper model:", xalign=0))
        self._whisper_model_combo = self._make_combo(
            WHISPER_MODELS, self._cfg.get("whisper_model", WHISPER_MODELS[0])
        )
        model_box.append(self._whisper_model_combo)
        self._whisper_config_box.append(model_box)

        note = Gtk.Label(
            label="Models are downloaded from HuggingFace and cached locally.",
            xalign=0,
        )
        note.set_wrap(True)
        self._whisper_config_box.append(note)

        self._whisper_grid = ModelRowGrid(
            WHISPER_MODELS, WHISPER_MODEL_INFO, self._start_whisper_download
        )
        self._whisper_config_box.append(self._whisper_grid)

    def _build_whisper_cpp_section(self) -> Gtk.Widget:
        self._wcpp_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>whisper.cpp (GPU-accelerated)</b>")
        self._wcpp_vbox.append(title)

        # Backend selector is always available — it drives both the build and
        # the runtime acceleration; "auto" detects the GPU.
        backend_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        detected = detect_gpu_backend()
        backend_box.append(
            Gtk.Label(label="Acceleration backend:", xalign=0)
        )
        self._wcpp_backend_combo = self._make_combo(
            WHISPER_CPP_BACKENDS, self._cfg.get("whisper_cpp_backend", "auto")
        )
        backend_box.append(self._wcpp_backend_combo)
        backend_box.append(
            Gtk.Label(label=f"(detected: {detected})", xalign=0)
        )
        self._wcpp_vbox.append(backend_box)

        # The engine is built from source on opt-in. Show a build button until
        # the binary exists, then the model download UI.
        if not self._wcpp_builder.is_built():
            self._build_wcpp_installer()
        else:
            self._build_wcpp_ui()

        return self._wcpp_vbox

    def _build_wcpp_installer(self) -> None:
        self._wcpp_install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._wcpp_vbox.append(self._wcpp_install_box)

        info = Gtk.Label(
            xalign=0,
            label=(
                "whisper.cpp is not built yet. Building it compiles a local transcription "
                "engine that can use AMD (ROCm/Vulkan), Apple (Metal), NVIDIA, or CPU. "
                "This installs a build toolchain and may take a few minutes."
            ),
        )
        info.set_wrap(True)
        self._wcpp_install_box.append(info)

        self._wcpp_install_button = Gtk.Button(label="Build whisper.cpp engine")
        self._wcpp_install_button.connect("clicked", self._on_build_whisper_cpp)
        self._wcpp_install_box.append(self._wcpp_install_button)

    def _build_wcpp_ui(self) -> None:
        self._wcpp_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._wcpp_vbox.append(self._wcpp_config_box)

        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        model_box.append(Gtk.Label(label="Model:", xalign=0))
        self._wcpp_model_combo = self._make_combo(
            WHISPER_CPP_MODELS, self._cfg.get("whisper_cpp_model", WHISPER_CPP_MODELS[0])
        )
        model_box.append(self._wcpp_model_combo)
        self._wcpp_config_box.append(model_box)

        note = Gtk.Label(
            label="GGML models are downloaded from HuggingFace and cached locally.",
            xalign=0,
        )
        note.set_wrap(True)
        self._wcpp_config_box.append(note)

        self._wcpp_grid = ModelRowGrid(
            WHISPER_CPP_MODELS, WHISPER_CPP_MODEL_INFO, self._start_wcpp_download
        )
        self._wcpp_config_box.append(self._wcpp_grid)

    def _build_ollama_section(self) -> Gtk.Widget:
        self._ollama_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>Ollama</b>")
        self._ollama_vbox.append(title)

        if not self._ollama_inst.is_available():
            self._build_ollama_installer()
        else:
            self._build_ollama_ui()

        return self._ollama_vbox

    def _build_ollama_installer(self) -> None:
        self._ollama_install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._ollama_vbox.append(self._ollama_install_box)

        info = Gtk.Label(
            xalign=0,
            label="Ollama is not installed. It is required for local summarization.",
        )
        self._ollama_install_box.append(info)

        self._ollama_install_button = Gtk.Button(label="Install Ollama")
        self._ollama_install_button.connect("clicked", self._on_install_ollama)
        self._ollama_install_box.append(self._ollama_install_button)

    def _build_ollama_ui(self) -> None:
        self._ollama_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._ollama_vbox.append(self._ollama_config_box)

        config_grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        config_grid.attach(Gtk.Label(label="Ollama model:", xalign=0), 0, 0, 1, 1)
        self._ollama_model_combo = self._make_combo(
            OLLAMA_MODELS, self._cfg.get("ollama_model", OLLAMA_MODELS[0])
        )
        config_grid.attach(self._ollama_model_combo, 1, 0, 1, 1)

        config_grid.attach(Gtk.Label(label="Ollama host:", xalign=0), 0, 1, 1, 1)
        self._ollama_host_entry = Gtk.Entry()
        self._ollama_host_entry.set_text(self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST))
        self._ollama_host_entry.set_hexpand(True)
        config_grid.attach(self._ollama_host_entry, 1, 1, 1, 1)

        self._ollama_config_box.append(config_grid)

        self._ollama_status_label = Gtk.Label(
            label="Checking Ollama connection\u2026", xalign=0
        )
        self._ollama_config_box.append(self._ollama_status_label)

        note = Gtk.Label(
            label="Requires Ollama to be installed and running (ollama serve).",
            xalign=0,
        )
        note.set_wrap(True)
        self._ollama_config_box.append(note)

        self._ollama_grid = ModelRowGrid(
            OLLAMA_MODELS, OLLAMA_MODEL_INFO, self._start_ollama_download
        )
        self._ollama_config_box.append(self._ollama_grid)

    def _build_gpu_section(self) -> Gtk.Widget:
        self._gpu_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>GPU Acceleration</b>")
        self._gpu_vbox.append(title)

        # Pick the right accelerator UI for the detected GPU vendor.
        if self._gpu_vendor == "nvidia":
            if self._cuda_inst.is_available():
                self._gpu_installed_label(
                    "NVIDIA CUDA libraries detected. GPU acceleration is available."
                )
            else:
                self._build_gpu_installer(
                    "nvidia",
                    "An NVIDIA GPU was detected but CUDA libraries are not installed. "
                    "Install them to enable GPU-accelerated transcription.",
                    "Install CUDA Libraries",
                )
        elif self._gpu_vendor == "amd":
            if self._rocm_inst.is_available():
                self._gpu_installed_label(
                    "AMD ROCm detected. GPU acceleration is available "
                    "(use the whisper.cpp engine)."
                )
            else:
                self._build_gpu_installer(
                    "amd",
                    "An AMD GPU was detected but the ROCm runtime is not installed. "
                    "Install it to enable GPU-accelerated transcription with whisper.cpp.",
                    "Install ROCm Runtime",
                )
        elif self._gpu_vendor == "apple":
            self._gpu_installed_label(
                "Apple Silicon detected. Metal GPU acceleration is built in "
                "(use the whisper.cpp engine) — no install needed."
            )
        else:
            self._gpu_installed_label(
                "No supported GPU detected. Local transcription will run on CPU, "
                "which is slow. For fast transcription, use the Gemini service."
            )

        return self._gpu_vbox

    def _gpu_installed_label(self, text: str) -> None:
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._gpu_vbox.append(info_box)
        info = Gtk.Label(xalign=0, label=text)
        info.set_wrap(True)
        info_box.append(info)

    def _build_gpu_installer(self, vendor: str, info_text: str, button_label: str) -> None:
        self._gpu_install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._gpu_vbox.append(self._gpu_install_box)

        info = Gtk.Label(xalign=0, label=info_text)
        info.set_wrap(True)
        self._gpu_install_box.append(info)

        self._gpu_install_button = Gtk.Button(label=button_label)
        self._gpu_install_button.connect("clicked", self._on_install_gpu, vendor)
        self._gpu_install_box.append(self._gpu_install_button)

    # ------------------------------------------------------------------
    # Install handlers — Ollama
    # ------------------------------------------------------------------

    def _on_install_ollama(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Installing\u2026")
        threading.Thread(target=self._do_install_ollama, daemon=True).start()

    def _do_install_ollama(self) -> None:
        success = self._ollama_inst.install()
        self._dispatch(self._on_ollama_install_finished, success)

    def _on_ollama_install_finished(self, success: bool) -> None:
        if success and self._ollama_inst.is_available():
            # GTK4 has no Widget.destroy(); remove the install box from its parent.
            self._ollama_vbox.remove(self._ollama_install_box)
            self._build_ollama_ui()
            self._refresh_local_model_statuses()
        else:
            self._ollama_install_button.set_sensitive(True)
            self._ollama_install_button.set_label("Retry Install")

    # ------------------------------------------------------------------
    # Install handlers — Whisper engine (faster-whisper, opt-in)
    # ------------------------------------------------------------------

    def _on_install_whisper_engine(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Installing\u2026")
        threading.Thread(target=self._do_install_whisper_engine, daemon=True).start()

    def _do_install_whisper_engine(self) -> None:
        success = self._whisper_eng_inst.install()
        self._dispatch(self._on_whisper_engine_install_finished, success)

    def _on_whisper_engine_install_finished(self, success: bool) -> None:
        if success and self._whisper_eng_inst.is_available():
            self._whisper_vbox.remove(self._whisper_install_box)
            self._build_whisper_ui()
            self._refresh_local_model_statuses()
        else:
            self._whisper_install_button.set_sensitive(True)
            self._whisper_install_button.set_label("Retry Install")

    # ------------------------------------------------------------------
    # Build handler \u2014 whisper.cpp engine (built from source, opt-in)
    # ------------------------------------------------------------------

    def _on_build_whisper_cpp(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Building\u2026 (may take a few minutes)")
        backend = self._wcpp_backend_combo.get_active_id() or "auto"
        if backend == "auto":
            backend = detect_gpu_backend()
        threading.Thread(
            target=self._do_build_whisper_cpp, args=(backend,), daemon=True
        ).start()

    def _do_build_whisper_cpp(self, backend: str) -> None:
        success = self._wcpp_builder.build(backend)
        self._dispatch(self._on_whisper_cpp_build_finished, success)

    def _on_whisper_cpp_build_finished(self, success: bool) -> None:
        if success and self._wcpp_builder.is_built():
            self._wcpp_vbox.remove(self._wcpp_install_box)
            self._build_wcpp_ui()
            self._refresh_local_model_statuses()
        else:
            self._wcpp_install_button.set_sensitive(True)
            self._wcpp_install_button.set_label("Retry Build")

    def _on_install_gpu(self, button: Gtk.Button, vendor: str) -> None:
        button.set_sensitive(False)
        button.set_label("Installing\u2026")
        threading.Thread(
            target=self._do_install_gpu, args=(vendor,), daemon=True
        ).start()

    def _do_install_gpu(self, vendor: str) -> None:
        installer = self._gpu_installers.get(vendor)
        success = installer.install() if installer else False
        self._dispatch(self._on_gpu_install_finished, success, vendor)

    def _on_gpu_install_finished(self, success: bool, vendor: str) -> None:
        installer = self._gpu_installers.get(vendor)
        if success and installer is not None and installer.is_available():
            self._gpu_vbox.remove(self._gpu_install_box)
            label = (
                "NVIDIA CUDA libraries detected. GPU acceleration is available."
                if vendor == "nvidia"
                else "AMD ROCm detected. GPU acceleration is available "
                "(use the whisper.cpp engine)."
            )
            self._gpu_installed_label(label)
        else:
            self._gpu_install_button.set_sensitive(True)
            self._gpu_install_button.set_label("Retry Install")

    # ------------------------------------------------------------------
    # Prompts tab — three sections built from a single helper (DRY)
    # ------------------------------------------------------------------

    def _build_prompts_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        vbox.append(
            self._build_prompt_section(
                key="transcription",
                label="Transcription prompt:",
                note="Note: Transcription prompts apply to Gemini only. Whisper does not use prompts.",
                height=180,
            )
        )
        vbox.append(Gtk.Separator())
        vbox.append(
            self._build_prompt_section(
                key="summarization",
                label="Summarization prompt:",
                height=180,
            )
        )
        vbox.append(Gtk.Separator())
        vbox.append(
            self._build_prompt_section(
                key="title",
                label="Title prompt:",
                note=(
                    "Used for auto-titling recordings and the AI title button in the Library. "
                    "Must contain {transcript}."
                ),
                height=120,
            )
        )
        return vbox

    def _build_prompt_section(
        self,
        key: str,
        label: str,
        note: str | None = None,
        height: int = 180,
    ) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.set_hexpand(True)
        reset_btn = Gtk.Button(label="Reset to default")
        reset_btn.connect("clicked", lambda *_: self._reset_prompt(key))
        header.append(lbl)
        header.append(reset_btn)
        vbox.append(header)

        if note:
            note_lbl = Gtk.Label(label=note, xalign=0)
            note_lbl.set_wrap(True)
            vbox.append(note_lbl)

        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_monospace(True)
        stored = self._cfg.get(f"{key}_prompt") or _PROMPT_DEFAULTS[key]
        view.get_buffer().set_text(stored)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(height)
        scroll.set_vexpand(True)
        scroll.set_child(view)
        vbox.append(scroll)

        self._prompt_views[key] = view
        return vbox

    def _reset_prompt(self, key: str) -> None:
        view = self._prompt_views.get(key)
        if view and key in _PROMPT_DEFAULTS:
            view.get_buffer().set_text(_PROMPT_DEFAULTS[key])

    # ------------------------------------------------------------------
    # Background status checks
    # ------------------------------------------------------------------

    def _refresh_local_model_statuses(self) -> None:
        threading.Thread(target=self._check_whisper_statuses,     daemon=True).start()
        threading.Thread(target=self._check_whisper_cpp_statuses, daemon=True).start()
        threading.Thread(target=self._check_ollama_statuses,      daemon=True).start()

    def _check_whisper_statuses(self) -> None:
        if self._whisper_grid is None:  # engine not installed yet
            return
        for model in WHISPER_MODELS:
            if self._whisper_checker.is_cached(model):
                self._dispatch(self._whisper_grid.set_ready, model)
            else:
                self._dispatch(self._whisper_grid.set_not_downloaded, model)

    def _check_whisper_cpp_statuses(self) -> None:
        if self._wcpp_grid is None:  # engine not built yet
            return
        for model in WHISPER_CPP_MODELS:
            if self._wcpp_checker.is_cached(model):
                self._dispatch(self._wcpp_grid.set_ready, model)
            else:
                self._dispatch(self._wcpp_grid.set_not_downloaded, model)

    def _check_ollama_statuses(self) -> None:
        if not self._ollama_inst.is_available():
            return
        host      = self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST)
        installed = self._ollama.get_installed_models(host)
        if installed is None:
            self._dispatch(self._set_ollama_unreachable)
            return
        self._dispatch(self._set_ollama_reachable)
        for model in OLLAMA_MODELS:
            if self._ollama.is_model_installed(model, installed):
                self._dispatch(self._ollama_grid.set_ready, model)
            else:
                self._dispatch(self._ollama_grid.set_not_downloaded, model)

    def _set_ollama_unreachable(self) -> None:
        if self._ollama_status_label:
            self._ollama_status_label.set_text(
                "Ollama not reachable. Start it with: ollama serve"
            )
        if self._ollama_grid:
            for model in OLLAMA_MODELS:
                self._ollama_grid.set_status_text(model, "Ollama offline")

    def _set_ollama_reachable(self) -> None:
        if self._ollama_status_label:
            self._ollama_status_label.set_text("Ollama is running.")

    # ------------------------------------------------------------------
    # Download handlers
    # ------------------------------------------------------------------

    def _start_whisper_download(self, model: str) -> None:
        self._whisper_grid.set_progress(model, "Downloading\u2026")
        threading.Thread(
            target=self._do_whisper_download, args=(model,), daemon=True
        ).start()

    def _do_whisper_download(self, model: str) -> None:
        try:
            self._whisper_dl.download(model)
            self._dispatch(self._whisper_grid.set_ready, model)
        except Exception as exc:
            self._dispatch(self._whisper_grid.set_error, model, str(exc))

    def _start_wcpp_download(self, model: str) -> None:
        self._wcpp_grid.set_progress(model, "Downloading…")
        threading.Thread(
            target=self._do_wcpp_download, args=(model,), daemon=True
        ).start()

    def _do_wcpp_download(self, model: str) -> None:
        try:
            self._wcpp_dl.download(model)
            self._dispatch(self._wcpp_grid.set_ready, model)
        except Exception as exc:
            self._dispatch(self._wcpp_grid.set_error, model, str(exc))

    def _start_ollama_download(self, model: str) -> None:
        host = self._ollama_host_entry.get_text().strip()
        self._ollama_grid.set_progress(model, "Starting\u2026")
        threading.Thread(
            target=self._do_ollama_download, args=(model, host), daemon=True
        ).start()

    def _do_ollama_download(self, model: str, host: str) -> None:
        def on_progress(text: str) -> None:
            self._dispatch(self._ollama_grid.set_progress, model, text)

        try:
            success = self._ollama.pull_model(model, host, on_progress)
            if success:
                self._dispatch(self._ollama_grid.set_ready, model)
            else:
                self._dispatch(self._ollama_grid.set_error, model, "Download may have failed")
        except Exception as exc:
            self._dispatch(self._ollama_grid.set_error, model, str(exc))

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

        dialog.select_folder(self, None, _done)

    def _on_response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id == Gtk.ResponseType.OK:
            self._save()
            if self._on_saved is not None:
                self._on_saved()
        # GTK4 dialogs are closed explicitly (no run()/destroy() pairing).
        self.close()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self) -> None:
        cfg = self._store.load()

        cfg["transcription_service"]    = self._ts_combo.get_active_id() or "gemini"
        cfg["summarization_service"]    = self._ss_combo.get_active_id() or "gemini"
        cfg["gemini_api_key"]           = self._gemini_key_entry.get_text().strip()
        cfg["gemini_transcription_model"] = (
            self._gemini_ts_model_combo.get_active_id() or GEMINI_MODELS[0]
        )
        cfg["gemini_summarization_model"] = (
            self._gemini_ss_model_combo.get_active_id() or GEMINI_MODELS[0]
        )
        cfg["llm_request_timeout_minutes"] = int(
            self._timeout_combo.get_active_id() or "3"
        )
        # These combos only exist once the corresponding opt-in engine is
        # installed/built; preserve the stored value otherwise.
        if self._whisper_model_combo is not None:
            cfg["whisper_model"] = self._whisper_model_combo.get_active_id() or WHISPER_MODELS[0]
        if self._wcpp_model_combo is not None:
            cfg["whisper_cpp_model"] = (
                self._wcpp_model_combo.get_active_id() or WHISPER_CPP_MODELS[0]
            )
        if self._wcpp_backend_combo is not None:
            cfg["whisper_cpp_backend"] = self._wcpp_backend_combo.get_active_id() or "auto"
        cfg["output_folder"]    = self._folder_entry.get_text().strip() or "~/meetings"
        cfg["recording_quality"] = self._quality_combo.get_active_id() or "high"
        cfg["call_detection_enabled"]       = self._detection_switch.get_active()
        cfg["start_at_startup"]             = self._startup_switch.get_active()
        cfg["auto_title"]                   = self._auto_title_switch.get_active()
        cfg["processing_countdown_enabled"] = self._countdown_switch.get_active()

        if self._ollama_inst.is_available():
            cfg["ollama_model"] = self._ollama_model_combo.get_active_id() or OLLAMA_MODELS[0]
            cfg["ollama_host"]  = (
                self._ollama_host_entry.get_text().strip() or OLLAMA_DEFAULT_HOST
            )

        for key, default in _PROMPT_DEFAULTS.items():
            cfg[f"{key}_prompt"] = self._read_prompt(self._prompt_views[key], default)

        try:
            self._store.save(cfg)
            update_autostart(cfg["start_at_startup"])
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)

    def _read_prompt(self, view: Gtk.TextView, default: str) -> str:
        buf  = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        return "" if text == default.strip() else text
