"""
Defines the SummarizationProvider protocol and a factory function to instantiate configured summarization services. This abstraction allows the application to support multiple AI backends for generating meeting notes.
"""

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
            model=config.get("gemini_model", "gemini-2.5-flash"),
            summarization_prompt=config.get("summarization_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 3),
        )

    if service == "ollama":
        from .providers.ollama import OllamaProvider
        return OllamaProvider(
            model=config.get("ollama_model", "phi4-mini"),
            host=config.get("ollama_host", "http://localhost:11434"),
            summarization_prompt=config.get("summarization_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 10),
        )

    raise ValueError(f"Unknown summarization service: {service!r}")
