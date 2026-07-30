"""
One-shot model/engine install child, and the daemon-side launcher for it.

Model/engine installs are long-running (pip, cmake builds, model downloads) and
must survive the Settings window closing — so, like AI processing, each runs in
a short-lived ``--install`` child the daemon spawns and tracks. Running them in
the daemon is also wrong for memory: a Whisper model download imports the heavy
``faster_whisper``/CTranslate2 stack, which would permanently bloat the daemon.

Protocol (child → daemon, one line each on stdout):
  ``STATUS:<text>``  progress (e.g. Ollama pull percentages)
  ``RESULT:ok``      success
  ``ERROR:<text>``   failure
Anything else on stdout is ignored. Cancellation is not offered for installs.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from ..core import install_spec as ispec
from ..core.install_spec import InstallSpec
from ..core.run_mode import INSTALL_FLAG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------


def _emit(prefix: str, text: str) -> None:
    sys.stdout.write(f"{prefix}:{text.replace(chr(10), ' ')}\n")
    sys.stdout.flush()


def _require(ok: bool, what: str) -> None:
    if not ok:
        raise RuntimeError(f"{what} failed")


def _run_install(spec: InstallSpec, on_status: Callable[[str], None]) -> None:
    """Dispatch a spec to the matching installer/builder/downloader.

    Imports are function-local so the module is importable headless and each
    child only loads what it needs.
    """
    from ..services.system_installer import (
        CudaInstaller,
        OllamaInstaller,
        RocmInstaller,
        WhisperEngineInstaller,
    )

    if spec.kind == ispec.OLLAMA:
        _require(OllamaInstaller().install(), "Ollama install")
    elif spec.kind == ispec.WHISPER_ENGINE:
        _require(WhisperEngineInstaller().install(), "faster-whisper engine install")
    elif spec.kind == ispec.WHISPER_CPP_BUILD:
        from ..services.whisper_cpp_service import WhisperCppBuilder

        _require(WhisperCppBuilder().build(spec.backend), "whisper.cpp build")
    elif spec.kind == ispec.GPU:
        installer = {"nvidia": CudaInstaller, "amd": RocmInstaller}.get(spec.vendor)
        if installer is None:
            raise ValueError(f"unknown GPU vendor: {spec.vendor!r}")
        _require(installer().install(), f"{spec.vendor} GPU runtime install")
    elif spec.kind == ispec.WHISPER_MODEL:
        from ..services.whisper_service import WhisperDownloader

        WhisperDownloader().download(spec.model)
    elif spec.kind == ispec.WHISPER_CPP_MODEL:
        from ..services.whisper_cpp_service import WhisperCppModelDownloader

        WhisperCppModelDownloader().download(spec.model)
    elif spec.kind == ispec.OLLAMA_MODEL:
        from ..services.ollama_service import OllamaClient

        _require(
            OllamaClient().pull_model(spec.model, spec.host, on_status),
            f"Ollama pull {spec.model}",
        )
    else:
        raise ValueError(f"unknown install kind: {spec.kind!r}")


def run_install_child(argv: list[str]) -> int:
    """Entry for ``meeting-recorder --install <spec-json>``."""
    from ..utils.logging_setup import setup_logging

    setup_logging(role="install")
    try:
        i = argv.index(INSTALL_FLAG)
        spec = ispec.spec_from_json(argv[i + 1])
    except (ValueError, IndexError):
        _emit("ERROR", "installer: missing or invalid spec argument")
        return 2

    try:
        _run_install(spec, on_status=lambda msg: _emit("STATUS", msg))
    except Exception as exc:  # noqa: BLE001 — report any failure to the daemon
        logger.exception("Install job failed")
        _emit("ERROR", str(exc))
        return 1
    _emit("RESULT", "ok")
    return 0


# ---------------------------------------------------------------------------
# Daemon side
# ---------------------------------------------------------------------------


class InstallHandle:
    def __init__(self, proc) -> None:
        self._proc = proc


class InstallLauncher:
    """Spawns ``--install`` children and streams their protocol back."""

    def launch(
        self,
        spec_json: str,
        *,
        on_status: Callable[[str], None],
        on_finished: Callable[[bool, str], None],
    ) -> InstallHandle:
        from gi.repository import Gio, GLib

        proc = Gio.Subprocess.new(
            [sys.executable, "-m", "meeting_recorder", INSTALL_FLAG, spec_json],
            Gio.SubprocessFlags.STDOUT_PIPE,
        )
        handle = InstallHandle(proc)
        data_in = Gio.DataInputStream.new(proc.get_stdout_pipe())
        state: dict = {"ok": False, "message": ""}

        def read_next() -> None:
            data_in.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

        def on_line(stream, res) -> None:
            try:
                line, _ = stream.read_line_finish_utf8(res)
            except GLib.Error:
                line = None
            if line is None:
                proc.wait_async(None, on_exit)
                return
            if line.startswith("STATUS:"):
                on_status(line[len("STATUS:") :])
            elif line.startswith("RESULT:"):
                state["ok"] = True
            elif line.startswith("ERROR:"):
                state["ok"] = False
                state["message"] = line[len("ERROR:") :]
            read_next()

        def on_exit(p, res) -> None:
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            on_finished(state["ok"], state["message"])

        read_next()
        return handle
