"""
Provides a simple wrapper around 'notify-send' (libnotify) to display desktop notifications. This is used to inform the user when a call is detected or when a recording's processing is complete.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def notify(
    summary: str,
    body: str = "",
    app_name: str = "Meeting Recorder",
    icon: str = "meeting-recorder",
) -> None:
    """Send a desktop notification using notify-send (libnotify)."""
    cmd = [
        "notify-send",
        "--app-name", app_name,
        "--icon", icon,
        summary,
    ]
    if body:
        cmd.append(body)

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.warning("notify-send not found; notifications unavailable")
    except Exception as exc:
        logger.warning("Failed to send notification: %s", exc)
