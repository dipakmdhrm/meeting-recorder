"""SummarizationProvider protocol and factory."""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class SummarizationProvider(Protocol):
    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Summarize transcript text. Returns meeting notes markdown."""
        ...


def create_summarization_provider(config: dict) -> SummarizationProvider:
    """Factory: return the configured summarization provider."""
    service = config.get("summarization_service", "gemini")

    if service == "gemini":
        from .providers.gemini import GeminiProvider
        return GeminiProvider(
            api_key=config["gemini_api_key"],
            model=config.get("gemini_model", "gemini-1.5-flash"),
        )
    elif service == "gpt4o":
        from .providers.gpt4o import GPT4oProvider
        return GPT4oProvider(
            api_key=config["openai_api_key"],
            model=config.get("openai_summarization_model", "gpt-4o"),
        )
    else:
        raise ValueError(f"Unknown summarization service: {service!r}")
