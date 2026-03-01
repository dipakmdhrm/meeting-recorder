"""Entry point: python -m meeting_recorder"""

import sys
from .app import MeetingRecorderApp


def main():
    app = MeetingRecorderApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
