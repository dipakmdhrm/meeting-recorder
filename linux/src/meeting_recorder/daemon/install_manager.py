"""
Tracks the model/engine installs running in the daemon.

Owns the set of in-flight ``--install`` children keyed by ``install_key`` so the
same request dedups, different models/vendors run concurrently, and a reopened
window can re-attach to whatever is still running (``running_json``). Spawning is
delegated to an injected ``InstallLauncher`` (real one built lazily so this module
imports headless); the start/dedup/progress/finished bookkeeping here is pure and
unit-tested.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from ..core.install_spec import install_key, spec_from_json

logger = logging.getLogger(__name__)


class InstallManager:
    def __init__(
        self,
        *,
        on_progress: Callable[[str, str], None],
        on_finished: Callable[[str, bool, str], None],
        launcher: object | None = None,
    ) -> None:
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._launcher = launcher
        # key -> {"handle": ..., "status": str}
        self._running: dict[str, dict] = {}

    def start(self, spec_json: str) -> str:
        """Start an install (or no-op if the same key is already running).

        Returns the install key. Raises ValueError on a malformed spec.
        """
        spec = spec_from_json(spec_json)
        key = install_key(spec)
        if key in self._running:
            logger.info("Install %s already running — ignoring duplicate request", key)
            return key
        self._running[key] = {"handle": None, "status": "Starting…"}
        handle = self._get_launcher().launch(
            spec_json,
            on_status=lambda text, k=key: self._progress(k, text),
            on_finished=lambda ok, msg, k=key: self._finished(k, ok, msg),
        )
        self._running[key]["handle"] = handle
        logger.info("Started install %s", key)
        return key

    def running_json(self) -> str:
        """JSON list of currently-running installs (key + last status)."""
        return json.dumps([{"key": k, "status": v["status"]} for k, v in self._running.items()])

    # ------------------------------------------------------------------

    def _progress(self, key: str, text: str) -> None:
        entry = self._running.get(key)
        if entry is not None:
            entry["status"] = text
        self._on_progress(key, text)

    def _finished(self, key: str, ok: bool, message: str) -> None:
        self._running.pop(key, None)
        logger.info("Install %s finished (ok=%s)", key, ok)
        self._on_finished(key, ok, message)

    def _get_launcher(self):
        if self._launcher is None:
            from .installer import InstallLauncher

            self._launcher = InstallLauncher()
        return self._launcher
