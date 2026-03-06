"""
Defines the TranscriptionProvider protocol and a factory function to instantiate configured transcription services. This provides a consistent interface for converting audio recordings into text using different AI providers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Transcribe audio file. Returns transcript text."""
        ...


def create_transcription_provider(config: dict) -> TranscriptionProvider:
    """Factory: return the configured transcription provider."""
    service = config.get("transcription_service", "gemini")

    if service == "gemini":
        from .providers.gemini import GeminiProvider
        return GeminiProvider(
            api_key=config["gemini_api_key"],
            model=config.get("gemini_model", "gemini-2.5-flash"),
            transcription_prompt=config.get("transcription_prompt", ""),
        )
    else:
        raise ValueError(f"Unknown transcription service: {service!r}")
