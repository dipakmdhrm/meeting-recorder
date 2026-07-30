"""
The D-Bus Engine service: the daemon<->window boundary.

Exposes ``io.github.dipakmdhrm.MeetingRecorder.Engine`` on the session bus.
Method calls from the window process are routed to the in-daemon ``Engine``;
state changes are pushed back as ``SnapshotChanged`` signals (plus ``Error`` /
``Output`` / ``OpenUseExisting`` / ``PresentWindow``). Also owns the window
child via ``WindowSupervisor`` (spawn on ``OpenWindow``, present the existing
one otherwise).

This module is GTK-free (``Gio``/``GLib`` only) and is not unit-tested — it
needs a real session bus. The spawn/present decision it delegates to
``WindowSupervisor`` is unit-tested separately; the pure snapshot payload it
carries is covered by ``core/wire`` tests.
"""

from __future__ import annotations

import logging
import sys

from gi.repository import Gio, GLib

from ..config.defaults import APP_ID
from .engine import Engine
from .window_supervisor import WindowSupervisor

logger = logging.getLogger(__name__)

ENGINE_NAME = APP_ID
ENGINE_PATH = "/" + APP_ID.replace(".", "/")
ENGINE_IFACE = APP_ID + ".Engine"

_ENGINE_XML = f"""
<node>
  <interface name="{ENGINE_IFACE}">
    <method name="StartRecording"><arg name="mode" type="s" direction="in"/></method>
    <method name="SetTitle"><arg name="title" type="s" direction="in"/></method>
    <method name="Pause"/>
    <method name="Resume"/>
    <method name="Stop"/>
    <method name="CancelCountdown"/>
    <method name="CancelSave"/>
    <method name="Cancel"/>
    <method name="ImportExisting">
      <arg name="audio" type="s" direction="in"/>
      <arg name="transcript" type="s" direction="in"/>
      <arg name="notes" type="s" direction="in"/>
      <arg name="label" type="s" direction="in"/>
    </method>
    <method name="SummarizeMeeting">
      <arg name="audio" type="s" direction="in"/>
      <arg name="transcript" type="s" direction="in"/>
      <arg name="notes" type="s" direction="in"/>
      <arg name="label" type="s" direction="in"/>
      <arg name="error" type="s" direction="out"/>
    </method>
    <method name="CancelJob"><arg name="id" type="i" direction="in"/></method>
    <method name="RetryJob"><arg name="id" type="i" direction="in"/></method>
    <method name="DismissJob"><arg name="id" type="i" direction="in"/></method>
    <method name="JobFolder">
      <arg name="id" type="i" direction="in"/>
      <arg name="path" type="s" direction="out"/>
    </method>
    <method name="OutputFolder"><arg name="path" type="s" direction="out"/></method>
    <method name="ReloadConfig"/>
    <method name="OpenWindow"/>
    <method name="GetSnapshot"><arg name="json" type="s" direction="out"/></method>
    <method name="Quit"/>
    <signal name="SnapshotChanged"><arg name="json" type="s"/></signal>
    <signal name="Error"><arg name="msg" type="s"/></signal>
    <signal name="Output"><arg name="text" type="s"/></signal>
    <signal name="OpenUseExisting"/>
    <signal name="PresentWindow"/>
  </interface>
</node>
"""


