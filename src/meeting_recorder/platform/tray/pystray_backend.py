from __future__ import annotations

import logging
import threading

from .base import TrayBackend

logger = logging.getLogger(__name__)

# Colors
_MIC_COLOR = (220, 220, 220)
_DOT_RED = (229, 57, 53)
_DOT_MAROON = (93, 22, 22)


def _draw_mic_icon(size: int = 64, dot_color: tuple | None = None):
    """Draw a microphone tray icon with optional recording dot."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 64  # scale factor

    # Mic capsule (rounded rect)
    draw.rounded_rectangle(
        [int(21 * s), int(5 * s), int(43 * s), int(33 * s)],
        radius=int(11 * s),
        fill=_MIC_COLOR,
    )

    # Holder arc (U-shape under capsule)
    # Draw as the bottom half of an ellipse
    bbox = [int(15 * s), int(12 * s), int(49 * s), int(50 * s)]
    draw.arc(bbox, start=0, end=180, fill=_MIC_COLOR, width=max(1, int(4.5 * s)))

    # Stem (short vertical line from arc bottom to base)
    cx = int(32 * s)
    draw.line(
        [cx, int(50 * s), cx, int(55 * s)],
        fill=_MIC_COLOR, width=max(1, int(4.5 * s)),
    )

    # Base (horizontal line)
    draw.line(
        [int(23 * s), int(58 * s), int(41 * s), int(58 * s)],
        fill=_MIC_COLOR, width=max(1, int(4.5 * s)),
    )

    # Recording dot overlay
    if dot_color:
        r = int(10 * s)
        cx_dot, cy_dot = int(52 * s), int(52 * s)
        draw.ellipse(
            [cx_dot - r, cy_dot - r, cx_dot + r, cy_dot + r],
            fill=dot_color,
        )

    return img


class PystrayBackend(TrayBackend):
    """pystray fallback implementation with recording dot blink."""

    def __init__(self, window) -> None:
        import pystray

        self._window = window
        self._recording_state = "idle"
        self._jobs: list = []
        self._pystray = pystray

        # Pre-render icon variants
        self._icon_idle = _draw_mic_icon()
        self._icon_rec_bright = _draw_mic_icon(dot_color=_DOT_RED)
        self._icon_rec_dim = _draw_mic_icon(dot_color=_DOT_MAROON)

        self._blink_on = True
        self._blink_timer: threading.Timer | None = None

        self._icon = pystray.Icon(
            "meeting-recorder",
            self._icon_idle,
            "Meeting Recorder",
            menu=self._build_menu(),
        )
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _build_menu(self):
        import pystray
        from ...utils.glib_bridge import idle_call

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
            "Show Window", lambda *_: idle_call(self._window.present)
        ))
        items.append(pystray.MenuItem("Quit", lambda *_: self._do_quit()))

        return pystray.Menu(*items)

    def _do_quit(self) -> None:
        from ...utils.glib_bridge import idle_call
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

        # Stop existing blink timer
        if self._blink_timer is not None:
            self._blink_timer.cancel()
            self._blink_timer = None

        if recording_state == "recording":
            self._blink_on = True
            self._icon.icon = self._icon_rec_bright
            self._schedule_blink()
        else:
            self._icon.icon = self._icon_idle

    def _schedule_blink(self) -> None:
        self._blink_timer = threading.Timer(0.7, self._blink_tick)
        self._blink_timer.daemon = True
        self._blink_timer.start()

    def _blink_tick(self) -> None:
        if self._recording_state != "recording":
            return
        self._blink_on = not self._blink_on
        self._icon.icon = self._icon_rec_bright if self._blink_on else self._icon_rec_dim
        self._schedule_blink()
