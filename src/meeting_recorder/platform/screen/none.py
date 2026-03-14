from __future__ import annotations

from pathlib import Path

from .base import ScreenRecorder, MonitorInfo


class NoOpScreenRecorder(ScreenRecorder):
    """No-op screen recorder. Used when screen recording is disabled."""

    def list_monitors(self) -> list[MonitorInfo]:
        return []

    def start(self, monitors: list[str], output_dir: Path, fps: int) -> None:
        pass

    def stop(self) -> list[Path]:
        return []

    def is_available(self) -> bool:
        return True
