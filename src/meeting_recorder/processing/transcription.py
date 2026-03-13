"""Provider factory for transcription."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

# Maps litellm provider prefixes to env var names for API key lookup
_LITELLM_KEY_MAP = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}


def _resolve_key(config: dict, env_name: str) -> str:
    """Get API key from config api_keys dict, falling back to os.environ."""
    return config.get("api_keys", {}).get(env_name, "") or os.environ.get(env_name, "")


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str: ...


def create_transcription_provider(config: dict) -> TranscriptionProvider:
    """Factory: return the configured transcription provider."""
    provider = config.get("transcription_provider", "gemini")

    if provider == "gemini":
        from .providers.gemini import GeminiProvider
        return GeminiProvider(
            api_key=_resolve_key(config, "GEMINI_API_KEY"),
            model=config.get("gemini_model", "gemini-2.5-flash"),
            transcription_prompt=config.get("transcription_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 5),
        )

    if provider == "elevenlabs":
        from .providers.elevenlabs import ElevenLabsProvider
        return ElevenLabsProvider(
            api_key=_resolve_key(config, "ELEVENLABS_API_KEY"),
        )

    if provider == "whisper":
        from .providers.whisper import WhisperProvider
        return WhisperProvider(
            model=config.get("whisper_model", "large-v3-turbo"),
        )

    if provider == "litellm":
        from .providers.litellm_provider import LiteLLMTranscriptionProvider
        model = config.get("litellm_transcription_model", "groq/whisper-large-v3")
        prefix = model.split("/")[0] if "/" in model else ""
        key_name = _LITELLM_KEY_MAP.get(prefix, "")
        api_key = _resolve_key(config, key_name) if key_name else None
        return LiteLLMTranscriptionProvider(model=model, api_key=api_key)

    raise ValueError(f"Unknown transcription provider: {provider!r}")
