from __future__ import annotations

import logging
from pathlib import Path

from .base import TrayBackend

logger = logging.getLogger(__name__)

_ICONS_DIR = str(Path(__file__).resolve().parent.parent.parent / "assets" / "icons")

_INDICATOR_LIB = None
try:
    import gi
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3
    _INDICATOR_LIB = AyatanaAppIndicator3
except (ImportError, ValueError):
    pass

if _INDICATOR_LIB is None:
    try:
        import gi
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AyatanaAppIndicator3
        _INDICATOR_LIB = AyatanaAppIndicator3
    except (ImportError, ValueError):
        pass

if _INDICATOR_LIB is None:
    raise ImportError("Neither AyatanaAppIndicator3 nor AppIndicator3 is available")


class AppIndicatorTray(TrayBackend):
    """AyatanaAppIndicator3-based tray implementation."""

    def __init__(self, window) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self._window = window
        self._Gtk = Gtk
        self._recording_state = "idle"
        self._jobs: list = []

        self._blink_timer_id = None
        self._blink_on = True

        self._indicator = _INDICATOR_LIB.Indicator.new(
            "meeting-recorder",
            "meeting-recorder",
            _INDICATOR_LIB.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_icon_theme_path(_ICONS_DIR)
        self._indicator.set_status(_INDICATOR_LIB.IndicatorStatus.ACTIVE)
        self._menu = Gtk.Menu()
        self._build_menu()

    def _build_menu(self) -> None:
        from gi.repository import Gtk
        for child in self._menu.get_children():
            self._menu.remove(child)

        state = self._recording_state
        jobs = self._jobs

        if state == "idle":
            self._add_item("Record (Headphones)", self._on_start_headphones)
            self._add_item("Record (Speaker)", self._on_start_speaker)
            self._add_item("Use Existing Recording", self._on_use_existing)
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

        if jobs:
            self._menu.append(Gtk.SeparatorMenuItem())
            header = Gtk.MenuItem(label=f"Processing ({len(jobs)} active)")
            header.set_sensitive(False)
            self._menu.append(header)
            for label, cancel_fn in jobs:
                self._add_item(f"  Cancel: {label}", cancel_fn)

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

    def update(self, recording_state: str, jobs: list) -> None:
        self._recording_state = recording_state
        self._jobs = jobs
        self._build_menu()

        # Stop any existing blink timer
        if self._blink_timer_id is not None:
            from gi.repository import GLib
            GLib.source_remove(self._blink_timer_id)
            self._blink_timer_id = None

        if recording_state == "recording":
            self._blink_on = True
            self._indicator.set_icon_full("meeting-recorder-recording", "recording")
            from gi.repository import GLib
            self._blink_timer_id = GLib.timeout_add(700, self._blink_tick)
        else:
            self._indicator.set_icon_full("meeting-recorder", "idle")

    def _blink_tick(self) -> bool:
        if self._recording_state != "recording":
            self._blink_timer_id = None
            return False
        self._blink_on = not self._blink_on
        icon = "meeting-recorder-recording" if self._blink_on else "meeting-recorder-recording-dim"
        self._indicator.set_icon_full(icon, "recording")
        return True

    def _on_start_headphones(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_record_headphones_clicked)

    def _on_start_speaker(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_record_speaker_clicked)

    def _on_use_existing(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_use_existing_clicked)

    def _on_pause(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_pause_clicked)

    def _on_resume(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_resume_clicked)

    def _on_stop(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_stop_clicked)

    def _on_cancel_save(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_cancel_save_clicked)

    def _on_cancel(self) -> None:
        from ...utils.glib_bridge import idle_call
        idle_call(self._window.on_cancel_clicked)

    def _on_show(self) -> None:
        from gi.repository import GLib
        GLib.idle_add(self._window.present)

    def _on_quit(self) -> None:
        from gi.repository import GLib
        def _do_quit():
            if self._window._recorder:
                self._window._recorder.stop()
            self._window.get_application().quit()
        GLib.idle_add(_do_quit)
