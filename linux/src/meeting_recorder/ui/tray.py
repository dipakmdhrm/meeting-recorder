"""
System tray icon implemented as a pure-DBus StatusNotifierItem (SNI).

GTK4 removed ``Gtk.StatusIcon`` entirely, and the AppIndicator / pystray menu
backends are GTK3-linked (GTK3 and GTK4 cannot coexist in one process). The
modern, toolkit-independent tray protocol is the KDE/freedesktop
StatusNotifierItem spec plus ``com.canonical.dbusmenu`` for the menu — both
spoken over D-Bus. We implement them directly on ``Gio.DBusConnection``, which
ships with GLib/PyGObject (already a dependency) and is independent of the GTK
version, so it runs cleanly inside the GTK4 app.

Hosts: GNOME via the AppIndicator/KStatusNotifier extension, KDE Plasma, and the
SNI plugins of XFCE/MATE/Cinnamon/etc. Left-click delivers ``Activate`` (focus
the window) where the host supports it; otherwise the host opens the menu.
"""

from __future__ import annotations

import logging
import os

from gi.repository import Gio, GLib

from .tray_model import build_menu_model, icon_for_state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D-Bus interface definitions
# ---------------------------------------------------------------------------

_SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""

_WATCHER_NAME = "org.kde.StatusNotifierWatcher"
_WATCHER_PATH = "/StatusNotifierWatcher"
_ITEM_IFACE = "org.kde.StatusNotifierItem"
_ITEM_PATH = "/StatusNotifierItem"
_MENU_IFACE = "com.canonical.dbusmenu"
_MENU_PATH = "/MenuBar"


