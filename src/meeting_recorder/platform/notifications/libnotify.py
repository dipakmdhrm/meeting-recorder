from __future__ import annotations

import logging
import shutil
import subprocess

from .base import Notifier

logger = logging.getLogger(__name__)


class LibnotifyNotifier(Notifier):
    """Desktop notifications via notify-send (libnotify)."""

    def __init__(self, app_name: str = "Meeting Recorder") -> None:
        self._app_name = app_name

    def notify(
        self,
        summary: str,
        body: str = "",
        icon: str = "audio-input-microphone",
    ) -> None:
        if not shutil.which("notify-send"):
            logger.warning("notify-send not found, skipping notification")
            return
        cmd = ["notify-send", "--app-name", self._app_name, "--icon", icon, summary]
        if body:
            cmd.append(body)
        try:
            subprocess.run(cmd, check=False, timeout=5)
        except Exception:
            logger.debug("Failed to send notification", exc_info=True)
