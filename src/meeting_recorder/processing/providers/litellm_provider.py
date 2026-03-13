"""LiteLLM-based providers for transcription and summarization."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class LiteLLMTranscriptionProvider:
    """Transcribes audio via litellm.transcription() — supports Groq Whisper, OpenAI Whisper, Deepgram, etc."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        import litellm

        if on_status:
            on_status(f"Transcribing with {self._model}\u2026")

        kwargs: dict = {"model": self._model}
        if self._api_key:
            kwargs["api_key"] = self._api_key

        with open(audio_path, "rb") as f:
            kwargs["file"] = f
            response = litellm.transcription(**kwargs)

        return response.text


class LiteLLMSummarizationProvider:
    """Summarizes transcripts via litellm.completion() — supports 100+ LLM providers."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        summarization_prompt: str = "",
        timeout_minutes: int = 5,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._prompt = summarization_prompt
        self._timeout = timeout_minutes * 60

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        import litellm

        if on_status:
            on_status(f"Summarizing with {self._model}\u2026")

        prompt = self._prompt
        try:
            prompt = prompt.format(transcript=transcript)
        except (KeyError, IndexError):
            prompt = prompt + f"\n\nTRANSCRIPT:\n{transcript}"

        kwargs: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": self._timeout,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key

        response = litellm.completion(**kwargs)

        text = response.choices[0].message.content.strip()
        if not text:
            raise RuntimeError(
                f"LiteLLM returned empty response for model {self._model!r}"
            )
        return text
