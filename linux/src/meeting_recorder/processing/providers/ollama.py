"""
OllamaProvider: local LLM summarization via Ollama's HTTP API.
Requires ollama to be installed and running (ollama serve).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Callable

from ...config.defaults import OLLAMA_DEFAULT_HOST, SUMMARIZATION_PROMPT

logger = logging.getLogger(__name__)


def get_loaded_models(host: str) -> list[str]:
    """Return names of models currently loaded in ollama's memory. Empty list if unreachable."""
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def unload_model(host: str, model: str) -> None:
    """Unload a specific model from ollama's memory (keep_alive=0)."""
    payload = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        logger.info("Unloaded ollama model from memory: %s", model)
    except Exception as exc:
        logger.warning("Failed to unload ollama model %s: %s", model, exc)


def unload_all_models(host: str) -> None:
    """Unload every model currently loaded in ollama's memory."""
    for model in get_loaded_models(host):
        unload_model(host, model)


class OllamaProvider:
    """Summarizes transcripts using a locally-running Ollama model."""

    def __init__(
        self,
        model: str = "phi4-mini",
        host: str = OLLAMA_DEFAULT_HOST,
        summarization_prompt: str = "",
        timeout_minutes: int = 10,
    ) -> None:
        self._model = model
        self._host = host.rstrip("/")
        self._summarization_prompt = summarization_prompt or SUMMARIZATION_PROMPT
        self._timeout = timeout_minutes * 60

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        if on_status:
            on_status(f"Summarizing with Ollama ({self._model})\u2026")

        try:
            prompt = self._summarization_prompt.format(transcript=transcript)
        except KeyError:
            prompt = self._summarization_prompt + f"\n\nTRANSCRIPT:\n{transcript}"

        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self._host}. "
                "Make sure ollama is running: ollama serve"
            ) from exc
        except TimeoutError:
            raise RuntimeError(
                f"Ollama did not respond within {self._timeout // 60} minutes. "
                "The transcript may be too long, or the model may be overloaded."
            )

        response = data.get("response", "").strip()
        if not response:
            raise RuntimeError(
                f"Ollama returned an empty response for model {self._model!r}."
            )
        return response

    def unload(self) -> None:
        """Unload this model from ollama's memory."""
        unload_model(self._host, self._model)
