"""
Tabbed settings window (General / Models / Prompts), built with libadwaita
preference rows.

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
gi.require_version("Adw", "1")
from gi.repository import GLib, Gio, Gtk, Adw

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
from ..utils.gtk_compat import remove_all_children
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


class IdComboRow(Adw.ComboRow):
    """An ``Adw.ComboRow`` backed by a list of display labels but addressed by a
    parallel list of stable ids — exposing ``get_active_id()`` / ``set_active_id()``
    so the save logic stays id-based (as it was with ``Gtk.ComboBoxText``)."""

    def __init__(self, title: str, ids: list[str], labels: list[str], active_id: str):
        super().__init__(title=title)
        self._ids = list(ids)
        self.set_model(Gtk.StringList.new(labels))
        self.set_active_id(active_id)

    def get_active_id(self) -> str | None:
        i = self.get_selected()
        if 0 <= i < len(self._ids):
            return self._ids[i]
        return None

    def set_active_id(self, id_: str | None) -> None:
        if id_ in self._ids:
            self.set_selected(self._ids.index(id_))
        elif self._ids:
            self.set_selected(0)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class SettingsDialog(Adw.Window):
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
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.set_default_size(620, 680)

        # Called after a successful save. The window is modeless (Adw.Window has
        # no blocking run()), so the caller acts on the result via this callback
        # — see MainWindow._on_settings_clicked.
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
        self._whisper_model_combo: IdComboRow | None = None
        self._wcpp_grid: ModelRowGrid | None = None
        self._wcpp_model_combo: IdComboRow | None = None
        self._wcpp_backend_combo: IdComboRow | None = None
        self._ollama_grid:  ModelRowGrid | None = None
        self._ollama_status_row: Adw.ActionRow | None = None
        self._prompt_views: dict[str, Gtk.TextView] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # Top-level layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        header.set_title_widget(switcher)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(save_btn)

        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self._stack)

        self._stack.add_titled_with_icon(
            self._build_general_tab(), "general", "General", "preferences-system-symbolic"
        )
        self._stack.add_titled_with_icon(
            self._build_models_tab(), "models", "Models", "folder-download-symbolic"
        )
        self._stack.add_titled_with_icon(
            self._build_prompts_tab(), "prompts", "Prompts", "document-edit-symbolic"
        )

        # Apply initial visibility based on selected services.
        self._update_models_visibility()
        # Kick off background status checks.
        self._refresh_local_model_statuses()

    @staticmethod
    def _make_scroll_page() -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        """Return (scrolled_window, content_box) for one tab — a clamped,
        vertically-scrolling column of preference groups."""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(12)
        box.set_margin_end(12)
        clamp = Adw.Clamp(maximum_size=620)
        clamp.set_child(box)
        scroll.set_child(clamp)
        return scroll, box

    # ------------------------------------------------------------------
    # General tab
    # ------------------------------------------------------------------

    def _build_general_tab(self) -> Gtk.Widget:
        scroll, box = self._make_scroll_page()

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
            "Recording quality", q_ids, q_labels,
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
    # Models tab — composed from independent section builders
    # ------------------------------------------------------------------

    def _build_models_tab(self) -> Gtk.Widget:
        scroll, box = self._make_scroll_page()

        services = Adw.PreferencesGroup(title="Services")
        self._ts_combo = self._make_service_combo(
            "Transcription service", TRANSCRIPTION_SERVICES,
            self._cfg.get("transcription_service", "gemini"),
        )
        self._ts_combo.connect("notify::selected", lambda *_: self._update_models_visibility())
        services.add(self._ts_combo)

        self._ss_combo = self._make_service_combo(
            "Summarization service", SUMMARIZATION_SERVICES,
            self._cfg.get("summarization_service", "gemini"),
        )
        self._ss_combo.connect("notify::selected", lambda *_: self._update_models_visibility())
        services.add(self._ss_combo)
        box.append(services)

        # Each section is a Box that holds one or more preference groups, shown
        # or hidden as a unit based on the selected services.
        self._gemini_section_widget = self._build_gemini_section()
        self._whisper_section_widget = self._build_whisper_section()
        self._wcpp_section_widget = self._build_whisper_cpp_section()
        self._ollama_section_widget = self._build_ollama_section()
        self._gpu_section_widget = self._build_gpu_section()
        for widget in (
            self._gemini_section_widget,
            self._whisper_section_widget,
            self._wcpp_section_widget,
            self._ollama_section_widget,
            self._gpu_section_widget,
        ):
            box.append(widget)

        return scroll

    def _make_service_combo(self, title: str, items: list[str], active: str) -> IdComboRow:
        return IdComboRow(
            title, items, [_SERVICE_LABELS.get(i, i) for i in items], active
        )

    def _update_models_visibility(self) -> None:
        ts = self._ts_combo.get_active_id() or "gemini"
        ss = self._ss_combo.get_active_id() or "gemini"
        vis = compute_section_visibility(ts, ss)
        self._gemini_section_widget.set_visible(vis["gemini"])
        self._whisper_section_widget.set_visible(vis["whisper"])
        self._wcpp_section_widget.set_visible(vis["wcpp"])
        self._ollama_section_widget.set_visible(vis["ollama"])
        self._gpu_section_widget.set_visible(vis["gpu"])

    # -- Gemini ---------------------------------------------------------

    def _build_gemini_section(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        group = Adw.PreferencesGroup(title="Gemini")

        self._gemini_key_entry = Adw.PasswordEntryRow(title="API key")
        self._gemini_key_entry.set_text(self._cfg.get("gemini_api_key", ""))
        group.add(self._gemini_key_entry)

        self._gemini_ts_model_combo = IdComboRow(
            "Transcription model", GEMINI_MODELS, GEMINI_MODELS,
            self._cfg.get("gemini_transcription_model", GEMINI_MODELS[0]),
        )
        group.add(self._gemini_ts_model_combo)

        self._gemini_ss_model_combo = IdComboRow(
            "Summarization model", GEMINI_MODELS, GEMINI_MODELS,
            self._cfg.get("gemini_summarization_model", GEMINI_MODELS[0]),
        )
        group.add(self._gemini_ss_model_combo)

        t_ids = [str(m) for m in LLM_TIMEOUT_OPTIONS]
        self._timeout_combo = IdComboRow(
            "Processing timeout", t_ids, [f"{m} min" for m in LLM_TIMEOUT_OPTIONS],
            str(self._cfg.get("llm_request_timeout_minutes", 3)),
        )
        group.add(self._timeout_combo)

        box.append(group)
        return box

    # -- Whisper --------------------------------------------------------

    def _build_whisper_section(self) -> Gtk.Widget:
        self._whisper_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._build_whisper_inner()
        return self._whisper_box

    def _build_whisper_inner(self) -> None:
        # The faster-whisper engine is opt-in (not in the base install): show an
        # install row until it is present, then the model download UI.
        if not self._whisper_eng_inst.is_available():
            group = Adw.PreferencesGroup(
                title="Whisper",
                description=(
                    "The Whisper engine (faster-whisper) is not installed. It enables "
                    "local transcription on NVIDIA GPUs or CPU."
                ),
            )
            self._whisper_install_button = self._install_button("Install")
            self._whisper_install_button.connect("clicked", self._on_install_whisper_engine)
            group.add(self._action_row("faster-whisper engine", "Not installed",
                                       self._whisper_install_button))
            self._whisper_box.append(group)
        else:
            group = Adw.PreferencesGroup(
                title="Whisper",
                description="Models are downloaded from HuggingFace and cached locally.",
            )
            self._whisper_model_combo = IdComboRow(
                "Whisper model", WHISPER_MODELS, WHISPER_MODELS,
                self._cfg.get("whisper_model", WHISPER_MODELS[0]),
            )
            group.add(self._whisper_model_combo)
            self._whisper_box.append(group)

            self._whisper_grid = ModelRowGrid(
                WHISPER_MODELS, WHISPER_MODEL_INFO, self._start_whisper_download,
                title="Whisper models",
            )
            self._whisper_box.append(self._whisper_grid)

    # -- whisper.cpp ----------------------------------------------------

    def _build_whisper_cpp_section(self) -> Gtk.Widget:
        self._wcpp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._build_wcpp_inner()
        return self._wcpp_box

    def _build_wcpp_inner(self) -> None:
        detected = detect_gpu_backend()
        group = Adw.PreferencesGroup(title="whisper.cpp (GPU-accelerated)")
        # Backend selector is always available — it drives both the build and
        # the runtime acceleration; "auto" detects the GPU.
        self._wcpp_backend_combo = IdComboRow(
            "Acceleration backend", WHISPER_CPP_BACKENDS, WHISPER_CPP_BACKENDS,
            self._cfg.get("whisper_cpp_backend", "auto"),
        )
        self._wcpp_backend_combo.set_subtitle(f"Detected: {detected}")
        group.add(self._wcpp_backend_combo)
        self._wcpp_box.append(group)

        if not self._wcpp_builder.is_built():
            install_group = Adw.PreferencesGroup(
                description=(
                    "whisper.cpp is not built yet. Building it compiles a local "
                    "transcription engine that can use AMD (ROCm/Vulkan), Apple "
                    "(Metal), NVIDIA, or CPU. This installs a build toolchain and "
                    "may take a few minutes."
                ),
            )
            self._wcpp_install_button = self._install_button("Build")
            self._wcpp_install_button.connect("clicked", self._on_build_whisper_cpp)
            install_group.add(self._action_row("whisper.cpp engine", "Not built",
                                                self._wcpp_install_button))
            self._wcpp_box.append(install_group)
        else:
            cfg_group = Adw.PreferencesGroup(
                description="GGML models are downloaded from HuggingFace and cached locally.",
            )
            self._wcpp_model_combo = IdComboRow(
                "Model", WHISPER_CPP_MODELS, WHISPER_CPP_MODELS,
                self._cfg.get("whisper_cpp_model", WHISPER_CPP_MODELS[0]),
            )
            cfg_group.add(self._wcpp_model_combo)
            self._wcpp_box.append(cfg_group)

            self._wcpp_grid = ModelRowGrid(
                WHISPER_CPP_MODELS, WHISPER_CPP_MODEL_INFO, self._start_wcpp_download,
                title="whisper.cpp models",
            )
            self._wcpp_box.append(self._wcpp_grid)

    # -- Ollama ---------------------------------------------------------

    def _build_ollama_section(self) -> Gtk.Widget:
        self._ollama_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._build_ollama_inner()
        return self._ollama_box

    def _build_ollama_inner(self) -> None:
        if not self._ollama_inst.is_available():
            group = Adw.PreferencesGroup(
                title="Ollama",
                description="Ollama is not installed. It is required for local summarization.",
            )
            self._ollama_install_button = self._install_button("Install")
            self._ollama_install_button.connect("clicked", self._on_install_ollama)
            group.add(self._action_row("Ollama", "Not installed",
                                       self._ollama_install_button))
            self._ollama_box.append(group)
        else:
            group = Adw.PreferencesGroup(
                title="Ollama",
                description="Requires Ollama to be installed and running (ollama serve).",
            )
            self._ollama_model_combo = IdComboRow(
                "Ollama model", OLLAMA_MODELS, OLLAMA_MODELS,
                self._cfg.get("ollama_model", OLLAMA_MODELS[0]),
            )
            group.add(self._ollama_model_combo)

            self._ollama_host_entry = Adw.EntryRow(title="Ollama host")
            self._ollama_host_entry.set_text(self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST))
            group.add(self._ollama_host_entry)

            self._ollama_status_row = Adw.ActionRow(
                title="Connection", subtitle="Checking Ollama connection…"
            )
            group.add(self._ollama_status_row)
            self._ollama_box.append(group)

            self._ollama_grid = ModelRowGrid(
                OLLAMA_MODELS, OLLAMA_MODEL_INFO, self._start_ollama_download,
                title="Ollama models",
            )
            self._ollama_box.append(self._ollama_grid)

    # -- GPU ------------------------------------------------------------

    def _build_gpu_section(self) -> Gtk.Widget:
        self._gpu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._build_gpu_inner()
        return self._gpu_box

    def _build_gpu_inner(self) -> None:
        if self._gpu_vendor == "nvidia":
            if self._cuda_inst.is_available():
                self._gpu_installed_group(
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
                self._gpu_installed_group(
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
            self._gpu_installed_group(
                "Apple Silicon detected. Metal GPU acceleration is built in "
                "(use the whisper.cpp engine) — no install needed."
            )
        else:
            self._gpu_installed_group(
                "No supported GPU detected. Local transcription will run on CPU, "
                "which is slow. For fast transcription, use the Gemini service."
            )

    def _gpu_installed_group(self, text: str) -> None:
        self._gpu_box.append(
            Adw.PreferencesGroup(title="GPU Acceleration", description=text)
        )

    def _build_gpu_installer(self, vendor: str, info_text: str, button_label: str) -> None:
        group = Adw.PreferencesGroup(title="GPU Acceleration", description=info_text)
        self._gpu_install_button = self._install_button(button_label)
        self._gpu_install_button.connect("clicked", self._on_install_gpu, vendor)
        group.add(self._action_row("GPU runtime", "Not installed", self._gpu_install_button))
        self._gpu_box.append(group)

    # -- small row helpers ----------------------------------------------

    @staticmethod
    def _install_button(label: str) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.add_css_class("flat")
        btn.set_valign(Gtk.Align.CENTER)
        return btn

    @staticmethod
    def _action_row(title: str, subtitle: str, suffix: Gtk.Widget) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.add_suffix(suffix)
        row.set_activatable_widget(suffix)
        return row

    # ------------------------------------------------------------------
    # Install handlers — Ollama
    # ------------------------------------------------------------------

    def _on_install_ollama(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Installing…")
        threading.Thread(target=self._do_install_ollama, daemon=True).start()

    def _do_install_ollama(self) -> None:
        success = self._ollama_inst.install()
        self._dispatch(self._on_ollama_install_finished, success)

    def _on_ollama_install_finished(self, success: bool) -> None:
        if success and self._ollama_inst.is_available():
            remove_all_children(self._ollama_box)
            self._build_ollama_inner()
            self._refresh_local_model_statuses()
        else:
            self._ollama_install_button.set_sensitive(True)
            self._ollama_install_button.set_label("Retry Install")

    # ------------------------------------------------------------------
    # Install handlers — Whisper engine (faster-whisper, opt-in)
    # ------------------------------------------------------------------

    def _on_install_whisper_engine(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Installing…")
        threading.Thread(target=self._do_install_whisper_engine, daemon=True).start()

    def _do_install_whisper_engine(self) -> None:
        success = self._whisper_eng_inst.install()
        self._dispatch(self._on_whisper_engine_install_finished, success)

    def _on_whisper_engine_install_finished(self, success: bool) -> None:
        if success and self._whisper_eng_inst.is_available():
            remove_all_children(self._whisper_box)
            self._build_whisper_inner()
            self._refresh_local_model_statuses()
        else:
            self._whisper_install_button.set_sensitive(True)
            self._whisper_install_button.set_label("Retry Install")

    # ------------------------------------------------------------------
    # Build handler — whisper.cpp engine (built from source, opt-in)
    # ------------------------------------------------------------------

    def _on_build_whisper_cpp(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        button.set_label("Building…")
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
            remove_all_children(self._wcpp_box)
            self._build_wcpp_inner()
            self._refresh_local_model_statuses()
        else:
            self._wcpp_install_button.set_sensitive(True)
            self._wcpp_install_button.set_label("Retry Build")

    def _on_install_gpu(self, button: Gtk.Button, vendor: str) -> None:
        button.set_sensitive(False)
        button.set_label("Installing…")
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
            remove_all_children(self._gpu_box)
            label = (
                "NVIDIA CUDA libraries detected. GPU acceleration is available."
                if vendor == "nvidia"
                else "AMD ROCm detected. GPU acceleration is available "
                "(use the whisper.cpp engine)."
            )
            self._gpu_installed_group(label)
        else:
            self._gpu_install_button.set_sensitive(True)
            self._gpu_install_button.set_label("Retry Install")

    # ------------------------------------------------------------------
    # Prompts tab — three sections built from a single helper (DRY)
    # ------------------------------------------------------------------

    def _build_prompts_tab(self) -> Gtk.Widget:
        scroll, box = self._make_scroll_page()
        box.append(self._build_prompt_section(
            key="transcription",
            label="Transcription prompt",
            note="Transcription prompts apply to Gemini only. Whisper does not use prompts.",
            height=160,
        ))
        box.append(self._build_prompt_section(
            key="summarization",
            label="Summarization prompt",
            height=160,
        ))
        box.append(self._build_prompt_section(
            key="title",
            label="Title prompt",
            note=(
                "Used for auto-titling recordings and the AI title button in the "
                "Library. Must contain {transcript}."
            ),
            height=120,
        ))
        return scroll

    def _build_prompt_section(
        self,
        key: str,
        label: str,
        note: str | None = None,
        height: int = 160,
    ) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=label)
        if note:
            group.set_description(note)

        reset_btn = Gtk.Button(label="Reset to default")
        reset_btn.add_css_class("flat")
        reset_btn.connect("clicked", lambda *_: self._reset_prompt(key))
        group.set_header_suffix(reset_btn)

        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_monospace(True)
        view.set_top_margin(8)
        view.set_bottom_margin(8)
        view.set_left_margin(8)
        view.set_right_margin(8)
        stored = self._cfg.get(f"{key}_prompt") or _PROMPT_DEFAULTS[key]
        view.get_buffer().set_text(stored)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(height)
        scroll.set_child(view)
        scroll.add_css_class("card")
        group.add(scroll)

        self._prompt_views[key] = view
        return group

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
        if self._ollama_status_row:
            self._ollama_status_row.set_subtitle(
                "Not reachable. Start it with: ollama serve"
            )
        if self._ollama_grid:
            for model in OLLAMA_MODELS:
                self._ollama_grid.set_status_text(model, "Ollama offline")

    def _set_ollama_reachable(self) -> None:
        if self._ollama_status_row:
            self._ollama_status_row.set_subtitle("Ollama is running.")

    # ------------------------------------------------------------------
    # Download handlers
    # ------------------------------------------------------------------

    def _start_whisper_download(self, model: str) -> None:
        self._whisper_grid.set_progress(model, "Downloading…")
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
        self._ollama_grid.set_progress(model, "Starting…")
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
    # Folder picker / save
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

        dialog.select_folder(self, None, _done)

    def _on_save_clicked(self, *_) -> None:
        self._save()
        if self._on_saved is not None:
            self._on_saved()
        self.close()

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
