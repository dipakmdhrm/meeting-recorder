"""
Provides utilities for managing application autostart on system login.
It handles creating and removing the .desktop entry in the user's autostart directory.
"""

from __future__ import annotations

import os
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "meeting-recorder"
AUTOSTART_DIR = Path(os.path.expanduser("~/.config/autostart"))
DESKTOP_FILENAME = f"{APP_NAME}.desktop"

_KNOWN_EXEC_PATHS = [
    Path("/usr/bin/meeting-recorder"),
    Path(os.path.expanduser("~/.local/bin/meeting-recorder")),
]

DESKTOP_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Meeting Recorder
Comment=Record, transcribe and summarize meetings
Exec={exec_path}
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;Audio;Recorder;
Keywords=meeting;record;transcribe;notes;audio;
StartupNotify=true
"""


def _find_exec() -> str:
    """Resolve the meeting-recorder executable path."""
    found = shutil.which(APP_NAME)
    if found:
        return found
    for path in _KNOWN_EXEC_PATHS:
        if path.exists():
            return str(path)
    return APP_NAME  # fallback: rely on PATH at login


def update_autostart(enabled: bool) -> None:
    """Enable or disable autostart by managing the .desktop file in ~/.config/autostart."""
    autostart_file = AUTOSTART_DIR / DESKTOP_FILENAME

    if enabled:
        if autostart_file.exists():
            return
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        try:
            autostart_file.write_text(DESKTOP_TEMPLATE.format(exec_path=_find_exec()))
            logger.info("Enabled autostart: wrote %s", autostart_file)
        except Exception as exc:
            logger.error("Failed to enable autostart: %s", exc)
    else:
        if autostart_file.exists():
            try:
                autostart_file.unlink()
                logger.info("Disabled autostart: removed %s", autostart_file)
            except Exception as exc:
                logger.error("Failed to disable autostart: %s", exc)


def is_autostart_enabled() -> bool:
    """Check if the autostart .desktop file exists."""
    return (AUTOSTART_DIR / DESKTOP_FILENAME).exists()
