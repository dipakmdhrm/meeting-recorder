"""
Testable HTTP client for the Ollama local API.

Inject ``http_open`` in tests to avoid real network calls:

    fake_response = FakeResponse(b'{"models": [{"name": "phi4-mini"}]}')
    client = OllamaClient(http_open=lambda *a, **kw: fake_response)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Callable

logger = logging.getLogger(__name__)


class OllamaClient:
    """HTTP client for the Ollama local API."""

    def __init__(self, http_open: Callable | None = None) -> None:
        self._http_open = http_open or urllib.request.urlopen

    def get_installed_models(self, host: str) -> list[str] | None:
        """Return installed model names, or ``None`` if Ollama is unreachable."""
        try:
            with self._http_open(f"{host}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return None

    def is_model_installed(self, model: str, installed: list[str]) -> bool:
        return any(n == model or n.startswith(f"{model}:") for n in installed)

    def pull_model(
        self,
        model: str,
        host: str,
        on_progress: Callable[[str], None],
    ) -> bool:
        """
        Stream-pull *model* from Ollama.

        Calls ``on_progress`` with a human-readable status string as data
        arrives.  Returns ``True`` when the server confirms success, ``False``
        if the stream ended without an explicit success message.
        Raises on network error.
        """
        payload = json.dumps({"name": model, "stream": True}).encode()
        req = urllib.request.Request(
            f"{host}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._http_open(req, timeout=None) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                status_text = data.get("status", "")
                total = data.get("total", 0)
                completed = data.get("completed", 0)
                if total and completed:
                    pct = int(completed / total * 100)
                    status_text = f"{status_text} {pct}%"
                on_progress(status_text)
                if data.get("status") == "success":
                    return True

        # Stream ended without explicit "success" — do one final check.
        installed = self.get_installed_models(host)
        return installed is not None and self.is_model_installed(model, installed)
