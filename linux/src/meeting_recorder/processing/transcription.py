"""
Defines the TranscriptionProvider protocol and a factory function to instantiate configured
transcription services. This provides a consistent interface for converting audio recordings into
text using different AI providers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..core.task_runner import CancelToken


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> str:
        """Transcribe audio file. Returns transcript text.

        ``cancel_token`` lets a provider that makes several calls for one
        recording (the chunked Gemini transcription path) abandon the rest of
        the work partway through. Single-call providers may ignore it.
        """
        ...


def create_transcription_provider(config: dict[str, Any]) -> TranscriptionProvider:
    """Factory: return the configured transcription provider."""
    service = config.get("transcription_service", "gemini")

    if service == "gemini":
        from .providers.gemini import GeminiProvider

        return GeminiProvider(
            api_key=config["gemini_api_key"],
            model=config.get("gemini_transcription_model", "gemini-2.5-flash"),
            transcription_prompt=config.get("transcription_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 3),
        )

    if service == "gemini_transcribe":
        from .providers.gemini_transcribe import GeminiTranscribeProvider

        return GeminiTranscribeProvider(
            api_key=config["gemini_api_key"],
            model=config.get("gemini_transcribe_model", "gemini-3.5-transcribe"),
            timeout_minutes=config.get("llm_request_timeout_minutes", 5),
        )

    if service == "whisper":
        from .providers.whisper import WhisperProvider

        return WhisperProvider(
            model=config.get("whisper_model", "large-v3-turbo"),
        )

    if service == "whisper_cpp":
        from .providers.whisper_cpp import WhisperCppProvider

        return WhisperCppProvider(
            model=config.get("whisper_cpp_model", "large-v3-turbo"),
        )

    raise ValueError(f"Unknown transcription service: {service!r}")
