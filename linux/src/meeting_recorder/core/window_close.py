"""Pure policy for what closing the recorder window should do.

The window is a child process of the daemon (see the daemon/UI split). By
default closing it hides the window and keeps the process resident, so the next
Open is an instant present at the cost of ~100 MB of GTK/libadwaita staying in
RAM. When the user opts into "Low memory mode", closing instead exits the
process so that memory is reclaimed and idle-in-tray stays ~20 MB; the daemon
respawns a fresh window on demand (a cold-start delay on every reopen).

This decision is read fresh from config at close time, so toggling the setting
(only possible while the window is visible) takes effect on the next close with
no restart or extra IPC.
"""

from __future__ import annotations

__all__ = ["CLOSE_HIDE", "CLOSE_EXIT", "resolve_close_action"]

CLOSE_HIDE = "hide"
CLOSE_EXIT = "exit"


def resolve_close_action(cfg: dict) -> str:
    """Return ``CLOSE_HIDE`` or ``CLOSE_EXIT`` for the given config.

    Any truthy ``low_memory_mode`` value exits the process on close; anything
    else (the default) hides the window and keeps the process resident.
    """
    if cfg.get("low_memory_mode", False):
        return CLOSE_EXIT
    return CLOSE_HIDE
