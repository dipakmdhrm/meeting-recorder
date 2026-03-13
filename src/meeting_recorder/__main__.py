"""
The execution entry point for the Meeting Recorder application. It allows the package to be run directly using 'python -m meeting_recorder', initializing the Gtk application and starting the main event loop.
"""

import sys
from .app import MeetingRecorderApp


def main():
    # Consume --hidden before GtkApplication sees it (it rejects unknown flags).
    # app.py checks sys.argv directly for this flag.
    gtk_argv = [a for a in sys.argv if a != "--hidden"]
    app = MeetingRecorderApp()
    sys.exit(app.run(gtk_argv))


if __name__ == "__main__":
    main()
