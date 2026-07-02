"""
Testable services for installing Ollama and GPU runtimes on the host system.

Security posture (see audit P1-L9):

- No ``os.system``: every command is an argv list executed without a shell,
  logged verbatim before it runs. Where a package manager genuinely needs a
  compound command it is a *fixed string* handed to ``sh -c`` with no user
  input interpolated (the Fedora version is validated as numeric first).
- No bare ``sudo``: privilege elevation goes through ``pkexec`` so a polkit
  authentication dialog appears — correct for a GUI app, where ``sudo``
  would fail silently without a terminal. ``sudo`` remains a fallback for
  systems without polkit.
- No ``curl | sh``: the Ollama install script is downloaded over HTTPS to a
  temp file, its SHA-256 is logged for auditability, and only then executed.
  (The hash cannot be pinned — upstream updates the script — so HTTPS to
  ollama.com remains the trust anchor, but partial-execution and
  pipe-injection hazards are gone.)

Inject ``which_fn`` / ``run_fn`` / ``capture_fn`` / ``fetch_fn`` in tests to
avoid executing real commands:

    installer = OllamaInstaller(
        which_fn=lambda _: "/usr/bin/ollama",   # pretend it's installed
        run_fn=lambda _cmd: 0,                  # pretend install succeeded
    )
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

OLLAMA_INSTALL_URL = "https://ollama.com/install.sh"

# The real install script is ~10 KB; anything tiny is a broken/captive response.
MIN_INSTALL_SCRIPT_BYTES = 1000


def _run_command(cmd: list[str]) -> int:
    """Default runner: execute an argv list without a shell, logging it first."""
    # shlex.join quotes arguments with spaces (e.g. sh -c snippets) so the
    # logged command is accurate and copy-pasteable.
    logger.info("Running: %s", shlex.join(cmd))
    return subprocess.call(cmd)


def _capture_output(cmd: list[str]) -> str:
    """Default capture: run an argv list and return its stdout."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout


def _fetch_url(url: str) -> bytes:
    """Default fetch: download *url* over HTTPS with a bounded timeout."""
    with urllib.request.urlopen(url, timeout=60) as resp:
        return bytes(resp.read())


