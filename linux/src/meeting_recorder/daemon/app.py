"""
The daemon entry point (``meeting-recorder --daemon``).

Runs the GTK-free engine, tray, call detector and D-Bus service on a GLib main
loop. This is the always-on process that sits in the system tray; the GTK
window is spawned on demand as a child (see ``dbus_service.EngineService``).
"""

from __future__ import annotations

import logging
import signal

from gi.repository import GLib

from ..config import settings
from ..core import commands
from ..core.task_runner import TaskRunner
from ..utils.autostart import migrate_autostart_entry
from ..utils.logging_setup import setup_logging
from .dbus_service import EngineService
from .engine import Engine

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self) -> None:
        self._loop = GLib.MainLoop()
        self._runner = TaskRunner()
        self._tray = None
        self._call_detector = None

        self._engine = Engine(
            self._runner,
            on_change=self._on_engine_change,
            on_error=self._on_engine_error,
            on_output=self._on_engine_output,
        )
        self._service = EngineService(
            self._engine,
            on_quit=self.quit,
            on_reload_config=self._reload_config,
        )

    # ------------------------------------------------------------------

    def run(self) -> int:
        setup_logging(role="daemon")
        try:
            settings.migrate_key_to_keyring()
        except Exception as exc:
            logger.warning("API-key keyring migration failed: %s", exc)
        # Safe now that --daemon exists: rewrite a legacy autostart entry so
        # login starts the tray daemon instead of opening a GTK window.
        migrate_autostart_entry()

        self._service.start()
        self._start_tray()
        self._engine.restore_persisted_jobs()

        cfg = settings.load()
        if cfg.get("call_detection_enabled"):
            self._start_call_detector()

        # Clean shutdown on SIGINT/SIGTERM.
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, int(sig), self._on_signal)

        logger.info("Daemon started")
        self._loop.run()
        return 0

    def quit(self) -> None:
        logger.info("Daemon shutting down")
        if self._call_detector is not None:
            self._call_detector.stop()
        # Finish any active recording (keep audio) and let in-flight jobs drain.
        self._engine.prepare_quit()
        abandoned = self._runner.shutdown(grace_seconds=10)
        if abandoned:
            logger.warning("Exited with unfinished background tasks: %s", ", ".join(abandoned))
        self._loop.quit()

    def _on_signal(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Tray
    # ------------------------------------------------------------------

    def _start_tray(self) -> None:
        try:
            from ..ui.tray import TrayIcon

            self._tray = TrayIcon(on_command=self._on_tray_command)
            self._on_engine_change()  # initial paint
        except Exception as exc:
            logger.info("Tray unavailable: %s", exc)

    def _on_tray_command(self, action: str, _job_index) -> None:
        eng = self._engine
        if action == commands.RECORD_HEADPHONES:
            eng.start_recording("headphones")
        elif action == commands.RECORD_SPEAKER:
            eng.start_recording("speaker")
        elif action == commands.PAUSE:
            eng.pause()
        elif action == commands.RESUME:
            eng.resume()
        elif action == commands.STOP:
            eng.stop()
        elif action == commands.CANCEL_SAVE:
            eng.cancel_and_save()
        elif action == commands.CANCEL:
            eng.cancel_and_discard()
        elif action in (commands.SHOW_WINDOW, commands.USE_EXISTING):
            # Both open the window; from there the user records or picks a file.
            self._service.open_window()
        elif action == commands.QUIT:
            self.quit()

    # ------------------------------------------------------------------
    # Engine event fan-out (tray + window)
    # ------------------------------------------------------------------

    def _on_engine_change(self) -> None:
        if self._tray is not None:
            jobs = [
                (j.label, (lambda jid=j.job_id: self._engine.cancel_job(jid)))
                for j in self._engine.processing_jobs()
            ]
            try:
                self._tray.update(self._engine.state_name(), jobs)
            except Exception:
                logger.debug("Tray update failed", exc_info=True)
        self._service.emit_snapshot()

    def _on_engine_error(self, msg: str) -> None:
        self._service.emit_error(msg)

    def _on_engine_output(self, text: str) -> None:
        self._service.emit_output(text)

    # ------------------------------------------------------------------
    # Config reload (from the window's Settings save)
    # ------------------------------------------------------------------

    def _reload_config(self) -> None:
        cfg = settings.load()
        if cfg.get("call_detection_enabled") and self._call_detector is None:
            self._start_call_detector()
        elif not cfg.get("call_detection_enabled") and self._call_detector is not None:
            self._call_detector.stop()
            self._call_detector = None

    def _start_call_detector(self) -> None:
        try:
            from ..detection.call_detector import CallDetector

            self._call_detector = CallDetector(on_call_detected=self._on_call_detected)
            self._call_detector.start()
        except Exception as exc:
            logger.warning("Failed to start call detector: %s", exc)

    def _on_call_detected(self, source: str) -> None:
        from ..core.state_machine import State

        if self._engine.state != State.IDLE:
            logger.debug("Call detected but engine already active — suppressing notification")
            return
        from ..ui.notifications import notify

        notify(
            summary="Call Detected",
            body="A call may have started. Open Meeting Recorder to start recording.",
        )


def main() -> int:
    return Daemon().run()
