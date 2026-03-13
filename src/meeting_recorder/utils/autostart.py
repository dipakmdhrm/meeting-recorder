"""
Provides utilities for managing application autostart on system login.
It handles creating and removing the .desktop entry in the user's autostart directory.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
import shutil

logger = logging.getLogger(__name__)

APP_NAME = "meeting-recorder"
AUTOSTART_DIR = Path(os.path.expanduser("~/.config/autostart"))
APPLICATIONS_DIR = Path(os.path.expanduser("~/.local/share/applications"))
DESKTOP_FILENAME = f"{APP_NAME}.desktop"

def update_autostart(enabled: bool) -> None:
    """Enable or disable autostart by managing the .desktop file in ~/.config/autostart."""
    autostart_file = AUTOSTART_DIR / DESKTOP_FILENAME
    
    if enabled:
        # Ensure autostart directory exists
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        
        # Try to find the installed desktop file
        source_desktop = APPLICATIONS_DIR / DESKTOP_FILENAME
        
        if source_desktop.exists():
            try:
                # Copy desktop file but append --hidden to the Exec line so the
                # window starts minimised to the system tray on login.
                lines = source_desktop.read_text().splitlines()
                with open(autostart_file, "w") as f:
                    for line in lines:
                        if line.startswith("Exec="):
                            line += " --hidden"
                        f.write(line + "\n")
                logger.info("Enabled autostart: created %s (with --hidden)", autostart_file)
            except Exception as exc:
                logger.error("Failed to enable autostart: %s", exc)
        else:
            logger.warning("Could not find installed desktop file at %s. Autostart not enabled.", source_desktop)
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

def can_enable_autostart() -> bool:
    """Check if the installed .desktop file exists so we can enable autostart."""
    return (APPLICATIONS_DIR / DESKTOP_FILENAME).exists()
