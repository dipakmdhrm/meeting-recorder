"""
Implements a system tray icon for the application. It provides quick-access controls for recording and displays the status of active background processing jobs.

Three backends, tried in priority order (see ``_choose_tray_backend``):
1. ``Gtk.StatusIcon`` — the legacy XEmbed tray. The only backend that exposes
   *separate* left-click (``activate``) and right-click (``popup-menu``) signals,
   so left-click focuses the window while right-click opens the menu. Used only
   when it actually embeds in a system tray (traditional desktops: XFCE, MATE,
   Cinnamon, KDE/X11, LXQt, i3+trayer, …).
2. ``AyatanaAppIndicator3`` — used on GNOME/Wayland where no XEmbed tray exists.
   AppIndicator cannot deliver a left-click action (menu opens on any click).
3. pystray — last-resort fallback.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

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


def _pystray_available() -> bool:
    """Whether the pystray fallback can be constructed."""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _choose_tray_backend(
    statusicon_embedded: bool,
    indicator_available: bool,
    pystray_available: bool,
) -> str | None:
    """Pure backend-selection policy, in priority order.

    Returns one of ``"statusicon"`` | ``"indicator"`` | ``"pystray"`` | ``None``.

    Gtk.StatusIcon is preferred only when it actually embedded in a system tray
    (that is the only path that gives a custom left-click); otherwise we fall
    back to the AppIndicator backend, then pystray.
    """
    if statusicon_embedded:
        return "statusicon"
    if indicator_available:
        return "indicator"
    if pystray_available:
        return "pystray"
    return None


# How long to wait for a freshly-created Gtk.StatusIcon to embed in the tray
# before deciding it won't and falling back. is_embedded() is only reliable
# after the tray manager has had a main-loop round-trip to respond.
_EMBED_PROBE_MS = 600


class TrayIcon:
    """
    System tray icon that reflects recording state and lists background jobs.

    Prefers a Gtk.StatusIcon (separate left/right click) when a legacy system
    tray is present, falling back to AppIndicator, then pystray.
    """

    def __init__(self, window) -> None:
        self._window = window
        self._impl: _IndicatorTray | _StatusIconTray | _PystrayTray | None = None
        self._recording_state = "idle"
        self._jobs: list = []
        self._init_backend()

    def update(self, recording_state: str, jobs: list) -> None:
        """
        Update tray icon and menu.

        recording_state: "idle" | "recording" | "paused"
        jobs: list of (label: str, cancel_fn: callable) for active processing jobs
        """
        self._recording_state = recording_state
        self._jobs = jobs
        if self._impl:
            self._impl.update(recording_state, jobs)

    # ------------------------------------------------------------------
    def _init_backend(self) -> None:
        # Try Gtk.StatusIcon first and show it provisionally; if it doesn't
        # embed within the probe window (e.g. GNOME/Wayland) it's invisible
        # anyway, so swapping it for the AppIndicator backend causes no flicker.
        try:
            provisional = _StatusIconTray(self._window)
        except Exception as exc:
            logger.debug("Gtk.StatusIcon backend unavailable: %s", exc)
            self._build(_choose_tray_backend(False, _INDICATOR_AVAILABLE, _pystray_available()))
            return

        self._impl = provisional
        from ..utils.glib_bridge import timeout_call
        timeout_call(_EMBED_PROBE_MS, self._confirm_or_fallback, provisional)

    def _confirm_or_fallback(self, provisional: "_StatusIconTray") -> None:
        try:
            embedded = provisional.is_embedded()
        except Exception:
            embedded = False

        backend = _choose_tray_backend(
            embedded, _INDICATOR_AVAILABLE, _pystray_available()
        )
        if backend == "statusicon":
            logger.info("Tray: using Gtk.StatusIcon backend (left-click focuses window)")
            return

        # Did not embed — discard the invisible status icon and fall back.
        logger.info("Tray: Gtk.StatusIcon did not embed — falling back to %s", backend)
        provisional.destroy()
        if self._impl is provisional:
            self._impl = None
        self._build(backend)

    def _build(self, backend: str | None) -> None:
        if backend == "indicator":
            self._impl = _IndicatorTray(self._window)
        elif backend == "pystray":
            try:
                self._impl = _PystrayTray(self._window)
            except Exception as exc:
                logger.warning("pystray unavailable: %s", exc)
                self._impl = None
        else:
            logger.warning("No system tray backend available")
            self._impl = None

        if self._impl is not None:
            self._impl.update(self._recording_state, self._jobs)


class _GtkMenuTray:
    """
    Shared GTK menu construction, state handling, and action handlers for the
    indicator and Gtk.StatusIcon backends. Subclasses differ only in how the
    icon is set (``_set_icon``), how the menu is attached after a rebuild
    (``_on_menu_built``), and how clicks are wired.
    """

    def __init__(self, window) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self._window = window
        self._Gtk = Gtk
        self._recording_state = "idle"
        self._jobs: list = []
        self._menu = Gtk.Menu()
        self._build_menu()

    # -- menu ----------------------------------------------------------
    def _build_menu(self) -> None:
        from gi.repository import Gtk
        for child in self._menu.get_children():
            self._menu.remove(child)

        state = self._recording_state
        jobs = self._jobs

        # Recording controls — always reflect current recording state
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

        # Background jobs section (only shown when jobs are active)
        if jobs:
            self._menu.append(Gtk.SeparatorMenuItem())
            header = Gtk.MenuItem(label=f"Processing ({len(jobs)} active)")
            header.set_sensitive(False)
            self._menu.append(header)
            for label, cancel_fn in jobs:
                self._add_item(f"  Cancel: {label}", cancel_fn)

        # Footer
        self._menu.append(Gtk.SeparatorMenuItem())
        self._add_item("Show Window", self._on_show)
        self._add_item("Quit", self._on_quit)

        self._menu.show_all()
        self._on_menu_built()

    def _on_menu_built(self) -> None:
        """Hook: re-attach the menu after a rebuild. Default is a no-op."""

    def _add_item(self, label: str, callback) -> None:
        from gi.repository import Gtk
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda *_: callback())
        self._menu.append(item)

    def update(self, recording_state: str, jobs: list) -> None:
        self._recording_state = recording_state
        self._jobs = jobs
        self._build_menu()

        # Icon priority: recording > paused > jobs processing > idle
        if recording_state == "recording":
            icon = "media-record"
        elif recording_state == "paused":
            icon = "media-playback-pause"
        elif jobs:
            icon = "system-run"
        else:
            icon = "audio-input-microphone"

        self._set_icon(icon, recording_state)

    def _set_icon(self, icon_name: str, state: str) -> None:
        raise NotImplementedError

    # -- action handlers (shared) -------------------------------------
    def _on_start_headphones(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_record_headphones_clicked)

    def _on_start_speaker(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_record_speaker_clicked)

    def _on_use_existing(self) -> None:
        from ..utils.glib_bridge import idle_call
        idle_call(self._window.on_use_existing_clicked)

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

    def _on_show(self) -> None:
        # Runs on the GTK main thread within the menu "activate" event — call
        # present_window() directly. Deferring via idle_add would clear the GDK
        # event context, zeroing Gtk.get_current_event_time() and defeating the
        # X11 focus-stealing-prevention timestamp.
        self._window.present_window()

    def _on_quit(self) -> None:
        from gi.repository import GLib
        def _do_quit():
            if self._window._recorder:
                self._window._recorder.stop()
            self._window.get_application().quit()
        GLib.idle_add(_do_quit)


class _IndicatorTray(_GtkMenuTray):
    """AyatanaAppIndicator3-based tray. Menu opens on any click (no left-click
    action is possible on this backend)."""

    def __init__(self, window) -> None:
        self._indicator = AyatanaAppIndicator3.Indicator.new(
            "meeting-recorder",
            "audio-input-microphone",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        super().__init__(window)

    def _on_menu_built(self) -> None:
        self._indicator.set_menu(self._menu)

    def _set_icon(self, icon_name: str, state: str) -> None:
        self._indicator.set_icon_full(icon_name, state)


class _StatusIconTray(_GtkMenuTray):
    """Gtk.StatusIcon (legacy XEmbed) tray. Left-click focuses the window,
    right-click opens the context menu — separate signals."""

    def __init__(self, window) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self._status_icon = Gtk.StatusIcon.new_from_icon_name("audio-input-microphone")
        self._status_icon.set_title("Meeting Recorder")
        self._status_icon.connect("activate", self._on_activate)
        self._status_icon.connect("popup-menu", self._on_popup_menu)
        super().__init__(window)

    # Left-click → bring the window up and focus it.
    def _on_activate(self, _status_icon) -> None:
        # Runs on the GTK main thread within the click event — call directly so
        # Gtk.get_current_event_time() keeps the real timestamp (idle_add would
        # zero it and defeat X11 focus-stealing prevention).
        self._window.present_window()

    # Right-click → context menu, anchored to the tray icon.
    def _on_popup_menu(self, status_icon, button, activate_time) -> None:
        # position_menu anchors the menu next to the icon for both mouse and
        # keyboard triggers, unlike popup_at_pointer (which follows the cursor).
        self._menu.popup(
            None, None, self._Gtk.StatusIcon.position_menu,
            status_icon, button, activate_time,
        )

    def _set_icon(self, icon_name: str, state: str) -> None:
        self._status_icon.set_from_icon_name(icon_name)

    def is_embedded(self) -> bool:
        return bool(self._status_icon.is_embedded())

    def destroy(self) -> None:
        if self._status_icon is not None:
            self._status_icon.set_visible(False)
            self._status_icon = None


class _PystrayTray:
    """pystray fallback implementation."""

    def __init__(self, window) -> None:
        import pystray
        from PIL import Image, ImageDraw

        self._window = window
        self._recording_state = "idle"
        self._jobs: list = []
        self._pystray = pystray

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

        state = self._recording_state
        jobs = self._jobs
        items = []

        if state == "idle":
            items.append(pystray.MenuItem(
                "Record (Headphones)",
                lambda *_: idle_call(self._window.on_record_headphones_clicked),
            ))
            items.append(pystray.MenuItem(
                "Record (Speaker)",
                lambda *_: idle_call(self._window.on_record_speaker_clicked),
            ))
            items.append(pystray.MenuItem(
                "Use Existing Recording",
                lambda *_: idle_call(self._window.on_use_existing_clicked),
            ))
        elif state == "recording":
            items.append(pystray.MenuItem(
                "Pause Recording",
                lambda *_: idle_call(self._window.on_pause_clicked),
            ))
            items.append(pystray.MenuItem(
                "Stop Recording",
                lambda *_: idle_call(self._window.on_stop_clicked),
            ))
        elif state == "paused":
            items.append(pystray.MenuItem(
                "Resume Recording",
                lambda *_: idle_call(self._window.on_resume_clicked),
            ))
            items.append(pystray.MenuItem(
                "Stop Recording",
                lambda *_: idle_call(self._window.on_stop_clicked),
            ))

        if jobs:
            items.append(pystray.MenuItem(
                f"Processing ({len(jobs)} active)", lambda *_: None, enabled=False
            ))
            for label, cancel_fn in jobs:
                items.append(pystray.MenuItem(f"  Cancel: {label}", cancel_fn))

        items.append(pystray.MenuItem(
            "Show Window", lambda *_: idle_call(self._window.present_window)
        ))
        items.append(pystray.MenuItem("Quit", lambda *_: self._do_quit()))

        return pystray.Menu(*items)

    def _do_quit(self) -> None:
        from ..utils.glib_bridge import idle_call
        def _quit():
            if self._window._recorder:
                self._window._recorder.stop()
            self._window.get_application().quit()
        idle_call(_quit)

    def update(self, recording_state: str, jobs: list) -> None:
        self._recording_state = recording_state
        self._jobs = jobs
        self._icon.menu = self._build_menu()
        self._icon.update_menu()
