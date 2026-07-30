"""
The GTK window process (``meeting-recorder --window``).

A short-lived ``Adw.Application`` that shows the recorder window and talks to the
always-on daemon through an ``EngineProxy``. It is launched as a child of the
daemon and exits when the window closes — so GTK/libadwaita memory is loaded
only while a window is visible and fully reclaimed by the OS afterwards.

Uses ``NON_UNIQUE`` so it does not try to own the ``io.github.dipakmdhrm...``
bus name (the daemon owns it); the daemon guarantees a single window by tracking
the child and emitting PresentWindow instead of spawning a second one. The app
id is still APP_ID so the shell maps the window to the app icon via StartupWMClass.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..config.defaults import APP_ID
from ..utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


class WindowApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.window = None
        self._proxy = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        setup_logging(role="window")
        self._setup_app_icon()

    def do_activate(self) -> None:
        if self.window is None:
            self._create_window()
        self.window.present_window()

    def _create_window(self) -> None:
        from .engine_proxy import EngineProxy
        from .main_window import MainWindow

        self._proxy = EngineProxy(
            on_snapshot=self._on_snapshot,
            on_error=self._on_error,
            on_output=self._on_output,
            on_open_use_existing=self._on_open_use_existing,
            on_present=self._on_present,
        )
        self.window = MainWindow(engine=self._proxy, application=self)

    # --- proxy signal fan-in (already on the main loop thread) ---
    def _on_snapshot(self, payload: str) -> None:
        if self.window:
            self.window.apply_snapshot_json(payload)

    def _on_error(self, msg: str) -> None:
        if self.window:
            self.window.show_error(msg)

    def _on_output(self, text: str) -> None:
        if self.window:
            self.window.show_output(text)

    def _on_open_use_existing(self) -> None:
        if self.window:
            self.window.open_use_existing()

    def _on_present(self) -> None:
        if self.window:
            self.window.present_window()

    @staticmethod
    def _setup_app_icon() -> None:
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return
            icons_dir = Path(__file__).resolve().parent.parent / "assets" / "icons"
            Gtk.IconTheme.get_for_display(display).add_search_path(str(icons_dir))
            Gtk.Window.set_default_icon_name("meeting-recorder")
        except Exception as exc:
            logger.warning("Failed to set up application icon: %s", exc)


def main(argv=None) -> int:
    import sys

    app = WindowApp()
    # Strip the --window role flag so GApplication doesn't try to parse it.
    args = [a for a in (argv if argv is not None else sys.argv) if a != "--window"]
    return app.run(args)
