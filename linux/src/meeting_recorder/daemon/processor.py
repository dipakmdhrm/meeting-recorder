"""
One-shot AI-processing child process, and the daemon-side launcher for it.

Running the pipeline *inside* the daemon would permanently balloon the daemon:
importing the Gemini SDK (`google.genai`) alone costs ~70 MB RSS, and Python
never unloads a module once imported. So — exactly like the GTK window — each
job runs in a short-lived **child process** (`--process`) that loads the heavy
stack, does one job, writes transcript.md/notes.md, and exits, returning the
daemon to its ~40 MB idle footprint.

Protocol (child → daemon, one line each on stdout):
  ``STATUS:<text>``  progress for the job row
  ``RESULT:<json>``  final ``[audio, transcript, notes]`` paths (auto-title may
                     have moved them); success
  ``ERROR:<text>``   failure
Anything else on stdout is ignored. Cancellation = the daemon kills the child.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from ..core.run_mode import PROCESS_FLAG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Child side (runs in the spawned `--process` process)
# ---------------------------------------------------------------------------


def _emit(prefix: str, text: str) -> None:
    # One protocol line; collapse newlines so it stays single-line.
    sys.stdout.write(f"{prefix}:{text.replace(chr(10), ' ')}\n")
    sys.stdout.flush()


def run_processor_child(argv: list[str]) -> int:
    """Entry for ``meeting-recorder --process <audio> <transcript> <notes>``."""
    from ..config import settings
    from ..processing.pipeline import Pipeline
    from ..utils.logging_setup import setup_logging

    setup_logging(role="process")
    try:
        i = argv.index(PROCESS_FLAG)
        audio, transcript, notes = argv[i + 1 : i + 4]
    except (ValueError, IndexError):
        _emit("ERROR", "processor: missing audio/transcript/notes arguments")
        return 2

    cfg = settings.load()
    pipeline = Pipeline(
        config=cfg,
        audio_path=Path(audio),
        transcript_path=Path(transcript),
        notes_path=Path(notes),
        on_status=lambda msg: _emit("STATUS", msg),
    )
    try:
        pipeline.run()
    except Exception as exc:  # noqa: BLE001 — report any failure to the daemon
        logger.exception("Processor job failed")
        _emit("ERROR", str(exc))
        return 1

    a, t, n = pipeline.output_paths
    _emit(
        "RESULT", json.dumps([str(a) if a else None, str(t) if t else None, str(n) if n else None])
    )
    return 0


# ---------------------------------------------------------------------------
# Daemon side (spawns and reads the child on the GLib main loop)
# ---------------------------------------------------------------------------


class ProcessorHandle:
    """A running processor child; ``cancel()`` kills it."""

    def __init__(self, proc) -> None:
        self._proc = proc
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        try:
            self._proc.force_exit()
        except Exception:  # already exited
            pass


class ProcessorLauncher:
    """Spawns `--process` children and streams their protocol back as callbacks.

    All callbacks fire on the daemon's GLib main loop (async pipe reads), so the
    engine mutates job state on the main thread as before.
    """

    def launch(
        self,
        audio: str,
        transcript: str,
        notes: str,
        *,
        on_status: Callable[[str], None],
        on_done: Callable[[list], None],
        on_error: Callable[[str], None],
    ) -> ProcessorHandle:
        from gi.repository import Gio, GLib

        from .child_io import capture_stderr, stderr_tail

        # STDERR_PIPE too: a provider's underlying tool (whisper-cli, ffmpeg) can
        # fail on stderr, so capture it to surface the real reason on failure.
        proc = Gio.Subprocess.new(
            [sys.executable, "-m", "meeting_recorder", PROCESS_FLAG, audio, transcript, notes],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        )
        handle = ProcessorHandle(proc)
        data_in = Gio.DataInputStream.new(proc.get_stdout_pipe())
        stderr_lines = capture_stderr(proc)
        state: dict = {"paths": None, "error": None}

        def read_next() -> None:
            data_in.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

        def on_line(stream, res) -> None:
            try:
                line, _ = stream.read_line_finish_utf8(res)
            except GLib.Error:
                line = None
            if line is None:  # EOF
                proc.wait_async(None, on_exit)
                return
            _handle(line)
            read_next()

        def _handle(line: str) -> None:
            if line.startswith("STATUS:"):
                on_status(line[len("STATUS:") :])
            elif line.startswith("RESULT:"):
                try:
                    state["paths"] = json.loads(line[len("RESULT:") :])
                except ValueError:
                    state["error"] = "processor returned malformed result"
            elif line.startswith("ERROR:"):
                state["error"] = line[len("ERROR:") :]

        def _fail(message: str) -> None:
            if stderr_lines:
                logger.warning("Processor stderr: %s", " | ".join(stderr_lines))
                tail = stderr_tail(stderr_lines)
                message = f"{message} — {tail}" if message else tail
            on_error(message)

        def on_exit(p, res) -> None:
            try:
                p.wait_finish(res)  # reap
            except GLib.Error:
                pass
            if handle.cancelled:
                return  # engine already handled the cancel
            if state["error"] is not None:
                _fail(state["error"])
            elif state["paths"] is not None:
                on_done(state["paths"])
            else:
                _fail("processing exited without a result")

        read_next()
        return handle
