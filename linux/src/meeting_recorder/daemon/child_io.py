"""
Shared stdout/stderr helpers for the daemon's short-lived child launchers.

Both the ``--process`` and ``--install`` children run their real work through
subprocesses that inherit fd 2, so the actual failure reason (pip's
``externally-managed-environment``, ffmpeg/cmake/sudo errors) lands on the
child's stderr. The launchers pipe that stderr and, on failure, log it and
append a tail to the message so the reason isn't opaque. ``stderr_tail`` is the
pure formatter; ``capture_stderr`` wires the async pipe read on the GLib loop.
"""

from __future__ import annotations

_MAX_LINES = 20


def stderr_tail(lines: list[str], limit: int = 400) -> str:
    """Single-line tail of captured stderr, capped to ``limit`` characters."""
    return " ".join(lines)[-limit:]


def capture_stderr(proc) -> list[str]:
    """Start reading ``proc``'s stderr pipe into the returned list.

    The list grows as lines arrive (keeping only the last ``_MAX_LINES``) and is
    driven by the GLib main loop, so the caller reads it once the child exits.
    ``proc`` must have been spawned with ``STDERR_PIPE``.
    """
    from gi.repository import Gio, GLib

    lines: list[str] = []
    err_in = Gio.DataInputStream.new(proc.get_stderr_pipe())

    def read() -> None:
        err_in.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

    def on_line(stream, res) -> None:
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:  # stderr EOF
            return
        text = line.strip()
        if text:
            lines.append(text)
            del lines[:-_MAX_LINES]
        read()

    read()
    return lines
