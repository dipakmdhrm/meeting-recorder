"""
Shared file+stderr logging setup, used by both the daemon and the window process.

Lifted verbatim from the old single-process app so both halves of the split log
to the same ``/var/log/meeting-recorder/{app,error}.log`` (falling back to
``~/.local/share/meeting-recorder`` when the system dir is not writable). A
``role`` tag distinguishes daemon vs window lines in the shared files.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


class _BelowWarning(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


def setup_logging(role: str = "app") -> None:
    log_dir = Path("/var/log/meeting-recorder")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / ".write_test").touch()
        (log_dir / ".write_test").unlink()
    except OSError:
        log_dir = Path(os.path.expanduser("~/.local/share/meeting-recorder"))
        log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(f"%(asctime)s [{role}] %(name)s %(levelname)s %(message)s")

    app_fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    app_fh.setLevel(logging.DEBUG)
    app_fh.addFilter(_BelowWarning())
    app_fh.setFormatter(fmt)
    root.addHandler(app_fh)

    err_fh = logging.FileHandler(log_dir / "error.log", encoding="utf-8")
    err_fh.setLevel(logging.WARNING)
    err_fh.setFormatter(fmt)
    root.addHandler(err_fh)

    if sys.stderr and sys.stderr.isatty():
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    logging.getLogger(__name__).info("Logging to %s/{app,error}.log", log_dir)
