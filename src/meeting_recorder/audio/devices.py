"""
Provides utilities for querying and validating audio devices using PulseAudio/PipeWire (via pactl). It identifies default input/output devices and manages the resolution of monitor sources for capturing system audio.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def _run_pactl(*args: str) -> str:
    result = subprocess.run(
        ["pactl", *args],
        capture_output=True,
        text=True,
        # pactl is instantaneous under normal conditions. A longer timeout would
        # stall the UI thread if PipeWire/PulseAudio is hung or restarting.
        timeout=5,
    )
    result.check_returncode()
    return result.stdout


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
    """Return the monitor source name for a given sink (for loopback recording).

    PipeWire/PulseAudio automatically creates a virtual "<sink>.monitor" source for
    every sink. Recording from it captures whatever audio is being played out through
    that sink — no loopback module or system configuration required.
    """
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
