"""
System tray icon — thin wrapper delegating to platform tray backends.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INDICATOR_AVAILABLE = False
try:
    from ..platform.tray.appindicator import AppIndicatorTray
    _INDICATOR_AVAILABLE = True
except Exception:
    pass


class TrayIcon:
    def __init__(self, window) -> None:
        self._impl = None
        if _INDICATOR_AVAILABLE:
            try:
                self._impl = AppIndicatorTray(window)
                return
            except Exception:
                logger.debug("AppIndicator init failed, trying pystray")
        try:
            from ..platform.tray.pystray_backend import PystrayBackend
            self._impl = PystrayBackend(window)
        except Exception:
            logger.warning("No tray backend available")

    def update(self, recording_state: str, jobs: list) -> None:
        if self._impl:
            self._impl.update(recording_state, jobs)
