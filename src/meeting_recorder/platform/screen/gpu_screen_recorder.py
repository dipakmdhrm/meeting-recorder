from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
from pathlib import Path

from .base import ScreenRecorder, MonitorInfo

logger = logging.getLogger(__name__)


class GpuScreenRecorder(ScreenRecorder):
    """Screen recording via gpu-screen-recorder (Wayland-native, GPU-accelerated)."""

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._output_dir: Path | None = None

    def list_monitors(self) -> list[MonitorInfo]:
        monitors = self._try_gpu_screen_recorder_list()
        if monitors:
            return monitors
        monitors = self._try_kscreen_doctor()
        if monitors:
            return monitors
        logger.warning("Could not auto-detect monitors")
        return []

    def start(self, monitors: list[str], output_dir: Path, fps: int) -> None:
        self._output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        for monitor in monitors:
            output_file = output_dir / f"screen-{monitor}.mp4"
            cmd = [
                "gpu-screen-recorder",
                "-w", monitor,
                "-f", str(fps),
                "-fallback-cpu-encoding", "yes",
                "-o", str(output_file),
            ]
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                # Check if it exits immediately (e.g. bad monitor name)
                try:
                    retcode = proc.wait(timeout=1.5)
                    stderr = proc.stderr.read().decode() if proc.stderr else ""
                    logger.error(
                        "gpu-screen-recorder exited immediately for %s (code %d): %s",
                        monitor, retcode, stderr.strip()[-200:],
                    )
                    continue
                except subprocess.TimeoutExpired:
                    pass  # still running — good
                self._processes[monitor] = proc
                logger.info("Started screen recording for %s -> %s", monitor, output_file)
            except Exception:
                logger.error("Failed to start screen recording for %s", monitor, exc_info=True)

    def stop(self) -> list[Path]:
        paths = []
        for monitor, proc in self._processes.items():
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

            if self._output_dir:
                output_file = self._output_dir / f"screen-{monitor}.mp4"
                if output_file.exists():
                    paths.append(output_file)

        self._processes.clear()
        return paths

    def is_available(self) -> bool:
        return shutil.which("gpu-screen-recorder") is not None

    def _try_gpu_screen_recorder_list(self) -> list[MonitorInfo]:
        try:
            result = subprocess.run(
                ["gpu-screen-recorder", "--list-monitors"],
                capture_output=True, text=True, timeout=5,
            )
            monitors = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                # Output format: "eDP-1|1920x1080" — split on | to get name and resolution
                if "|" in line:
                    name, resolution = line.split("|", 1)
                else:
                    name, resolution = line, ""
                monitors.append(MonitorInfo(name=name.strip(), resolution=resolution.strip(), position=""))
            return monitors
        except Exception:
            return []

    def _try_kscreen_doctor(self) -> list[MonitorInfo]:
        try:
            result = subprocess.run(
                ["kscreen-doctor", "--outputs"],
                capture_output=True, text=True, timeout=5,
            )
            monitors = []
            for line in result.stdout.splitlines():
                match = re.match(r"Output:\s+\d+\s+(\S+)", line)
                if match:
                    monitors.append(MonitorInfo(
                        name=match.group(1), resolution="", position=""
                    ))
            return monitors
        except Exception:
            return []