class TrayIcon:
    """StatusNotifierItem tray icon driven over D-Bus.

    Public API (unchanged from the GTK3 version): construct with the main
    window, then call ``update(recording_state, jobs)`` whenever state changes.
    ``jobs`` is a list of ``(label, cancel_fn)`` tuples.
    """

    def __init__(self, window) -> None:
        self._window = window
        self._recording_state = "idle"
        self._jobs: list = []
        self._icon_name = icon_for_state("idle", [])

        # Menu state: materialised item list + a monotonically increasing
        # revision the host watches via LayoutUpdated.
        self._menu_items: list[dict] = self._materialize_menu()
        self._revision = 1

        self._conn: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._owner_id = 0
        self._watch_id = 0
        self._item_reg_id = 0
        self._menu_reg_id = 0
        # We must own self._bus_name before telling the watcher to register it,
        # otherwise the watcher can't reach the item. Both name-ownership and the
        # watcher-appeared signal are async, so we register only once the name is
        # acquired (from whichever callback fires last).
        self._name_acquired = False

        self._setup_dbus()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, recording_state: str, jobs: list) -> None:
        self._recording_state = recording_state
        self._jobs = jobs
        self._icon_name = icon_for_state(recording_state, jobs)
        self._menu_items = self._materialize_menu()
        self._revision += 1
        self._emit_signal(_ITEM_PATH, _ITEM_IFACE, "NewIcon", None)
        self._emit_signal(
            _MENU_PATH, _MENU_IFACE, "LayoutUpdated",
            GLib.Variant("(ui)", (self._revision, 0)),
        )

    # ------------------------------------------------------------------
    # D-Bus setup
    # ------------------------------------------------------------------

    def _setup_dbus(self) -> None:
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        item_node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
        self._item_reg_id = self._conn.register_object(
            _ITEM_PATH, item_node.interfaces[0],
            self._sni_method, self._sni_get_property, None,
        )
        menu_node = Gio.DBusNodeInfo.new_for_xml(_MENU_XML)
        self._menu_reg_id = self._conn.register_object(
            _MENU_PATH, menu_node.interfaces[0],
            self._menu_method, self._menu_get_property, None,
        )

        # Own a per-process well-known name (libappindicator convention) so the
        # watcher can address us by it. Registration is (re)attempted once the
        # name is actually acquired.
        self._owner_id = Gio.bus_own_name_on_connection(
            self._conn, self._bus_name, Gio.BusNameOwnerFlags.NONE,
            self._on_name_acquired, None,
        )
        # Register whenever the watcher is/becomes present — fires immediately if
        # a host already exists, and again if the host (e.g. the GNOME extension)
        # restarts. Guarded on name acquisition so we never register a name we
        # don't yet own.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._conn, _WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._register_with_watcher(), None,
        )

    def _on_name_acquired(self, _conn, _name) -> None:
        self._name_acquired = True
        self._register_with_watcher()

    def _register_with_watcher(self) -> None:
        if self._conn is None or not self._name_acquired:
            return

        def _done(conn, result):
            try:
                conn.call_finish(result)
                logger.info("Tray: registered StatusNotifierItem with watcher")
            except GLib.Error as exc:
                logger.info("Tray: no StatusNotifierWatcher available (%s)", exc)

        self._conn.call(
            _WATCHER_NAME, _WATCHER_PATH, _WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None, Gio.DBusCallFlags.NONE, -1, None, _done,
        )

    def _emit_signal(self, path: str, iface: str, name: str, body) -> None:
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(None, path, iface, name, body)
        except GLib.Error as exc:
            logger.debug("Tray: failed to emit %s: %s", name, exc)

    # ------------------------------------------------------------------
    # StatusNotifierItem interface
    # ------------------------------------------------------------------

    def _sni_get_property(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "meeting-recorder"),
            "Title": GLib.Variant("s", "Meeting Recorder"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("u", 0),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", _MENU_PATH),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)", ("", [], "Meeting Recorder", "")
            ),
        }
        return values.get(prop)

    def _sni_method(self, _conn, _sender, _path, _iface, method, _params, invocation):
        # Left-click → focus the window. Runs on the GTK main thread (the D-Bus
        # connection is owned by the main-loop context), so call directly.
        if method == "Activate":
            self._window.present_window()
        invocation.return_value(None)

    # ------------------------------------------------------------------
    # com.canonical.dbusmenu interface
    # ------------------------------------------------------------------

    def _menu_get_property(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(prop)

    def _menu_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            # The `av` children are variant ('v') leaves, so each must be a
            # GLib.Variant; the enclosing root struct is passed as a plain tuple
            # (PyGObject builds the struct field from raw values, not a pre-made
            # Variant).
            children = [
                GLib.Variant("(ia{sv}av)", (item["id"], self._item_props(item), []))
                for item in self._menu_items
            ]
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))", (self._revision, (0, {}, children)))
            )
        elif method == "GetGroupProperties":
            ids, _props = params.unpack()
            wanted = set(ids)
            result = [
                (item["id"], self._item_props(item))
                for item in self._menu_items
                if not wanted or item["id"] in wanted
            ]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            item = self._find_item(item_id)
            val = self._item_props(item).get(name) if item else None
            invocation.return_value(
                GLib.Variant("(v)", (val or GLib.Variant("s", ""),))
            )
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._on_menu_clicked(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            events, = params.unpack()
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self._on_menu_clicked(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _item_props(self, item: dict) -> dict:
        if item.get("type") == "separator":
            # Some dbusmenu clients warn on missing standard boolean props, so
            # declare them explicitly. build_menu_model only emits separators
            # where one belongs, so a tray separator is always visible.
            return {
                "type": GLib.Variant("s", "separator"),
                "visible": GLib.Variant("b", True),
                "enabled": GLib.Variant("b", True),
            }
        return {
            "label": GLib.Variant("s", item.get("label", "")),
            "enabled": GLib.Variant("b", item.get("enabled", True)),
            "visible": GLib.Variant("b", True),
        }

    # ------------------------------------------------------------------
    # Menu model materialisation + action dispatch
    # ------------------------------------------------------------------

    def _materialize_menu(self) -> list[dict]:
        """Assign stable integer ids (1..N) to the pure menu model."""
        model = build_menu_model(self._recording_state, self._jobs)
        for i, item in enumerate(model, start=1):
            item["id"] = i
        return model

    def _find_item(self, item_id: int) -> dict | None:
        for item in self._menu_items:
            if item["id"] == item_id:
                return item
        return None

    def _on_menu_clicked(self, item_id: int) -> None:
        item = self._find_item(item_id)
        if not item or item.get("type") != "action" or not item.get("enabled", True):
            return
        self._dispatch_action(item["action"], item.get("job_index"))

    def _dispatch_action(self, action: str, job_index=None) -> None:
        from ..utils.glib_bridge import idle_call
        w = self._window
        handlers = {
            "record_headphones": w.on_record_headphones_clicked,
            "record_speaker": w.on_record_speaker_clicked,
            "use_existing": w.on_use_existing_clicked,
            "pause": w.on_pause_clicked,
            "resume": w.on_resume_clicked,
            "stop": w.on_stop_clicked,
            "cancel_save": w.on_cancel_save_clicked,
            "cancel": w.on_cancel_clicked,
        }
        if action in handlers:
            idle_call(handlers[action])
        elif action == "show":
            # Direct call keeps the click context for window focusing.
            w.present_window()
        elif action == "quit":
            self._quit()
        elif action == "cancel_job":
            if job_index is not None and 0 <= job_index < len(self._jobs):
                # cancel_fn already marshals onto the main thread via idle_call.
                self._jobs[job_index][1]()

    def _quit(self) -> None:
        from ..utils.glib_bridge import idle_call

        def _do_quit():
            if self._window._recorder:
                self._window._recorder.stop()
            self._window.get_application().quit()

        idle_call(_do_quit)
