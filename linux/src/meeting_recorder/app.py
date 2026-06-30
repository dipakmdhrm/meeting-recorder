"""
Defines the MeetingRecorderApp class, a Gtk.Application subclass that manages the overall application lifecycle. It handles startup initialization, logging setup, system dependency validation, and coordinates the creation of the main window, tray icon, and call detector.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Gio, Adw

from meeting_recorder.config.defaults import APP_ID, APP_NAME
from meeting_recorder.config import settings

logger = logging.getLogger(__name__)


def _check_system_deps() -> list[str]:
    """Return list of missing system dependencies."""
    missing = []
    for cmd in ("ffmpeg", "pactl"):
        try:
            subprocess.run([cmd, "--version"], capture_output=True, timeout=3)
        except FileNotFoundError:
            missing.append(cmd)
        except subprocess.TimeoutExpired:
            pass  # binary exists but hung — don't flag as missing
    return missing


class MeetingRecorderApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None
        self._tray = None
        self._call_detector = None

    # ------------------------------------------------------------------
    def do_startup(self) -> None:
        # Chain up to Adw.Application so libadwaita is initialised (style manager,
        # dark-mode portal, Adwaita stylesheet).
        Adw.Application.do_startup(self)
        self._setup_logging()
        # Without hold(), GApplication exits as soon as the last window is hidden.
        # We hide to tray rather than closing, so we need to keep the app alive manually.
        # The matching release() is never called; we exit via quit() instead.
        self.hold()

    @staticmethod
    def _setup_logging() -> None:
        class _BelowWarning(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return record.levelno < logging.WARNING

        log_dir = Path("/var/log/meeting-recorder")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / ".write_test").touch()
            (log_dir / ".write_test").unlink()
        except OSError:
            log_dir = Path(os.path.expanduser("~/.local/share/meeting-recorder"))
            log_dir.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s"
        )

        app_fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
        app_fh.setLevel(logging.DEBUG)
        app_fh.addFilter(_BelowWarning())
        app_fh.setFormatter(fmt)
        root.addHandler(app_fh)

        err_fh = logging.FileHandler(log_dir / "error.log", encoding="utf-8")
        err_fh.setLevel(logging.WARNING)
        err_fh.setFormatter(fmt)
        root.addHandler(err_fh)

        # Also log to stderr if a terminal is attached
        if sys.stderr and sys.stderr.isatty():
            sh = logging.StreamHandler(sys.stderr)
            sh.setLevel(logging.DEBUG)
            sh.setFormatter(fmt)
            root.addHandler(sh)

        logging.getLogger(__name__).info(
            "Logging to %s/{app,error}.log", log_dir
        )

    def do_activate(self) -> None:
        if self.window is None:
            self._create_window()
        self.window.present()

    # ------------------------------------------------------------------
    def _create_window(self) -> None:
        from .ui.main_window import MainWindow
        self.window = MainWindow(application=self)

        # System tray (best-effort)
        try:
            from .ui.tray import TrayIcon
            self._tray = TrayIcon(self.window)
        except Exception as exc:
            logger.info("Tray unavailable: %s", exc)

        # Call detection (if enabled)
        cfg = settings.load()
        if cfg.get("call_detection_enabled"):
            self._start_call_detector()

        # GTK4 widgets are visible by default; the window is shown via
        # do_activate() -> window.present(), so no explicit show_all() is needed.

        # Validate system deps after window shown so errors display nicely
        GLib.idle_add(self._validate_system_deps)

    def _validate_system_deps(self) -> bool:
        missing = _check_system_deps()
        if missing:
            msg = (
                f"Missing system tools: {', '.join(missing)}\n\n"
                "Please run install.sh to install dependencies."
            )
            # GTK4 has no blocking Gtk.Dialog.run(); Gtk.AlertDialog shows
            # asynchronously. This is purely informational, so no callback is
            # needed — show() is fire-and-forget.
            alert = Gtk.AlertDialog()
            alert.set_modal(True)
            alert.set_message("Missing Dependencies")
            alert.set_detail(msg)
            alert.set_buttons(["OK"])
            alert.show(self.window)
        return GLib.SOURCE_REMOVE

    def _start_call_detector(self) -> None:
        try:
            from .detection.call_detector import CallDetector
            self._call_detector = CallDetector(
                on_call_detected=self._on_call_detected
            )
            self._call_detector.start()
        except Exception as exc:
            logger.warning("Failed to start call detector: %s", exc)

    def _on_call_detected(self, source: str) -> None:
        from .ui.main_window import State
        # Don't notify if we are already recording or processing — the user started
        # intentionally and a "call detected" popup would be disruptive/redundant.
        if self.window and self.window._state != State.IDLE:
            logger.debug("Call detected but app is already active — suppressing notification")
            return
        from .ui.notifications import notify
        notify(
            summary="Call Detected",
            body="A call may have started. Click to start recording.",
            app_name=APP_NAME,
        )

    # ------------------------------------------------------------------
    def do_shutdown(self) -> None:
        # Terminate the pactl subscribe subprocess explicitly; daemon threads die on
        # exit but the child process would otherwise become an orphan.
        if self._call_detector is not None:
            self._call_detector.stop()
        Adw.Application.do_shutdown(self)
