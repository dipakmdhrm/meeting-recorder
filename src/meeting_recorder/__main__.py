"""
The execution entry point for the Meeting Recorder application. It allows the package to be run directly using 'python -m meeting_recorder', initializing the Gtk application and starting the main event loop.
"""

import sys
import setproctitle
from .app import MeetingRecorderApp

setproctitle.setproctitle("meeting-recorder")


def main():
    app = MeetingRecorderApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
