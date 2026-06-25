"""
Testable services for installing Ollama and NVIDIA CUDA on the host system.

Inject ``which_fn`` / ``shell_fn`` in tests to avoid executing real shell
commands:

    installer = OllamaInstaller(
        which_fn=lambda _: "/usr/bin/ollama",   # pretend it's installed
        shell_fn=lambda _: 0,                   # pretend install succeeded
    )
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Callable

logger = logging.getLogger(__name__)


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
        self._runner = runner_fn or (lambda cmd: subprocess.call(cmd))

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
    """Checks for and installs Ollama via the official install script."""

    def __init__(
        self,
        which_fn: Callable[[str], str | None] = shutil.which,
        shell_fn: Callable[[str], int] = os.system,
    ) -> None:
        self._which = which_fn
        self._shell = shell_fn

    def is_available(self) -> bool:
        return self._which("ollama") is not None

    def install(self) -> bool:
        """Run the Ollama install script.  Returns ``True`` on success."""
        try:
            return self._shell("curl -fsSL https://ollama.com/install.sh | sh") == 0
        except Exception as exc:
            logger.error("Failed to install Ollama: %s", exc)
            return False


class CudaInstaller:
    """Checks for and installs NVIDIA CUDA runtime libraries."""

    def __init__(
        self,
        which_fn: Callable[[str], str | None] = shutil.which,
        shell_fn: Callable[[str], int] = os.system,
        popen_fn: Callable[[str], object] = os.popen,
    ) -> None:
        self._which = which_fn
        self._shell = shell_fn
        self._popen = popen_fn

    def is_available(self) -> bool:
        return self._which("nvidia-smi") is not None

    def install(self) -> bool:
        """Install CUDA libraries for the detected package manager.  Returns ``True`` on success."""
        try:
            if self._which("apt-get"):
                code = self._shell(
                    "sudo apt-get update -qq && sudo apt-get install -y libcublas12 libcudart12"
                )
            elif self._which("dnf"):
                fedora_version = self._popen("rpm -E %fedora").read().strip()
                code = self._shell(
                    f"sudo dnf config-manager --add-repo https://developer.download.nvidia.com/"
                    f"compute/cuda/repos/fedora{fedora_version}/$(uname -m)/"
                    f"cuda-fedora{fedora_version}.repo"
                    f" && sudo dnf install -y libcublas-12-x cuda-cudart-12-x"
                )
            elif self._which("pacman"):
                code = self._shell("sudo pacman -Syu --noconfirm cuda")
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
        shell_fn: Callable[[str], int] = os.system,
    ) -> None:
        self._which = which_fn
        self._shell = shell_fn

    def is_available(self) -> bool:
        return self._which("rocminfo") is not None or os.path.exists("/dev/kfd")

    def install(self) -> bool:
        """Install ROCm runtime libraries for the detected package manager."""
        try:
            if self._which("apt-get"):
                code = self._shell(
                    "sudo apt-get update -qq && sudo apt-get install -y rocm-hip-runtime rocblas"
                )
            elif self._which("dnf"):
                code = self._shell("sudo dnf install -y rocm-hip rocblas")
            elif self._which("pacman"):
                code = self._shell("sudo pacman -Syu --noconfirm rocm-hip-runtime rocblas")
            else:
                logger.warning("No supported package manager found for ROCm installation")
                return False
            return code == 0
        except Exception as exc:
            logger.error("Failed to install ROCm: %s", exc)
            return False
