"""Settings → Models tab: service selection, per-engine model management,
opt-in engine install/build flows, and GPU-runtime installs.

All I/O is injected (same seams as ``SettingsDialog``, which passes its own
injected dependencies straight through) so the page can be exercised in tests
without a real filesystem, network, or GTK main loop. ``dispatcher`` routes
worker-thread results back to the GTK main thread (``GLib.idle_add`` in
production, a synchronous callable in tests).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from meeting_recorder.config.defaults import (
    GEMINI_MODELS,
    LLM_TIMEOUT_OPTIONS,
    OLLAMA_DEFAULT_HOST,
    OLLAMA_MODEL_INFO,
    OLLAMA_MODELS,
    SUMMARIZATION_SERVICES,
    TRANSCRIPTION_SERVICES,
    WHISPER_CPP_BACKENDS,
    WHISPER_CPP_MODEL_INFO,
    WHISPER_CPP_MODELS,
    WHISPER_MODEL_INFO,
    WHISPER_MODELS,
)

from ...services.ollama_service import OllamaClient
from ...services.system_installer import (
    CudaInstaller,
    OllamaInstaller,
    RocmInstaller,
    WhisperEngineInstaller,
    detect_gpu_vendor,
)
from ...services.whisper_cpp_service import (
    WhisperCppBuilder,
    WhisperCppModelDownloader,
    WhisperCppStatusChecker,
    detect_gpu_backend,
)
from ...services.whisper_service import WhisperDownloader, WhisperStatusChecker
from ...utils.gtk_compat import remove_all_children
from ..model_row_grid import ModelRowGrid
from ..settings_visibility import compute_section_visibility
from .widgets import IdComboRow, action_row, install_button, make_scroll_page

_SERVICE_LABELS = {
    "gemini": "Google Gemini",
    "whisper": "Whisper (local)",
    "whisper_cpp": "whisper.cpp (local, GPU)",
    "ollama": "Ollama (local)",
}


class ModelsPage:
    """Builds the Models tab and writes its values back on save via ``apply()``."""

    def __init__(
        self,
        cfg: dict,
        *,
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
    ) -> None:
        # --- injected dependencies (real defaults for production) ---
        self._cfg = cfg
        self._dispatch = dispatcher
        self._whisper_checker = whisper_checker or WhisperStatusChecker()
        self._whisper_dl = whisper_downloader or WhisperDownloader()
        self._ollama = ollama_client or OllamaClient()
        self._ollama_inst = ollama_installer or OllamaInstaller()
        self._cuda_inst = cuda_installer or CudaInstaller()
        self._rocm_inst = rocm_installer or RocmInstaller()
        # Maps a detected GPU vendor to the runtime installer that serves it.
        self._gpu_installers = {"nvidia": self._cuda_inst, "amd": self._rocm_inst}
        self._whisper_eng_inst = whisper_engine_installer or WhisperEngineInstaller()
        self._wcpp_builder = whisper_cpp_builder or WhisperCppBuilder()
        self._wcpp_checker = whisper_cpp_checker or WhisperCppStatusChecker()
        self._wcpp_dl = whisper_cpp_downloader or WhisperCppModelDownloader()
        self._gpu_vendor = gpu_vendor if gpu_vendor is not None else detect_gpu_vendor()

        # --- widget references populated during build ---
        self._whisper_grid: ModelRowGrid | None = None
        self._whisper_model_combo: IdComboRow | None = None
        self._wcpp_grid: ModelRowGrid | None = None
        self._wcpp_model_combo: IdComboRow | None = None
        self._wcpp_backend_combo: IdComboRow | None = None
        self._ollama_grid: ModelRowGrid | None = None
        self._ollama_status_row: Adw.ActionRow | None = None
        self._ollama_model_combo: IdComboRow | None = None
        self._ollama_host_entry: Adw.EntryRow | None = None

        self.widget = self._build()

        # Apply initial visibility based on selected services.
        self._update_models_visibility()

    # ------------------------------------------------------------------
    # Build — composed from independent section builders
    # ------------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        scroll, box = make_scroll_page()

        services = Adw.PreferencesGroup(title="Services")
        self._ts_combo = self._make_service_combo(
            "Transcription service",
            TRANSCRIPTION_SERVICES,
            self._cfg.get("transcription_service", "gemini"),
        )
        self._ts_combo.connect("notify::selected", lambda *_: self._update_models_visibility())
        services.add(self._ts_combo)

        self._ss_combo = self._make_service_combo(
            "Summarization service",
            SUMMARIZATION_SERVICES,
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
        return IdComboRow(title, items, [_SERVICE_LABELS.get(i, i) for i in items], active)

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
            "Transcription model",
            GEMINI_MODELS,
            GEMINI_MODELS,
            self._cfg.get("gemini_transcription_model", GEMINI_MODELS[0]),
        )
        group.add(self._gemini_ts_model_combo)

        self._gemini_ss_model_combo = IdComboRow(
            "Summarization model",
            GEMINI_MODELS,
            GEMINI_MODELS,
            self._cfg.get("gemini_summarization_model", GEMINI_MODELS[0]),
        )
        group.add(self._gemini_ss_model_combo)

        t_ids = [str(m) for m in LLM_TIMEOUT_OPTIONS]
        self._timeout_combo = IdComboRow(
            "Processing timeout",
            t_ids,
            [f"{m} min" for m in LLM_TIMEOUT_OPTIONS],
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
            self._whisper_install_button = install_button("Install")
            self._whisper_install_button.connect("clicked", self._on_install_whisper_engine)
            group.add(
                action_row("faster-whisper engine", "Not installed", self._whisper_install_button)
            )
            self._whisper_box.append(group)
        else:
            group = Adw.PreferencesGroup(
                title="Whisper",
                description="Models are downloaded from HuggingFace and cached locally.",
            )
            self._whisper_model_combo = IdComboRow(
                "Whisper model",
                WHISPER_MODELS,
                WHISPER_MODELS,
                self._cfg.get("whisper_model", WHISPER_MODELS[0]),
            )
            group.add(self._whisper_model_combo)
            self._whisper_box.append(group)

            self._whisper_grid = ModelRowGrid(
                WHISPER_MODELS,
                WHISPER_MODEL_INFO,
                self._start_whisper_download,
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
            "Acceleration backend",
            WHISPER_CPP_BACKENDS,
            WHISPER_CPP_BACKENDS,
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
            self._wcpp_install_button = install_button("Build")
            self._wcpp_install_button.connect("clicked", self._on_build_whisper_cpp)
            install_group.add(
                action_row("whisper.cpp engine", "Not built", self._wcpp_install_button)
            )
            self._wcpp_box.append(install_group)
        else:
            cfg_group = Adw.PreferencesGroup(
                description="GGML models are downloaded from HuggingFace and cached locally.",
            )
            self._wcpp_model_combo = IdComboRow(
                "Model",
                WHISPER_CPP_MODELS,
                WHISPER_CPP_MODELS,
                self._cfg.get("whisper_cpp_model", WHISPER_CPP_MODELS[0]),
            )
            cfg_group.add(self._wcpp_model_combo)
            self._wcpp_box.append(cfg_group)

            self._wcpp_grid = ModelRowGrid(
                WHISPER_CPP_MODELS,
                WHISPER_CPP_MODEL_INFO,
                self._start_wcpp_download,
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
            self._ollama_install_button = install_button("Install")
            self._ollama_install_button.connect("clicked", self._on_install_ollama)
            group.add(action_row("Ollama", "Not installed", self._ollama_install_button))
            self._ollama_box.append(group)
        else:
            group = Adw.PreferencesGroup(
                title="Ollama",
                description="Requires Ollama to be installed and running (ollama serve).",
            )
            self._ollama_model_combo = IdComboRow(
                "Ollama model",
                OLLAMA_MODELS,
                OLLAMA_MODELS,
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
                OLLAMA_MODELS,
                OLLAMA_MODEL_INFO,
                self._start_ollama_download,
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
                    "AMD ROCm detected. GPU acceleration is available (use the whisper.cpp engine)."
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
        self._gpu_box.append(Adw.PreferencesGroup(title="GPU Acceleration", description=text))

    def _build_gpu_installer(self, vendor: str, info_text: str, button_label: str) -> None:
        group = Adw.PreferencesGroup(title="GPU Acceleration", description=info_text)
        self._gpu_install_button = install_button(button_label)
        self._gpu_install_button.connect("clicked", self._on_install_gpu, vendor)
        group.add(action_row("GPU runtime", "Not installed", self._gpu_install_button))
        self._gpu_box.append(group)

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
            self.refresh_local_model_statuses()
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
            self.refresh_local_model_statuses()
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
        threading.Thread(target=self._do_build_whisper_cpp, args=(backend,), daemon=True).start()

    def _do_build_whisper_cpp(self, backend: str) -> None:
        success = self._wcpp_builder.build(backend)
        self._dispatch(self._on_whisper_cpp_build_finished, success)

    def _on_whisper_cpp_build_finished(self, success: bool) -> None:
        if success and self._wcpp_builder.is_built():
            remove_all_children(self._wcpp_box)
            self._build_wcpp_inner()
            self.refresh_local_model_statuses()
        else:
            self._wcpp_install_button.set_sensitive(True)
            self._wcpp_install_button.set_label("Retry Build")

    def _on_install_gpu(self, button: Gtk.Button, vendor: str) -> None:
        button.set_sensitive(False)
        button.set_label("Installing…")
        threading.Thread(target=self._do_install_gpu, args=(vendor,), daemon=True).start()

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
    # Background status checks
    # ------------------------------------------------------------------

    def refresh_local_model_statuses(self) -> None:
        threading.Thread(target=self._check_whisper_statuses, daemon=True).start()
        threading.Thread(target=self._check_whisper_cpp_statuses, daemon=True).start()
        threading.Thread(target=self._check_ollama_statuses, daemon=True).start()

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
        host = self._cfg.get("ollama_host", OLLAMA_DEFAULT_HOST)
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
            self._ollama_status_row.set_subtitle("Not reachable. Start it with: ollama serve")
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
        threading.Thread(target=self._do_whisper_download, args=(model,), daemon=True).start()

    def _do_whisper_download(self, model: str) -> None:
        try:
            self._whisper_dl.download(model)
            self._dispatch(self._whisper_grid.set_ready, model)
        except Exception as exc:
            self._dispatch(self._whisper_grid.set_error, model, str(exc))

    def _start_wcpp_download(self, model: str) -> None:
        self._wcpp_grid.set_progress(model, "Downloading…")
        threading.Thread(target=self._do_wcpp_download, args=(model,), daemon=True).start()

    def _do_wcpp_download(self, model: str) -> None:
        try:
            self._wcpp_dl.download(model)
            self._dispatch(self._wcpp_grid.set_ready, model)
        except Exception as exc:
            self._dispatch(self._wcpp_grid.set_error, model, str(exc))

    def _start_ollama_download(self, model: str) -> None:
        host = self._ollama_host_entry.get_text().strip()
        self._ollama_grid.set_progress(model, "Starting…")
        threading.Thread(target=self._do_ollama_download, args=(model, host), daemon=True).start()

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
    # Save
    # ------------------------------------------------------------------

    def apply(self, cfg: dict) -> None:
        """Write this tab's values into ``cfg`` (called by the dialog's save flow)."""
        cfg["transcription_service"] = self._ts_combo.get_active_id() or "gemini"
        cfg["summarization_service"] = self._ss_combo.get_active_id() or "gemini"
        cfg["gemini_api_key"] = self._gemini_key_entry.get_text().strip()
        cfg["gemini_transcription_model"] = (
            self._gemini_ts_model_combo.get_active_id() or GEMINI_MODELS[0]
        )
        cfg["gemini_summarization_model"] = (
            self._gemini_ss_model_combo.get_active_id() or GEMINI_MODELS[0]
        )
        cfg["llm_request_timeout_minutes"] = int(self._timeout_combo.get_active_id() or "3")
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

        if self._ollama_model_combo is not None:
            cfg["ollama_model"] = self._ollama_model_combo.get_active_id() or OLLAMA_MODELS[0]
        if self._ollama_host_entry is not None:
            cfg["ollama_host"] = self._ollama_host_entry.get_text().strip() or OLLAMA_DEFAULT_HOST
