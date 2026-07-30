"""
Entry point for the Meeting Recorder application.

Dispatches on the process role (see ``core/run_mode.py``):
  - ``--daemon`` → the always-on GTK-free engine + tray daemon
  - ``--window`` → the GTK window child (spawned by the daemon)
  - no flag     → client: ensure the daemon is up, then open a window

Run directly with ``python -m meeting_recorder [--daemon|--window]``.
"""

import sys

import setproctitle

from .core.run_mode import DAEMON, WINDOW, resolve_run_mode


def main() -> int:
    mode = resolve_run_mode(sys.argv)
    if mode == DAEMON:
        setproctitle.setproctitle("meeting-recorder-daemon")
        from .daemon.app import main as daemon_main

        return daemon_main()
    if mode == WINDOW:
        setproctitle.setproctitle("meeting-recorder-window")
        from .ui.window_app import main as window_main

        return window_main(sys.argv)
    # client mode
    setproctitle.setproctitle("meeting-recorder")
    from .client import main as client_main

    return client_main()


if __name__ == "__main__":
    sys.exit(main())
