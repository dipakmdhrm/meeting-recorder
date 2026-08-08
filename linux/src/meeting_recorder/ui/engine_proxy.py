"""
Client-side proxy to the daemon's Engine (the window process talks through this).

Wraps the ``io.github.dipakmdhrm.MeetingRecorder.Engine`` D-Bus interface: the
window issues commands as method calls and receives state as ``SnapshotChanged``
(and Error/Output/OpenUseExisting/PresentWindow) signals. Command calls are
fire-and-forget (async); the few that need a value (snapshot, job folder,
summarize) are synchronous.

GTK-free itself (``Gio``/``GLib`` only); it is constructed by ``window_app.py``
and handed to ``MainWindow``. Not unit-tested — it needs a live bus and the
running daemon on the other end.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib

from ..core.daemon_watch import should_exit_on_owner_change
from ..daemon.dbus_service import ENGINE_IFACE, ENGINE_NAME, ENGINE_PATH

logger = logging.getLogger(__name__)


class EngineProxy:
    def __init__(
        self,
        *,
        on_snapshot: Callable[[str], None],
        on_error: Callable[[str], None],
        on_output: Callable[[str], None],
        on_open_use_existing: Callable[[], None],
        on_present: Callable[[], None],
        on_daemon_gone: Callable[[], None] = lambda: None,
    ) -> None:
        self._on_snapshot = on_snapshot
        self._on_error = on_error
        self._on_output = on_output
        self._on_open_use_existing = on_open_use_existing
        self._on_present = on_present
        self._on_daemon_gone = on_daemon_gone
        # Install signals go to whatever Settings/Models page is currently open;
        # it (un)registers itself here since it is created and destroyed on demand.
        self._install_listeners: list = []
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._conn.signal_subscribe(
            ENGINE_NAME,
            ENGINE_IFACE,
            None,  # all signals
            ENGINE_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_signal,
        )
        # A window must never outlive its daemon: watch the Engine bus name and
        # exit if the daemon that spawned us quits or crashes, so a hidden
        # (kept-in-memory) window can't linger and double up on the next daemon's
        # PresentWindow broadcast. See core/daemon_watch.py.
        self._daemon_seen = False
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._conn,
            ENGINE_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_daemon_appeared,
            self._on_daemon_vanished,
        )

    # ------------------------------------------------------------------
    # Daemon liveness (bus-name owner) watch
    # ------------------------------------------------------------------

    def _on_daemon_appeared(self, _conn, _name, _owner) -> None:
        self._daemon_seen = True

    def _on_daemon_vanished(self, _conn, _name) -> None:
        if should_exit_on_owner_change(self._daemon_seen, has_owner=False):
            logger.info("Daemon gone — window exiting to avoid orphaning")
            self._on_daemon_gone()

    # ------------------------------------------------------------------
    # Signals from the daemon
    # ------------------------------------------------------------------

    def _on_signal(self, _conn, _sender, _path, _iface, signal_name, params):
        if signal_name == "SnapshotChanged":
            self._on_snapshot(params.unpack()[0])
        elif signal_name == "Error":
            self._on_error(params.unpack()[0])
        elif signal_name == "Output":
            self._on_output(params.unpack()[0])
        elif signal_name == "OpenUseExisting":
            self._on_open_use_existing()
        elif signal_name == "PresentWindow":
            self._on_present()
        elif signal_name == "InstallProgress":
            key, text = params.unpack()
            for listener in list(self._install_listeners):
                listener.on_install_progress(key, text)
        elif signal_name == "InstallFinished":
            key, ok, message = params.unpack()
            for listener in list(self._install_listeners):
                listener.on_install_finished(key, ok, message)

    # ------------------------------------------------------------------
    # Command calls
    # ------------------------------------------------------------------

    def _call(self, method: str, body=None) -> None:
        self._conn.call(
            ENGINE_NAME,
            ENGINE_PATH,
            ENGINE_IFACE,
            method,
            body,
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,  # fire-and-forget
        )

    def _call_sync(self, method: str, body=None, reply_type: str = "(s)"):
        try:
            result = self._conn.call_sync(
                ENGINE_NAME,
                ENGINE_PATH,
                ENGINE_IFACE,
                method,
                body,
                GLib.VariantType.new(reply_type),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            return result.unpack()
        except GLib.Error as exc:
            logger.error("Engine.%s failed: %s", method, exc)
            return None

    # --- recording lifecycle ---
    def start_recording(self, mode: str) -> None:
        self._call("StartRecording", GLib.Variant("(s)", (mode,)))

    def set_title(self, title: str) -> None:
        self._call("SetTitle", GLib.Variant("(s)", (title,)))

    def pause(self) -> None:
        self._call("Pause")

    def resume(self) -> None:
        self._call("Resume")

    def stop(self) -> None:
        self._call("Stop")

    def cancel_countdown(self) -> None:
        self._call("CancelCountdown")

    def cancel_save(self) -> None:
        self._call("CancelSave")

    def cancel(self) -> None:
        self._call("Cancel")

    # --- jobs ---
    def import_existing(self, audio: str, transcript: str, notes: str, label: str) -> None:
        self._call("ImportExisting", GLib.Variant("(ssss)", (audio, transcript, notes, label)))

    def summarize_meeting(self, audio: str, transcript: str, notes: str, label: str) -> str:
        res = self._call_sync(
            "SummarizeMeeting", GLib.Variant("(ssss)", (audio, transcript, notes, label))
        )
        return res[0] if res else ""

    def cancel_job(self, job_id: int) -> None:
        self._call("CancelJob", GLib.Variant("(i)", (job_id,)))

    def retry_job(self, job_id: int) -> None:
        self._call("RetryJob", GLib.Variant("(i)", (job_id,)))

    def dismiss_job(self, job_id: int) -> None:
        self._call("DismissJob", GLib.Variant("(i)", (job_id,)))

    def job_folder(self, job_id: int) -> str:
        res = self._call_sync("JobFolder", GLib.Variant("(i)", (job_id,)))
        return res[0] if res else ""

    def output_folder(self) -> str:
        res = self._call_sync("OutputFolder")
        return res[0] if res else ""

    # --- model/engine installs (run in the daemon) ---
    def add_install_listener(self, listener) -> None:
        self._install_listeners.append(listener)

    def remove_install_listener(self, listener) -> None:
        if listener in self._install_listeners:
            self._install_listeners.remove(listener)

    def start_install(self, spec_json: str) -> None:
        self._call("StartInstall", GLib.Variant("(s)", (spec_json,)))

    def get_installs(self) -> str:
        res = self._call_sync("GetInstalls")
        return res[0] if res else "[]"

    # --- misc ---
    def reload_config(self) -> None:
        self._call("ReloadConfig")

    def open_window(self) -> None:
        self._call("OpenWindow")

    def get_snapshot(self) -> str:
        res = self._call_sync("GetSnapshot")
        return res[0] if res else ""

    def quit(self) -> None:
        self._call("Quit")
