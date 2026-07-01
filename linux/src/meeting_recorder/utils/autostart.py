"""
Provides utilities for managing application autostart on system login.
It handles creating and removing the .desktop entry in the user's autostart directory.
"""

from __future__ import annotations

import os
import logging
import shutil
from pathlib import Path

from meeting_recorder.config.defaults import APP_ID

logger = logging.getLogger(__name__)

APP_NAME = "meeting-recorder"
AUTOSTART_DIR = Path(os.path.expanduser("~/.config/autostart"))
# Named after the GTK application id so that, when the session autostarts the app,
# the launched identity (systemd scope) matches the installed
# ``<APP_ID>.desktop`` — otherwise the GNOME shell can't tie the running window to
# the app and shows a generic/empty icon in the panel and overview.
DESKTOP_FILENAME = f"{APP_ID}.desktop"
# Older versions wrote the entry under the app *name*; keep it for migration.
LEGACY_DESKTOP_FILENAME = f"{APP_NAME}.desktop"

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
Icon=meeting-recorder
Terminal=false
Categories=AudioVideo;Audio;Recorder;
Keywords=meeting;record;transcribe;notes;audio;
StartupNotify=true
StartupWMClass={app_id}
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
    legacy_file = AUTOSTART_DIR / LEGACY_DESKTOP_FILENAME

    if enabled:
        # Always drop the legacy-named entry so the app isn't autostarted twice
        # (and under the wrong identity) after the rename.
        _unlink_quietly(legacy_file)
        if autostart_file.exists():
            return
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        try:
            autostart_file.write_text(
                DESKTOP_TEMPLATE.format(exec_path=_find_exec(), app_id=APP_ID)
            )
            logger.info("Enabled autostart: wrote %s", autostart_file)
        except Exception as exc:
            logger.error("Failed to enable autostart: %s", exc)
    else:
        _unlink_quietly(autostart_file)
        _unlink_quietly(legacy_file)


def _unlink_quietly(path: Path) -> None:
    """Remove ``path`` if present, logging but never raising."""
    if path.exists():
        try:
            path.unlink()
            logger.info("Removed autostart entry %s", path)
        except Exception as exc:
            logger.error("Failed to remove autostart entry %s: %s", path, exc)


def is_autostart_enabled() -> bool:
    """Check if an autostart .desktop file exists (current or legacy name)."""
    return (
        (AUTOSTART_DIR / DESKTOP_FILENAME).exists()
        or (AUTOSTART_DIR / LEGACY_DESKTOP_FILENAME).exists()
    )
