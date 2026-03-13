from __future__ import annotations

from abc import ABC, abstractmethod


class TrayBackend(ABC):
    """Abstract base for system tray implementations."""

    @abstractmethod
    def update(self, recording_state: str, jobs: list) -> None:
        """Update tray icon and menu.

        Args:
            recording_state: "idle" | "recording" | "paused"
            jobs: list of (label: str, cancel_fn: Callable) tuples
        """
        ...
