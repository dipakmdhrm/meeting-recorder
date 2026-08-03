"""
Tabbed settings window (General / Models / Prompts), built with libadwaita
preference rows. Each tab lives in its own module under ``settings_pages/``;
this dialog is a thin shell that provides the Cancel/ViewSwitcher/Save chrome,
instantiates the pages, and runs the save flow.

All I/O is injected so the dialog can be exercised in tests without a real
filesystem, network, or GTK main loop:

    dialog = SettingsDialog(
        parent,
        store=FakeStore({}),
        whisper_checker=WhisperStatusChecker(cache_root=tmp_path),
        ollama_client=OllamaClient(http_open=fake_http),
        ollama_installer=OllamaInstaller(which_fn=lambda _: None, run_fn=lambda _: 0),
        cuda_installer=CudaInstaller(which_fn=lambda _: None, run_fn=lambda _: 0),
        dispatcher=lambda fn, *a: fn(*a),   # synchronous — no GTK loop needed
    )
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from meeting_recorder.config import settings

from ..services.ollama_service import OllamaClient
from ..services.system_installer import (
    CudaInstaller,
    OllamaInstaller,
    RocmInstaller,
    WhisperEngineInstaller,
)
from ..services.whisper_cpp_service import (
    WhisperCppBuilder,
    WhisperCppModelDownloader,
    WhisperCppStatusChecker,
)
from ..services.whisper_service import WhisperDownloader, WhisperStatusChecker
from ..utils.autostart import update_autostart
from .settings_pages import GeneralPage, IdComboRow, ModelsPage, PromptsPage
from .settings_visibility import compute_section_visibility

__all__ = ["IdComboRow", "SettingsDialog", "compute_section_visibility"]

logger = logging.getLogger(__name__)


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
        engine=None,
    ) -> None:
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.set_default_size(620, 680)

        # The daemon proxy: model/engine installs are started on it and run in
        # the daemon so they survive this window closing.
        self._engine = engine

        # Called after a successful save. The window is modeless (Adw.Window has
        # no blocking run()), so the caller acts on the result via this callback
        # — see MainWindow._on_settings_clicked.
        self._on_saved = on_saved

        self._store = store
        cfg = store.load()

        # Pages own their tab's widgets and save logic; injected dependencies
        # are passed straight through (ModelsPage applies the real production
        # defaults for any that are None).
        self._general_page = GeneralPage(cfg, self)
        self._models_page = ModelsPage(
            cfg,
            whisper_checker=whisper_checker,
            whisper_downloader=whisper_downloader,
            ollama_client=ollama_client,
            ollama_installer=ollama_installer,
            cuda_installer=cuda_installer,
            rocm_installer=rocm_installer,
            whisper_engine_installer=whisper_engine_installer,
            whisper_cpp_builder=whisper_cpp_builder,
            whisper_cpp_checker=whisper_cpp_checker,
            whisper_cpp_downloader=whisper_cpp_downloader,
            gpu_vendor=gpu_vendor,
            dispatcher=dispatcher,
            engine=engine,
        )
        self._prompts_page = PromptsPage(cfg)
        self._pages = (self._general_page, self._models_page, self._prompts_page)

        self._build_ui()

        # Route the daemon's install progress/finished signals to the Models page
        # while this dialog is open, and detach when it closes.
        if self._engine is not None:
            self._engine.add_install_listener(self._models_page)
            self._models_page.reflect_running_installs()
            self.connect("close-request", self._on_close_request)

    def _on_close_request(self, *_) -> bool:
        if self._engine is not None:
            self._engine.remove_install_listener(self._models_page)
        return False

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
            self._general_page.widget, "general", "General", "preferences-system-symbolic"
        )
        self._stack.add_titled_with_icon(
            self._models_page.widget, "models", "Models", "folder-download-symbolic"
        )
        self._stack.add_titled_with_icon(
            self._prompts_page.widget, "prompts", "Prompts", "document-edit-symbolic"
        )

        # Kick off background status checks.
        self._models_page.refresh_local_model_statuses()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_save_clicked(self, *_) -> None:
        self._save()
        # Cheap format check so a pasted-wrong API key surfaces now instead of
        # as a failed job at the end of a meeting. Non-blocking: still saves.
        warning = settings.gemini_key_warning(self._store.load())
        if warning:
            alert = Gtk.AlertDialog()
            alert.set_message("API Key Warning")
            alert.set_detail(warning)
            alert.set_buttons(["OK"])
            alert.show(self.get_transient_for())
        if self._on_saved is not None:
            self._on_saved()
        self.close()

    def _save(self) -> None:
        cfg = self._store.load()
        for page in self._pages:
            page.apply(cfg)
        try:
            self._store.save(cfg)
            update_autostart(cfg["start_at_startup"])
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)
