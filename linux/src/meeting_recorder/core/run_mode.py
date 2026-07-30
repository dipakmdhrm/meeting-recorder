"""
Pure resolution of which process role the entry point should assume.

The Linux app runs as two cooperating processes (see the daemon/UI split): a
GTK-free **daemon** that owns the engine + tray, and an ephemeral GTK **window**
child spawned by the daemon. A user-facing launch with no role flag is a
**client** request — "make sure the daemon is up, then open a window".

Kept free of GLib/GTK so the mode decision is unit-testable in isolation.
"""

from __future__ import annotations

from collections.abc import Sequence

DAEMON = "daemon"
WINDOW = "window"
CLIENT = "client"

DAEMON_FLAG = "--daemon"
WINDOW_FLAG = "--window"


def resolve_run_mode(argv: Sequence[str]) -> str:
    """Return the process role implied by ``argv``.

    ``--daemon`` → run the engine daemon (tray only, no window).
    ``--window``  → run the GTK UI child (spawned by the daemon).
    Neither     → client mode: ensure the daemon is running, then ask it to
    open a window. This is what the app-menu launcher and the tray "Open"
    action invoke.

    The two role flags are mutually exclusive; ``--daemon`` wins if both are
    somehow present (defensive — a daemon must never also try to be a window).
    """
    args = set(argv[1:]) if len(argv) > 1 else set()
    if DAEMON_FLAG in args:
        return DAEMON
    if WINDOW_FLAG in args:
        return WINDOW
    return CLIENT
