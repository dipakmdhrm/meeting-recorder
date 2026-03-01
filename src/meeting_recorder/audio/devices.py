"""pactl-based audio device enumeration and validation."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioDevice:
    name: str
    description: str
    kind: str  # "source" or "sink"


def _run_pactl(*args: str) -> str:
    result = subprocess.run(
        ["pactl", *args],
        capture_output=True,
        text=True,
        timeout=5,
    )
    result.check_returncode()
    return result.stdout


def list_sources() -> list[AudioDevice]:
    """Return all PulseAudio/PipeWire sources (microphone inputs)."""
    return _parse_devices("list", "short", "sources", "source")


def list_sinks() -> list[AudioDevice]:
    """Return all PulseAudio/PipeWire sinks (output devices)."""
    return _parse_devices("list", "short", "sinks", "sink")


def _parse_devices(cmd: str, *args: str, kind: str) -> list[AudioDevice]:
    try:
        output = _run_pactl(cmd, *args)
    except Exception as exc:
        logger.error("pactl %s %s failed: %s", cmd, " ".join(args), exc)
        return []

    devices = []
    for line in output.splitlines():
        # Short format: index \t name \t driver \t sample \t state
        parts = line.strip().split("\t")
        if len(parts) >= 2:
            name = parts[1]
            description = parts[1]  # Short format lacks description
            devices.append(AudioDevice(name=name, description=description, kind=kind))
    return devices


def get_default_source() -> str | None:
    """Return name of the default PulseAudio source (microphone)."""
    try:
        output = _run_pactl("get-default-source")
        return output.strip() or None
    except Exception as exc:
        logger.warning("Could not get default source: %s", exc)
        return None


def get_default_sink() -> str | None:
    """Return name of the default PulseAudio sink."""
    try:
        output = _run_pactl("get-default-sink")
        return output.strip() or None
    except Exception as exc:
        logger.warning("Could not get default sink: %s", exc)
        return None


def get_monitor_source(sink_name: str) -> str:
    """Return the monitor source name for a given sink (for loopback recording)."""
    return f"{sink_name}.monitor"


def validate_devices() -> tuple[bool, str]:
    """
    Validate that required audio devices exist.
    Returns (ok, error_message).
    """
    mic = get_default_source()
    if not mic:
        return False, "No microphone (audio source) found."

    sink = get_default_sink()
    if not sink:
        return False, "No audio output device (sink) found."

    return True, ""
