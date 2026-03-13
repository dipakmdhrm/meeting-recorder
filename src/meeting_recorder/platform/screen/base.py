from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MonitorInfo:
    name: str
    resolution: str
    position: str


class ScreenRecorder(ABC):
    """Abstract base for screen recording backends."""

    @abstractmethod
    def list_monitors(self) -> list[MonitorInfo]:
        """List available monitors."""
        ...

    @abstractmethod
    def start(self, monitors: list[str], output_dir: Path, fps: int) -> None:
        """Start recording specified monitors.

        If a monitor fails to start, log error and continue with remaining.
        """
        ...

    @abstractmethod
    def stop(self) -> list[Path]:
        """Stop recording, return paths to recorded video files."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the screen recorder binary is installed."""
        ...
