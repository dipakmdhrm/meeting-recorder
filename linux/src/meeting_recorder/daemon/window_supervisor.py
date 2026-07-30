"""
Supervises the single GTK window child process.

The daemon spawns the window on demand (fork+exec, never a bare fork — the
daemon has threads, D-Bus connections and a live ffmpeg subprocess that must not
be inherited). Only one window may exist at a time: if one is already alive,
"open" presents it (the daemon emits a PresentWindow signal the running window
listens for) rather than spawning a second.

The spawn/present decision and the alive bookkeeping are isolated here with
injected ``spawn_fn``/``present_fn`` so they are unit-testable without a real
subprocess. Child-exit is reported back via ``on_child_exit`` so a crashed or
closed window resets the state instead of blocking future opens.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class WindowSupervisor:
    def __init__(self, spawn_fn: Callable[[], None], present_fn: Callable[[], None]) -> None:
        self._spawn_fn = spawn_fn
        self._present_fn = present_fn
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive

    def open(self) -> None:
        """Spawn the window, or present the existing one."""
        if self._alive:
            logger.debug("Window already open — presenting existing instance")
            self._present_fn()
            return
        logger.info("Spawning window child process")
        self._spawn_fn()
        self._alive = True

    def on_child_exit(self) -> None:
        """The window process exited (closed or crashed) — allow a fresh spawn."""
        if self._alive:
            logger.debug("Window child exited")
        self._alive = False
