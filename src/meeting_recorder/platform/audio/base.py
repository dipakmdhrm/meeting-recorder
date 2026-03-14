from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class CaptureMode(Enum):
    HEADPHONES = "headphones"
    SPEAKER = "speaker"


@dataclass
class AudioDevice:
    name: str
    description: str
    is_default: bool = False


@dataclass
class CaptureOutputPaths:
    mic: Path
    system: Path | None  # None in SPEAKER mode


@dataclass
class AudioResult:
    combined: Path
    mic: Path | None       # None if separate_audio_tracks is False
    system: Path | None    # None if SPEAKER mode or tracks disabled


class AudioBackend(ABC):
    """Abstract base for platform-specific audio capture."""

    @abstractmethod
    def list_sources(self) -> list[AudioDevice]:
        """List available microphone sources."""
        ...

    @abstractmethod
    def list_sinks(self) -> list[AudioDevice]:
        """List available output sinks."""
        ...

    @abstractmethod
    def get_default_source(self) -> AudioDevice | None:
        """Return the default microphone source."""
        ...

    @abstractmethod
    def get_default_sink(self) -> AudioDevice | None:
        """Return the default output sink."""
        ...

    @abstractmethod
    def start_capture(self, output_paths: CaptureOutputPaths, quality: str) -> None:
        """Start raw audio capture. One or two ffmpeg processes depending on mode.

        Args:
            output_paths: Where to write mic and system audio files.
            quality: FFmpeg quality parameter (e.g., "2" for ~190kbps).
        """
        ...

    @abstractmethod
    def stop_capture(self) -> None:
        """Stop all running capture processes cleanly."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend's dependencies are installed."""
        ...

    @abstractmethod
    def validate(self) -> tuple[bool, str]:
        """Validate audio devices are available. Returns (ok, error_message)."""
        ...
