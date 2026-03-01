"""OpenAI GPT-4o summarization provider."""

from __future__ import annotations

import logging
from typing import Callable

from ...config.defaults import SUMMARIZATION_PROMPT

logger = logging.getLogger(__name__)


class GPT4oProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        if on_status:
            on_status(f"Summarizing with {self._model}…")

        client = self._get_client()
        prompt = SUMMARIZATION_PROMPT.format(transcript=transcript)

        response = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
