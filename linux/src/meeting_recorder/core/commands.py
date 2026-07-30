"""
Shared command vocabulary for the daemon/UI split.

The tray (in the daemon) and the window (a separate process) both issue the
same engine commands — the tray in-process, the window over D-Bus. Defining the
action identifiers once keeps the tray menu model, the D-Bus method dispatch,
and the window's button handlers speaking the same language.

Kept dependency-free so both the GTK-free daemon and the pure tests can import it.
"""

from __future__ import annotations

# Recording lifecycle commands (no arguments).
RECORD_HEADPHONES = "record_headphones"
RECORD_SPEAKER = "record_speaker"
PAUSE = "pause"
RESUME = "resume"
STOP = "stop"
CANCEL_SAVE = "cancel_save"
CANCEL = "cancel"
CANCEL_COUNTDOWN = "cancel_countdown"

# UI-mediated commands the tray forwards to the window process.
USE_EXISTING = "use_existing"
SHOW_WINDOW = "show"
QUIT = "quit"

# Recording lifecycle actions the daemon can execute without any UI. These are
# the tray-issued commands that map straight onto RecordingController methods.
ENGINE_COMMANDS = frozenset(
    {
        RECORD_HEADPHONES,
        RECORD_SPEAKER,
        PAUSE,
        RESUME,
        STOP,
        CANCEL_SAVE,
        CANCEL,
        CANCEL_COUNTDOWN,
    }
)
