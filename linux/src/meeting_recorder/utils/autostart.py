"""
Provides utilities for managing application autostart on system login.
It handles creating and removing the .desktop entry in the user's autostart directory.

Under the daemon/UI split the login entry must launch the **daemon** (tray only,
no GTK window) — its ``Exec`` carries the ``--daemon`` flag. Users upgrading from a
pre-split version have an autostart entry whose ``Exec`` opens a full GTK window at
every login; ``migrate_autostart_entry`` rewrites those in place on daemon startup
(the packaging scripts cannot reliably touch per-user ``~/.config``). The parse/rewrite
decision lives in the pure ``needs_autostart_migration``/``migrate_autostart_exec``
helpers so it is unit-testable and idempotent.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from meeting_recorder.config.defaults import APP_ID
from meeting_recorder.core.run_mode import DAEMON_FLAG

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
Exec={exec_path} {daemon_flag}
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


# --- pure migration helpers (unit-tested) ---------------------------------


def _exec_value(contents: str) -> str | None:
    """Return the value of the first ``Exec=`` line, or None if absent."""
    for line in contents.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec=") :]
    return None


def needs_autostart_migration(contents: str) -> bool:
    """True if a legacy autostart entry must be rewritten to launch the daemon.

    A pre-split entry runs the launcher with no role flag, which opens a GTK
    window at login. Migration is needed when an ``Exec=`` line exists and does
    not already carry ``--daemon``. Idempotent: an already-migrated entry (or
    one with no ``Exec`` line at all) needs no change.
    """
    value = _exec_value(contents)
    if value is None:
        return False
    return DAEMON_FLAG not in value.split()


def migrate_autostart_exec(contents: str) -> str:
    """Return ``contents`` with ``--daemon`` appended to the ``Exec`` line.

    Preserves every other line verbatim (including keys this app does not own).
    Returns the input unchanged when no migration is needed.
    """
    if not needs_autostart_migration(contents):
        return contents
    out = []
    for line in contents.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.startswith("Exec=") and DAEMON_FLAG not in stripped.split():
            newline = "\n" if line.endswith("\n") else ""
            out.append(f"{stripped.rstrip()} {DAEMON_FLAG}{newline}")
        else:
            out.append(line)
    return "".join(out)


# --- side-effecting entry points ------------------------------------------


def migrate_autostart_entry() -> None:
    """Rewrite a legacy autostart entry in place so login starts the daemon.

    Called once on daemon startup. No-op when autostart is disabled (file
    absent) or the entry already launches ``--daemon``.
    """
    autostart_file = AUTOSTART_DIR / DESKTOP_FILENAME
    try:
        if not autostart_file.exists():
            return
        contents = autostart_file.read_text()
        if not needs_autostart_migration(contents):
            return
        autostart_file.write_text(migrate_autostart_exec(contents))
        logger.info("Migrated autostart entry to launch the daemon: %s", autostart_file)
    except Exception as exc:  # never let migration break startup
        logger.warning("Failed to migrate autostart entry: %s", exc)


def update_autostart(enabled: bool) -> None:
    """Enable or disable autostart by managing the .desktop file in ~/.config/autostart."""
    autostart_file = AUTOSTART_DIR / DESKTOP_FILENAME

    if enabled:
        if autostart_file.exists():
            return
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        try:
            autostart_file.write_text(
                DESKTOP_TEMPLATE.format(
                    exec_path=_find_exec(), daemon_flag=DAEMON_FLAG, app_id=APP_ID
                )
            )
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
