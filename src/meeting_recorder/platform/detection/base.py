from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class AudioEventWatcher(ABC):
    """Abstract base for monitoring audio events (e.g., new mic captures)."""

    @abstractmethod
    def start(self, on_detected: Callable[[str], None]) -> None:
        """Start monitoring for audio events in background thread."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop monitoring."""
        ...
