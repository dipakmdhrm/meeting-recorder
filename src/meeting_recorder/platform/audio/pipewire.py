from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .base import AudioBackend, AudioDevice, CaptureOutputPaths

logger = logging.getLogger(__name__)


class PipeWireBackend(AudioBackend):
    """Audio backend using wpctl for device enumeration + ffmpeg -f pulse for capture.

    Uses PipeWire's PulseAudio compatibility layer for ffmpeg capture (since
    pw-record cannot encode MP3 and ffmpeg -f pipewire doesn't exist).
    Device enumeration uses wpctl (WirePlumber) for PipeWire-native detection.
    """

    def __init__(self) -> None:
        self._mic_proc: subprocess.Popen | None = None
        self._sys_proc: subprocess.Popen | None = None

    def _wpctl_status(self) -> str:
        result = subprocess.run(
            ["wpctl", "status"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout

    def _wpctl_inspect(self, node_id: str) -> str:
        result = subprocess.run(
            ["wpctl", "inspect", node_id],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout

    def list_sources(self) -> list[AudioDevice]:
        return self._list_devices("Sources")

    def list_sinks(self) -> list[AudioDevice]:
        return self._list_devices("Sinks")

    def _list_devices(self, section: str) -> list[AudioDevice]:
        """Parse wpctl status output for devices in given section."""
        try:
            status = self._wpctl_status()
            devices = []
            in_audio = False
            in_section = False

            for line in status.splitlines():
                stripped = line.strip()
                if stripped == "Audio":
                    in_audio = True
                    continue
                if in_audio and section in line:
                    in_section = True
                    continue
                if in_section and line.strip() == "":
                    break
                if in_section:
                    # Strip box-drawing chars (│├└─) before matching
                    clean = line.lstrip(" │├└─")
                    match = re.match(r"(\*)?\s*(\d+)\.\s+(.+?)(?:\s+\[|$)", clean)
                    if match:
                        is_default = match.group(1) == "*"
                        name = match.group(3).strip()
                        devices.append(AudioDevice(
                            name=name, description=name, is_default=is_default
                        ))
            return devices
        except Exception:
            logger.debug("Failed to enumerate devices via wpctl", exc_info=True)
            return []

    def get_default_source(self) -> AudioDevice | None:
        sources = self.list_sources()
        for s in sources:
            if s.is_default:
                return s
        return sources[0] if sources else None

    def get_default_sink(self) -> AudioDevice | None:
        sinks = self.list_sinks()
        for s in sinks:
            if s.is_default:
                return s
        return sinks[0] if sinks else None

    def start_capture(self, output_paths: CaptureOutputPaths, quality: str) -> None:
        source = self._get_pulse_default_source()
        if not source:
            raise RuntimeError("No default audio source found")

        mic_cmd = [
            "ffmpeg", "-y",
            "-f", "pulse", "-i", source,
            "-af", "highpass=f=80",
            "-acodec", "libmp3lame", "-q:a", quality,
            str(output_paths.mic),
        ]
        self._mic_proc = subprocess.Popen(
            mic_cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

        if output_paths.system is not None:
            sink = self._get_pulse_default_sink()
            if sink:
                monitor = f"{sink}.monitor"
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
            shutil.which("wpctl") is not None
            and shutil.which("ffmpeg") is not None
        )

    def validate(self) -> tuple[bool, str]:
        if not self.is_available():
            return False, "wpctl and/or ffmpeg not found"
        # Check via pactl — that's what start_capture() actually uses
        if not self._get_pulse_default_source():
            return False, "No default audio source found"
        return True, ""

    @staticmethod
    def _get_pulse_default_source() -> str | None:
        try:
            result = subprocess.run(
                ["pactl", "get-default-source"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            return result.stdout.strip() or None
        except Exception:
            return None

    @staticmethod
    def _get_pulse_default_sink() -> str | None:
        try:
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            return result.stdout.strip() or None
        except Exception:
            return None
