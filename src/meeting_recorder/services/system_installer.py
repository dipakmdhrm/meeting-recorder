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
from typing import Callable

logger = logging.getLogger(__name__)


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
                    f"compute/cuda/repos/fedora{fedora_version}/x86_64/"
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