class EngineService:
    """Publishes the Engine on D-Bus and supervises the window child."""

    def __init__(
        self,
        engine: Engine,
        on_quit,
        on_reload_config,
    ) -> None:
        self._engine = engine
        self._on_quit = on_quit
        self._on_reload_config = on_reload_config
        self._conn: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._owner_id = 0
        self._supervisor = WindowSupervisor(
            spawn_fn=self._spawn_window, present_fn=self._emit_present
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(_ENGINE_XML)
        self._reg_id = self._conn.register_object(
            ENGINE_PATH, node.interfaces[0], self._on_method, None, None
        )
        self._owner_id = Gio.bus_own_name_on_connection(
            self._conn,
            ENGINE_NAME,
            Gio.BusNameOwnerFlags.NONE,
            None,
            self._on_name_lost,
        )
        logger.info("Engine service registered at %s", ENGINE_PATH)

    def _on_name_lost(self, _conn, _name) -> None:
        # Another daemon owns the name (or the bus went away). A second daemon
        # must not fight for it — log and let this one exit cleanly.
        logger.warning("Lost/failed to acquire bus name %s — another daemon is running", ENGINE_NAME)

    # ------------------------------------------------------------------
    # Signals pushed to the window
    # ------------------------------------------------------------------

    def emit_snapshot(self) -> None:
        self._emit("SnapshotChanged", GLib.Variant("(s)", (self._engine.snapshot_json(),)))

    def emit_error(self, msg: str) -> None:
        self._emit("Error", GLib.Variant("(s)", (msg,)))

    def emit_output(self, text: str) -> None:
        self._emit("Output", GLib.Variant("(s)", (text,)))

    def _emit_present(self) -> None:
        self._emit("PresentWindow", None)

    def _emit(self, name: str, body) -> None:
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(None, ENGINE_PATH, ENGINE_IFACE, name, body)
        except GLib.Error as exc:
            logger.debug("Failed to emit %s: %s", name, exc)

    # ------------------------------------------------------------------
    # Window supervision
    # ------------------------------------------------------------------

    def open_window(self) -> None:
        self._supervisor.open()

    def _spawn_window(self) -> None:
        # fork+exec a fresh interpreter — NEVER a bare fork (the daemon has
        # threads, D-Bus connections and a live ffmpeg child that must not be
        # inherited). Gio.Subprocess does fork+exec.
        try:
            proc = Gio.Subprocess.new(
                [sys.executable, "-m", "meeting_recorder", "--window"],
                Gio.SubprocessFlags.NONE,
            )
            proc.wait_async(None, self._on_window_exit)
        except GLib.Error as exc:
            logger.error("Failed to spawn window: %s", exc)
            self._supervisor.on_child_exit()

    def _on_window_exit(self, proc, result) -> None:
        try:
            proc.wait_finish(result)  # reap — no zombies
        except GLib.Error:
            pass
        self._supervisor.on_child_exit()

    # ------------------------------------------------------------------
    # Method dispatch (window -> engine)
    # ------------------------------------------------------------------

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            self._dispatch(method, params, invocation)
        except Exception:
            logger.exception("Engine method %s failed", method)
            invocation.return_value(None)

    def _dispatch(self, method, params, invocation):
        eng = self._engine
        if method == "StartRecording":
            (mode,) = params.unpack()
            eng.start_recording(mode)
            invocation.return_value(None)
        elif method == "SetTitle":
            (title,) = params.unpack()
            eng.set_title(title)
            invocation.return_value(None)
        elif method == "Pause":
            eng.pause()
            invocation.return_value(None)
        elif method == "Resume":
            eng.resume()
            invocation.return_value(None)
        elif method == "Stop":
            eng.stop()
            invocation.return_value(None)
        elif method == "CancelCountdown":
            eng.cancel_countdown()
            invocation.return_value(None)
        elif method == "CancelSave":
            eng.cancel_and_save()
            invocation.return_value(None)
        elif method == "Cancel":
            eng.cancel_and_discard()
            invocation.return_value(None)
        elif method == "ImportExisting":
            audio, transcript, notes, label = params.unpack()
            eng.import_existing(audio, transcript, notes, label)
            invocation.return_value(None)
        elif method == "SummarizeMeeting":
            audio, transcript, notes, label = params.unpack()
            err = eng.summarize_meeting(audio, transcript, notes, label) or ""
            invocation.return_value(GLib.Variant("(s)", (err,)))
        elif method == "CancelJob":
            (jid,) = params.unpack()
            eng.cancel_job(int(jid))
            invocation.return_value(None)
        elif method == "RetryJob":
            (jid,) = params.unpack()
            eng.retry_job(int(jid))
            invocation.return_value(None)
        elif method == "DismissJob":
            (jid,) = params.unpack()
            eng.dismiss_job(int(jid))
            invocation.return_value(None)
        elif method == "JobFolder":
            (jid,) = params.unpack()
            invocation.return_value(GLib.Variant("(s)", (eng.job_folder(int(jid)) or "",)))
        elif method == "OutputFolder":
            invocation.return_value(GLib.Variant("(s)", (eng.output_folder(),)))
        elif method == "ReloadConfig":
            self._on_reload_config()
            invocation.return_value(None)
        elif method == "OpenWindow":
            self.open_window()
            invocation.return_value(None)
        elif method == "GetSnapshot":
            invocation.return_value(GLib.Variant("(s)", (eng.snapshot_json(),)))
        elif method == "Quit":
            invocation.return_value(None)
            self._on_quit()
        else:
            invocation.return_value(None)
