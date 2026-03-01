"""System tray icon using AyatanaAppIndicator3 with pystray fallback."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Try AyatanaAppIndicator3 first
_INDICATOR_AVAILABLE = False
try:
    import gi
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3
    _INDICATOR_AVAILABLE = True
except (ImportError, ValueError):
    pass

if not _INDICATOR_AVAILABLE:
    try:
        import gi
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AyatanaAppIndicator3
        _INDICATOR_AVAILABLE = True
    except (ImportError, ValueError):
        pass


class TrayIcon:
    """
    System tray icon that reflects recording state and provides a context menu.
    Falls back to pystray if AyatanaAppIndicator3 is unavailable.
    """

    def __init__(self, window) -> None:
        self._window = window
        self._impl: _IndicatorTray | _PystrayTray | None = None

        if _INDICATOR_AVAILABLE:
            self._impl = _IndicatorTray(window)
        else:
            try:
                self._impl = _PystrayTray(window)
            except Exception as exc:
                logger.warning("pystray unavailable: %s", exc)
                raise

    def set_state(self, state: str) -> None:
        """Update tray icon/menu for given state: idle, recording, paused, processing."""
        if self._impl:
            self._impl.set_state(state)


class _IndicatorTray:
    """AyatanaAppIndicator3-based tray implementation."""

    def __init__(self, window) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self._window = window
        self._Gtk = Gtk
        self._state = "idle"

        self._indicator = AyatanaAppIndicator3.Indicator.new(
            "meeting-recorder",
            "audio-input-microphone",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self._menu = Gtk.Menu()
        self._build_menu()

    def _build_menu(self) -> None:
        from gi.repository import Gtk
        # Clear existing items
        for child in self._menu.get_children():
            self._menu.remove(child)

        state = self._state

        if state == "idle":
            self._add_item("Start Recording", self._on_start)
        elif state == "recording":
            self._add_item("Pause Recording", self._on_pause)
            self._add_item("Stop Recording", self._on_stop)
            self._add_item("Cancel (save recording)", self._on_cancel_save)
            self._add_item("Cancel", self._on_cancel)
        elif state == "paused":
            self._add_item("Resume Recording", self._on_resume)
            self._add_item("Stop Recording", self._on_stop)
            self._add_item("Cancel (save recording)", self._on_cancel_save)
            self._add_item("Cancel", self._on_cancel)
        elif state == "processing":
            item = Gtk.MenuItem(label="Processing...")
            item.set_sensitive(False)
            self._menu.append(item)
            self._add_item("Cancel Processing", self._on_cancel_processing)

        # Separator + Show Window + Quit
        self._menu.append(Gtk.SeparatorMenuItem())
        self._add_item("Show Window", self._on_show)
        self._add_item("Quit", self._on_quit)

        self._menu.show_all()
        self._indicator.set_menu(self._menu)

    def _add_item(self, label: str, callback) -> None:
        from gi.repository import Gtk
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda *_: callback())
        self._menu.append(item)

    def set_state(self, state: str) -> None:
        self._state = state
        self._build_menu()
        # Update icon
        icons = {
            "idle": "audio-input-microphone",
            "recording": "media-record",
            "paused": "media-playback-pause",
            "processing": "system-run",
        }
        self._indicator.set_icon_full(
            icons.get(state, "audio-input-microphone"), state
        )

    def _on_start(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_record_clicked)

    def _on_pause(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_pause_clicked)

    def _on_resume(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_resume_clicked)

    def _on_stop(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_stop_clicked)

    def _on_cancel_save(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_cancel_save_clicked)

    def _on_cancel(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_cancel_clicked)

    def _on_cancel_processing(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_cancel_processing_clicked)

    def _on_show(self) -> None:
        from gi.repository import GLib
        GLib.idle_add(self._window.present)

    def _on_quit(self) -> None:
        from gi.repository import GLib
        GLib.idle_add(self._window.get_application().quit)


class _PystrayTray:
    """pystray fallback implementation."""

    def __init__(self, window) -> None:
        import pystray
        from PIL import Image, ImageDraw

        self._window = window
        self._state = "idle"
        self._pystray = pystray

        # Create a simple icon image
        img = Image.new("RGB", (64, 64), color=(64, 64, 64))
        draw = ImageDraw.Draw(img)
        draw.ellipse([16, 16, 48, 48], fill=(220, 50, 50))
        self._icon_image = img

        self._icon = pystray.Icon(
            "meeting-recorder",
            self._icon_image,
            "Meeting Recorder",
            menu=self._build_menu(),
        )
        import threading
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _build_menu(self):
        import pystray
        from ..utils.glib_bridge import idle_call

        state = self._state
        items = []

        if state == "idle":
            items.append(pystray.MenuItem("Start Recording",
                lambda *_: idle_call(self._window.on_record_clicked)))
        elif state == "recording":
            items.append(pystray.MenuItem("Pause Recording",
                lambda *_: idle_call(self._window.on_pause_clicked)))
            items.append(pystray.MenuItem("Stop Recording",
                lambda *_: idle_call(self._window.on_stop_clicked)))
        elif state == "paused":
            items.append(pystray.MenuItem("Resume Recording",
                lambda *_: idle_call(self._window.on_resume_clicked)))
            items.append(pystray.MenuItem("Stop Recording",
                lambda *_: idle_call(self._window.on_stop_clicked)))
        elif state == "processing":
            items.append(pystray.MenuItem("Processing...",
                lambda *_: None, enabled=False))
            items.append(pystray.MenuItem("Cancel Processing",
                lambda *_: idle_call(self._window.on_cancel_processing_clicked)))

        items.append(pystray.MenuItem("Show Window",
            lambda *_: idle_call(self._window.present)))
        items.append(pystray.MenuItem("Quit",
            lambda *_: idle_call(self._window.get_application().quit)))

        return pystray.Menu(*items)

    def set_state(self, state: str) -> None:
        self._state = state
        self._icon.menu = self._build_menu()
        self._icon.update_menu()
