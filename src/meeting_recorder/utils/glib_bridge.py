"""
Provides thread-safe wrappers for interacting with the GLib main loop. These utilities ensure that UI updates from background threads are correctly scheduled on the GTK main thread.
"""

from __future__ import annotations

import threading
from typing import Callable, Any

from gi.repository import GLib


def idle_call(func: Callable, *args: Any) -> None:
    """Schedule func(*args) to run on the GTK main thread via GLib.idle_add."""
    def _wrapper():
        func(*args)
        # GLib.SOURCE_REMOVE (False) tells GLib not to re-schedule this callback.
        # Returning SOURCE_CONTINUE (True) would call it again on every idle cycle.
        return GLib.SOURCE_REMOVE
    GLib.idle_add(_wrapper)


def timeout_call(delay_ms: int, func: Callable, *args: Any) -> int:
    """Schedule func(*args) once after delay_ms milliseconds. Returns source id."""
    def _wrapper():
        func(*args)
        return GLib.SOURCE_REMOVE
    return GLib.timeout_add(delay_ms, _wrapper)


def assert_main_thread() -> None:
    """Assert we are on the GTK main thread. Call at top of UI update methods."""
    assert threading.current_thread() is threading.main_thread(), (
        "UI update called from non-main thread — use idle_call() instead"
    )
