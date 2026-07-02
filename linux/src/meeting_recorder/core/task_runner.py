"""
The application's single background-work facility.

Every piece of off-main-thread work goes through :meth:`TaskRunner.submit`
instead of ad-hoc ``threading.Thread(...)`` spawns. This gives the app three
guarantees the raw threads never had:

- results and errors are always routed back to the GTK main thread via
  callbacks, and a worker exception can never disappear silently — if no
  ``on_error`` is given the traceback is still logged;
- callbacks scheduled on the main loop are wrapped so an exception inside
  them is logged instead of being swallowed by GLib;
- the app can shut down gracefully: :meth:`TaskRunner.shutdown` joins running
  tasks with a bounded grace period and reports what had to be abandoned.

Worker threads are daemon threads by design: a wedged external process (for
example a hung ffmpeg) must not be able to block application exit forever.
The grace period in ``shutdown()`` is what makes the common case clean.

The main-thread scheduler is injectable so this module stays importable and
fully testable without GLib/GTK.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Schedules a zero-argument callable onto the UI main loop.
MainThreadScheduler = Callable[[Callable[[], None]], None]


def _glib_scheduler(callback: Callable[[], None]) -> None:
    """Default scheduler: run *callback* once on the GLib main loop."""
    # Imported lazily so the module (and its tests) work without PyGObject.
    from gi.repository import GLib

    def _once() -> bool:
        callback()
        return False  # GLib.SOURCE_REMOVE — do not reschedule

    GLib.idle_add(_once)


class CancelToken:
    """Cooperative cancellation flag shared between the UI and a worker.

    Workers must check :attr:`cancelled` between stages; cancellation is a
    request, not preemption.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class TaskRunner:
    """Runs background work on tracked daemon threads with main-thread callbacks."""

    def __init__(self, schedule_on_main: MainThreadScheduler | None = None) -> None:
        self._schedule_on_main = schedule_on_main or _glib_scheduler
        self._lock = threading.Lock()
        self._tasks: dict[threading.Thread, str] = {}
        self._shutting_down = False

    # ------------------------------------------------------------------

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        description: str = "task",
    ) -> None:
        """Run ``fn(*args)`` on a worker thread.

        ``on_done(result)`` / ``on_error(exception)`` are invoked on the main
        thread. A worker exception with no ``on_error`` is logged with its
        traceback — never swallowed.
        """
        with self._lock:
            if self._shutting_down:
                raise RuntimeError(f"TaskRunner is shut down; refusing task {description!r}")
            thread = threading.Thread(
                target=self._run_task,
                args=(fn, args, on_done, on_error, description),
                daemon=True,
                name=f"task-runner: {description}",
            )
            self._tasks[thread] = description
        thread.start()

    def active_descriptions(self) -> list[str]:
        """Descriptions of tasks currently running (for logging/diagnostics)."""
        with self._lock:
            return [d for t, d in self._tasks.items() if t.is_alive()]

    def shutdown(self, grace_seconds: float = 10.0) -> list[str]:
        """Stop accepting tasks and join running ones for up to *grace_seconds*.

        Returns the descriptions of tasks that were still running when the
        grace period expired (they keep running as daemons and die with the
        process).
        """
        with self._lock:
            self._shutting_down = True
            pending = [t for t in self._tasks if t.is_alive()]

        remaining = grace_seconds
        for thread in pending:
            if remaining <= 0:
                break
            start = time.monotonic()
            thread.join(timeout=remaining)
            remaining -= time.monotonic() - start

        with self._lock:
            abandoned = [d for t, d in self._tasks.items() if t.is_alive()]
        if abandoned:
            logger.warning(
                "Abandoning %d background task(s) after %.0fs grace: %s",
                len(abandoned),
                grace_seconds,
                ", ".join(abandoned),
            )
        return abandoned

    # ------------------------------------------------------------------

    def _run_task(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[Exception], None] | None,
        description: str,
    ) -> None:
        # The task stays registered until its callback has been dispatched, so
        # shutdown() waits for result delivery too, not just for fn() itself.
        try:
            try:
                result = fn(*args)
            except Exception as exc:
                logger.error("Background task %r failed:\n%s", description, traceback.format_exc())
                if on_error is not None:
                    self._dispatch(on_error, exc, description)
            else:
                if on_done is not None:
                    self._dispatch(on_done, result, description)
        finally:
            with self._lock:
                self._tasks.pop(threading.current_thread(), None)

    def _dispatch(self, callback: Callable[[Any], None], value: Any, description: str) -> None:
        """Schedule *callback(value)* on the main thread, logging its errors."""

        def _guarded() -> None:
            try:
                callback(value)
            except Exception:
                logger.error(
                    "Main-thread callback for task %r raised:\n%s",
                    description,
                    traceback.format_exc(),
                )

        self._schedule_on_main(_guarded)
