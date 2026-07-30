"""
Client mode (``meeting-recorder`` with no role flag).

This is what the app-menu launcher and the tray "Open" invoke. It makes sure the
daemon is running — starting it detached if not — and then asks it to open a
window. It loads no GTK; the daemon spawns the GTK window child.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time

from gi.repository import Gio, GLib

from .config.defaults import APP_ID

logger = logging.getLogger(__name__)

ENGINE_NAME = APP_ID
ENGINE_PATH = "/" + APP_ID.replace(".", "/")
ENGINE_IFACE = APP_ID + ".Engine"

_DBUS = "org.freedesktop.DBus"
_DBUS_PATH = "/org/freedesktop/DBus"


def _name_has_owner(conn: Gio.DBusConnection, name: str) -> bool:
    try:
        res = conn.call_sync(
            _DBUS,
            _DBUS_PATH,
            _DBUS,
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return bool(res.unpack()[0])
    except GLib.Error as exc:
        logger.warning("NameHasOwner check failed: %s", exc)
        return False


def _spawn_daemon() -> None:
    # Detached (own session) so the daemon outlives this transient client and is
    # not reparented under it. fork+exec of a fresh interpreter.
    subprocess.Popen(
        [sys.executable, "-m", "meeting_recorder", "--daemon"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_daemon(conn: Gio.DBusConnection, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _name_has_owner(conn, ENGINE_NAME):
            return True
        time.sleep(0.1)
    return False


def _open_window(conn: Gio.DBusConnection) -> None:
    conn.call_sync(
        ENGINE_NAME,
        ENGINE_PATH,
        ENGINE_IFACE,
        "OpenWindow",
        None,
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )


def main() -> int:
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if not _name_has_owner(conn, ENGINE_NAME):
        logger.info("Daemon not running — starting it")
        _spawn_daemon()
        if not _wait_for_daemon(conn):
            logger.error("Daemon did not come up in time")
            return 1
    try:
        _open_window(conn)
    except GLib.Error as exc:
        logger.error("Failed to open window: %s", exc)
        return 1
    return 0
