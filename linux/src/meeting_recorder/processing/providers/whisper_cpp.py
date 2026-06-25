"""
WhisperCppProvider: local speech-to-text using a whisper.cpp ``whisper-cli``
binary built from source (see services/whisper_cpp_service.py). Unlike the
faster-whisper engine, whisper.cpp can use AMD (ROCm/Vulkan) and Apple (Metal)
GPUs as well as NVIDIA/CPU.

Models are GGML ``.bin`` files downloaded on opt-in. Output matches the
faster-whisper provider: ``[HH:MM:SS] text`` segments, one per line.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def parse_whisper_cpp_output(raw: str) -> str:
    """Convert whisper.cpp JSON (``-oj``/``--output-json`` to stdout) into the
    shared ``[HH:MM:SS] text`` transcript format.

    Pure function so the formatting is unit-testable without the binary. The
    whisper.cpp JSON shape is ``{"transcription": [{"offsets": {"from": ms,
    "to": ms}, "text": "..."}, ...]}`` where offsets are in milliseconds.
    Falls back to returning the trimmed raw text if it is not valid JSON.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw.strip()

    segments = data.get("transcription", []) if isinstance(data, dict) else []
    lines = []
    for seg in segments:
        offsets = seg.get("offsets", {}) if isinstance(seg, dict) else {}
        start_ms = offsets.get("from", 0) or 0
        start = start_ms / 1000.0
        h = int(start // 3600)
        m = int((start % 3600) // 60)
        s = int(start % 60)
        text = (seg.get("text") or "").strip()
        if text:
            lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {text}")
    return "\n".join(lines)


class WhisperCppProvider:
    """Transcribes audio locally by shelling out to a whisper.cpp binary."""

    def __init__(
        self,
        model: str = "large-v3-turbo",
        binary_path: Path | None = None,
        model_path: Path | None = None,
        runner: Callable[[list[str]], str] | None = None,
    ) -> None:
        self._model_name = model
        self._binary = binary_path
        self._model_path = model_path
        self._runner = runner or _default_runner

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        if on_status:
            on_status(f"Transcribing with whisper.cpp ({self._model_name})…")

        binary = self._resolve_binary()
        model_file = self._resolve_model_path()

        cmd = [
            str(binary),
            "-m", str(model_file),
            "-f", str(audio_path),
            "--output-json-full",
            "--output-file", "-",
        ]
        logger.info("Running whisper.cpp: %s", " ".join(cmd))
        raw = self._runner(cmd)
        return parse_whisper_cpp_output(raw)

    def _resolve_binary(self) -> Path:
        if self._binary is not None:
            return self._binary
        from ...services.whisper_cpp_service import WHISPER_CPP_BINARY  # noqa: PLC0415
        return WHISPER_CPP_BINARY

    def _resolve_model_path(self) -> Path:
        if self._model_path is not None:
            return self._model_path
        from ...services.whisper_cpp_service import WhisperCppStatusChecker  # noqa: PLC0415
        return WhisperCppStatusChecker().model_path(self._model_name)


def _default_runner(cmd: list[str]) -> str:
    """Run *cmd* and return stdout; raise on a non-zero exit."""
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