def build_privileged_command(
    shell_snippet: str,
    which_fn: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Wrap a fixed shell snippet for privilege elevation.

    Uses ``pkexec`` (polkit authentication dialog — works from a GUI with no
    terminal attached) when available, falling back to ``sudo`` otherwise.
    The snippet must be a constant or fully validated — nothing user-supplied.
    """
    elevate = "pkexec" if which_fn("pkexec") else "sudo"
    return [elevate, "sh", "-c", shell_snippet]


def detect_gpu_vendor(
    which_fn: Callable[[str], str | None] = shutil.which,
    platform_fn: Callable[[], str] = lambda: sys.platform,
) -> str:
    """Best-effort GPU vendor detection. Returns one of
    ``"nvidia"``, ``"amd"``, ``"apple"``, ``"none"``.

    Pure aside from the injected probes, so it is unit-testable.
    """
    if platform_fn() == "darwin":
        return "apple"
    if which_fn("nvidia-smi") is not None:
        return "nvidia"
    # ``rocminfo`` is the canonical ROCm probe; ``/dev/kfd`` is the AMD compute
    # kernel device that exists when the amdgpu/ROCm stack is loaded.
    if which_fn("rocminfo") is not None or os.path.exists("/dev/kfd"):
        return "amd"
    return "none"


class WhisperEngineInstaller:
    """Installs the optional ``faster-whisper`` engine into the app venv.

    Kept out of the base install so a fresh, Gemini-only setup stays minimal;
    the user opts in from Settings -> Models.
    """

    def __init__(
        self,
        find_spec_fn: Callable[[str], object | None] | None = None,
        runner_fn: Callable[[list[str]], int] | None = None,
    ) -> None:
        if find_spec_fn is None:
            import importlib.util

            find_spec_fn = importlib.util.find_spec
        self._find_spec = find_spec_fn
        self._runner = runner_fn or _run_command

    def is_available(self) -> bool:
        try:
            return self._find_spec("faster_whisper") is not None
        except Exception:
            return False

    def install(self) -> bool:
        """``pip install faster-whisper`` into the running interpreter's venv."""
        try:
            cmd = [sys.executable, "-m", "pip", "install", "faster-whisper"]
            return self._runner(cmd) == 0
        except Exception as exc:
            logger.error("Failed to install faster-whisper engine: %s", exc)
            return False


class OllamaInstaller:
    """Checks for and installs Ollama via the official install script.

    The script is downloaded to a temp file (SHA-256 logged) and executed
    from disk — never piped from the network into a shell.
    """

    def __init__(
        self,
        which_fn: Callable[[str], str | None] = shutil.which,
        run_fn: Callable[[list[str]], int] = _run_command,
        fetch_fn: Callable[[str], bytes] = _fetch_url,
    ) -> None:
        self._which = which_fn
        self._run = run_fn
        self._fetch = fetch_fn

    def is_available(self) -> bool:
        return self._which("ollama") is not None

    def install(self) -> bool:
        """Download and run the Ollama install script. Returns ``True`` on success."""
        try:
            script = self._fetch(OLLAMA_INSTALL_URL)
            if len(script) < MIN_INSTALL_SCRIPT_BYTES:
                # A captive portal or proxy can return a tiny/empty 200 body;
                # executing that would silently "succeed" without installing.
                logger.error(
                    "Ollama install script suspiciously small (%d bytes) — aborting",
                    len(script),
                )
                return False
            digest = hashlib.sha256(script).hexdigest()
            logger.info(
                "Fetched Ollama install script (%d bytes, sha256=%s) from %s",
                len(script),
                digest,
                OLLAMA_INSTALL_URL,
            )
            with tempfile.NamedTemporaryFile(
                suffix=".sh", prefix="ollama-install-", delete=False
            ) as f:
                f.write(script)
                path = f.name
            try:
                return self._run(["sh", path]) == 0
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as exc:
            logger.error("Failed to install Ollama: %s", exc)
            return False


class CudaInstaller:
    """Checks for and installs NVIDIA CUDA runtime libraries."""

    def __init__(
        self,
        which_fn: Callable[[str], str | None] = shutil.which,
        run_fn: Callable[[list[str]], int] = _run_command,
        capture_fn: Callable[[list[str]], str] = _capture_output,
    ) -> None:
        self._which = which_fn
        self._run = run_fn
        self._capture = capture_fn

    def is_available(self) -> bool:
        return self._which("nvidia-smi") is not None

    def install(self) -> bool:
        """Install CUDA libraries for the detected package manager.  Returns ``True`` on success."""
        try:
            if self._which("apt-get"):
                code = self._run(
                    build_privileged_command(
                        "apt-get update -qq && apt-get install -y libcublas12 libcudart12",
                        self._which,
                    )
                )
            elif self._which("dnf"):
                fedora_version = self._capture(["rpm", "-E", "%fedora"]).strip()
                if not fedora_version.isdigit():
                    logger.error(
                        "Could not determine Fedora version (rpm -E %%fedora returned %r)",
                        fedora_version,
                    )
                    return False
                arch = platform.machine()
                repo_url = (
                    "https://developer.download.nvidia.com/compute/cuda/repos/"
                    f"fedora{fedora_version}/{arch}/cuda-fedora{fedora_version}.repo"
                )
                code = self._run(
                    build_privileged_command(
                        f"dnf config-manager --add-repo '{repo_url}'"
                        " && dnf install -y libcublas-12-x cuda-cudart-12-x",
                        self._which,
                    )
                )
            elif self._which("pacman"):
                code = self._run(
                    build_privileged_command("pacman -Syu --noconfirm cuda", self._which)
                )
            else:
                logger.warning("No supported package manager found for CUDA installation")
                return False
            return code == 0
        except Exception as exc:
            logger.error("Failed to install CUDA: %s", exc)
            return False


class RocmInstaller:
    """Checks for and installs the AMD ROCm runtime libraries.

    The AMD counterpart to :class:`CudaInstaller`; used on machines whose GPU
    vendor is detected as ``"amd"`` (see :func:`detect_gpu_vendor`).
    """

    def __init__(
        self,
        which_fn: Callable[[str], str | None] = shutil.which,
        run_fn: Callable[[list[str]], int] = _run_command,
    ) -> None:
        self._which = which_fn
        self._run = run_fn

    def is_available(self) -> bool:
        return self._which("rocminfo") is not None or os.path.exists("/dev/kfd")

    def install(self) -> bool:
        """Install ROCm runtime libraries for the detected package manager."""
        try:
            if self._which("apt-get"):
                code = self._run(
                    build_privileged_command(
                        "apt-get update -qq && apt-get install -y rocm-hip-runtime rocblas",
                        self._which,
                    )
                )
            elif self._which("dnf"):
                code = self._run(
                    build_privileged_command("dnf install -y rocm-hip rocblas", self._which)
                )
            elif self._which("pacman"):
                code = self._run(
                    build_privileged_command(
                        "pacman -Syu --noconfirm rocm-hip-runtime rocblas", self._which
                    )
                )
            else:
                logger.warning("No supported package manager found for ROCm installation")
                return False
            return code == 0
        except Exception as exc:
            logger.error("Failed to install ROCm: %s", exc)
            return False
