"""
Testable services for the optional whisper.cpp transcription engine: GPU
backend detection, the cmake build command, building the binary from source,
and GGML model download / cache detection.

The engine is built from source on opt-in (kept out of the base install) so a
fresh, Gemini-only setup stays minimal. The pure helpers (``detect_gpu_backend``,
``build_cmake_command``) and the injected ``which_fn`` / ``shell_fn`` /
``downloader`` seams keep everything unit-testable without a compiler, a GPU, or
the network:

    backend = detect_gpu_backend(which_fn=fake_which, platform_fn=lambda: "linux")
    checker = WhisperCppStatusChecker(cache_root=tmp_path / "models")
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Where the engine and downloaded GGML models live.
WHISPER_CPP_HOME = Path.home() / ".local" / "share" / "meeting-recorder" / "whisper.cpp"
WHISPER_CPP_BINARY = WHISPER_CPP_HOME / "build" / "bin" / "whisper-cli"
WHISPER_CPP_MODELS_DIR = (
    Path.home() / ".local" / "share" / "meeting-recorder" / "whisper-cpp-models"
)
WHISPER_CPP_REPO = "https://github.com/ggerganov/whisper.cpp.git"

# Maps an acceleration backend to the cmake flag that enables it.
_BACKEND_CMAKE_FLAGS = {
    "cuda":   "-DGGML_CUDA=1",
    "rocm":   "-DGGML_HIPBLAS=1",
    "vulkan": "-DGGML_VULKAN=1",
    "metal":  "-DGGML_METAL=1",
    "cpu":    "",
}


def detect_gpu_backend(
    which_fn: Callable[[str], str | None] = shutil.which,
    platform_fn: Callable[[], str] = lambda: sys.platform,
    path_exists_fn: Callable[[str], bool] = os.path.exists,
) -> str:
    """Return the best whisper.cpp acceleration backend for this machine.

    One of ``"metal"``, ``"cuda"``, ``"rocm"``, ``"vulkan"``, ``"cpu"``.
    Pure aside from the injected probes, so it is unit-testable.
    """
    if platform_fn() == "darwin":
        return "metal"
    if which_fn("nvidia-smi") is not None:
        return "cuda"
    if which_fn("rocminfo") is not None or path_exists_fn("/dev/kfd"):
        return "rocm"
    if which_fn("vulkaninfo") is not None:
        return "vulkan"
    return "cpu"


def build_cmake_command(backend: str) -> str:
    """Return the ``cmake`` configure+build command for *backend*.

    Pure string builder so the per-backend flag mapping is unit-testable.
    Raises ``ValueError`` for an unknown backend.
    """
    if backend not in _BACKEND_CMAKE_FLAGS:
        raise ValueError(f"Unknown whisper.cpp backend: {backend!r}")
    flag = _BACKEND_CMAKE_FLAGS[backend]
    configure = "cmake -B build" + (f" {flag}" if flag else "")
    return f"{configure} && cmake --build build --config Release -j"


def _toolchain_install_command(which_fn: Callable[[str], str | None]) -> str | None:
    """Return a command that installs the build toolchain (git/cmake/compiler)
    for the detected package manager, or ``None`` if unsupported."""
    if which_fn("apt-get"):
        return "sudo apt-get update -qq && sudo apt-get install -y git cmake build-essential"
    if which_fn("dnf"):
        return "sudo dnf install -y git cmake gcc-c++ make"
    if which_fn("pacman"):
        return "sudo pacman -Syu --noconfirm git cmake base-devel"
    return None


class WhisperCppBuilder:
    """Clones and builds whisper.cpp from source with the chosen backend."""

    def __init__(
        self,
        binary_path: Path | None = None,
        home: Path | None = None,
        which_fn: Callable[[str], str | None] = shutil.which,
        shell_fn: Callable[[str], int] = os.system,
    ) -> None:
        self._binary = binary_path or WHISPER_CPP_BINARY
        self._home = home or WHISPER_CPP_HOME
        self._which = which_fn
        self._shell = shell_fn

    def is_built(self) -> bool:
        return self._binary.exists()

    @property
    def binary_path(self) -> Path:
        return self._binary

    def build(self, backend: str) -> bool:
        """Ensure the toolchain, clone the repo if needed, and build.

        Returns ``True`` on success. Raises ``ValueError`` for an unknown
        backend (surfaced early, before any shell command runs).
        """
        cmake_cmd = build_cmake_command(backend)  # validates backend first
        try:
            toolchain = _toolchain_install_command(self._which)
            if toolchain is None:
                logger.warning("No supported package manager found to install build toolchain")
                return False
            if self._shell(toolchain) != 0:
                return False

            if not (self._home / ".git").exists():
                if self._shell(f"git clone --depth 1 {WHISPER_CPP_REPO} {self._home}") != 0:
                    return False

            full = f"cd {self._home} && {cmake_cmd}"
            return self._shell(full) == 0
        except Exception as exc:
            logger.error("Failed to build whisper.cpp: %s", exc)
            return False


def _default_ggml_downloader(url: str, dest: Path) -> None:
    """Download a GGML model file to *dest* via urllib."""
    import urllib.request  # noqa: PLC0415

    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)  # noqa: S310


class WhisperCppStatusChecker:
    """Checks whether a GGML model file is already downloaded."""

    def __init__(self, cache_root: Path | None = None) -> None:
        self._cache_root = cache_root or WHISPER_CPP_MODELS_DIR

    def model_path(self, model: str) -> Path:
        from meeting_recorder.config.defaults import WHISPER_CPP_GGML_FILES  # noqa: PLC0415
        filename = WHISPER_CPP_GGML_FILES.get(model, f"ggml-{model}.bin")
        return self._cache_root / filename

    def is_cached(self, model: str) -> bool:
        return self.model_path(model).exists()


class WhisperCppModelDownloader:
    """Downloads a GGML model file from the HuggingFace whisper.cpp repo."""

    def __init__(
        self,
        cache_root: Path | None = None,
        downloader: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._cache_root = cache_root or WHISPER_CPP_MODELS_DIR
        self._download = downloader or _default_ggml_downloader

    def download(self, model: str) -> None:
        """Download *model*'s GGML weights.  Raises on failure."""
        from meeting_recorder.config.defaults import (  # noqa: PLC0415
            WHISPER_CPP_GGML_BASE_URL,
            WHISPER_CPP_GGML_FILES,
        )
        filename = WHISPER_CPP_GGML_FILES.get(model, f"ggml-{model}.bin")
        url = WHISPER_CPP_GGML_BASE_URL + filename
        self._download(url, self._cache_root / filename)
