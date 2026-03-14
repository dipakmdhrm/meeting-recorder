from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .base import AudioBackend, AudioDevice, CaptureOutputPaths

logger = logging.getLogger(__name__)

_PACTL_TIMEOUT = 5


def _run_pactl(*args: str) -> str:
    result = subprocess.run(
        ["pactl", *args],
        capture_output=True, text=True,
        timeout=_PACTL_TIMEOUT, check=True,
    )
    return result.stdout.strip()


class PulseAudioBackend(AudioBackend):
    """Audio backend using pactl + ffmpeg -f pulse."""

    def __init__(self) -> None:
        self._mic_proc: subprocess.Popen | None = None
        self._sys_proc: subprocess.Popen | None = None

    def list_sources(self) -> list[AudioDevice]:
        try:
            output = _run_pactl("list", "sources", "short")
            devices = []
            for line in output.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    devices.append(AudioDevice(name=parts[1], description=parts[1]))
            return devices
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return []

    def list_sinks(self) -> list[AudioDevice]:
        try:
            output = _run_pactl("list", "sinks", "short")
            devices = []
            for line in output.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    devices.append(AudioDevice(name=parts[1], description=parts[1]))
            return devices
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return []

    def get_default_source(self) -> AudioDevice | None:
        try:
            name = _run_pactl("get-default-source")
            return AudioDevice(name=name, description=name, is_default=True) if name else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def get_default_sink(self) -> AudioDevice | None:
        try:
            name = _run_pactl("get-default-sink")
            return AudioDevice(name=name, description=name, is_default=True) if name else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def start_capture(self, output_paths: CaptureOutputPaths, quality: str) -> None:
        source = self.get_default_source()
        if not source:
            raise RuntimeError("No default audio source found")

        mic_cmd = [
            "ffmpeg", "-y",
            "-f", "pulse", "-i", source.name,
            "-af", "highpass=f=80",
            "-acodec", "libmp3lame", "-q:a", quality,
            str(output_paths.mic),
        ]
        self._mic_proc = subprocess.Popen(
            mic_cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

        if output_paths.system is not None:
            sink = self.get_default_sink()
            if sink:
                monitor = f"{sink.name}.monitor"
                sys_cmd = [
                    "ffmpeg", "-y",
                    "-f", "pulse", "-i", monitor,
                    "-acodec", "libmp3lame", "-q:a", quality,
                    str(output_paths.system),
                ]
                self._sys_proc = subprocess.Popen(
                    sys_cmd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )

    def stop_capture(self) -> None:
        for proc in (self._mic_proc, self._sys_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._mic_proc = None
        self._sys_proc = None

    def is_available(self) -> bool:
        return (
            shutil.which("pactl") is not None
            and shutil.which("ffmpeg") is not None
        )

    def validate(self) -> tuple[bool, str]:
        if not self.is_available():
            return False, "pactl and/or ffmpeg not found"
        source = self.get_default_source()
        if not source:
            return False, "No default audio source (microphone) detected"
        return True, ""
