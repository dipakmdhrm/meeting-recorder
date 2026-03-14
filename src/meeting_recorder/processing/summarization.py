"""Provider factory for summarization."""
from __future__ import annotations

import os
from typing import Callable, Protocol, runtime_checkable

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
class SummarizationProvider(Protocol):
    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str: ...


def create_summarization_provider(config: dict) -> SummarizationProvider:
    """Factory: return the configured summarization provider."""
    provider = config.get("summarization_provider", "litellm")

    if provider == "claude_code":
        from .providers.claude_code import ClaudeCodeProvider
        return ClaudeCodeProvider(
            timeout=config.get("llm_request_timeout_minutes", 5) * 60,
        )

    if provider == "litellm":
        from .providers.litellm_provider import LiteLLMSummarizationProvider
        model = config.get("litellm_summarization_model", "gemini/gemini-2.5-flash")
        prefix = model.split("/")[0] if "/" in model else ""
        key_name = _LITELLM_KEY_MAP.get(prefix, "")
        api_key = _resolve_key(config, key_name) if key_name else None
        return LiteLLMSummarizationProvider(
            model=model,
            api_key=api_key,
            summarization_prompt=config.get("summarization_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 5),
        )

    raise ValueError(f"Unknown summarization provider: {provider!r}")
