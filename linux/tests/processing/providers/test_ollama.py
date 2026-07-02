"""
Tests for OllamaProvider.summarize error handling.

All network calls are replaced with fakes via the provider's injected
``http_open`` hook — no real HTTP requests are made.
"""

import io
import json
import urllib.error

import pytest

from meeting_recorder.processing.providers.ollama import OllamaProvider


class FakeReadResponse:
    """Simulates a urllib response whose body is read all at once."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _provider(http_open) -> OllamaProvider:
    return OllamaProvider(model="phi4-mini", http_open=http_open)


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://localhost:11434/api/generate",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body),
    )


class TestSummarize:
    def test_returns_response_text_on_success(self):
        body = json.dumps({"response": "## Notes\n- point"}).encode()
        provider = _provider(lambda *a, **kw: FakeReadResponse(body))
        assert provider.summarize("transcript") == "## Notes\n- point"

    def test_passes_bounded_timeout(self):
        seen: dict = {}

        def capture(req, timeout=None):
            seen["timeout"] = timeout
            return FakeReadResponse(json.dumps({"response": "ok"}).encode())

        _provider(capture).summarize("transcript")
        assert seen["timeout"] is not None and seen["timeout"] > 0

    def test_raises_with_server_error_field_on_http_error(self):
        # e.g. requesting a model that isn't pulled → 404 with an error body
        err = _http_error(404, json.dumps({"error": 'model "phi4-mini" not found'}).encode())

        def fail(*a, **kw):
            raise err

        with pytest.raises(RuntimeError, match='model "phi4-mini" not found'):
            _provider(fail).summarize("transcript")

    def test_raises_with_http_code_when_error_body_unparseable(self):
        err = _http_error(500, b"<html>oops</html>")

        def fail(*a, **kw):
            raise err

        with pytest.raises(RuntimeError, match="HTTP 500"):
            _provider(fail).summarize("transcript")

    def test_raises_unreachable_message_on_connection_error(self):
        def fail(*a, **kw):
            raise urllib.error.URLError("connection refused")

        with pytest.raises(RuntimeError, match="Cannot reach Ollama"):
            _provider(fail).summarize("transcript")

    def test_raises_when_success_body_carries_error_field(self):
        body = json.dumps({"error": "model crashed"}).encode()
        provider = _provider(lambda *a, **kw: FakeReadResponse(body))
        with pytest.raises(RuntimeError, match="model crashed"):
            provider.summarize("transcript")

    def test_raises_on_empty_response(self):
        body = json.dumps({"response": ""}).encode()
        provider = _provider(lambda *a, **kw: FakeReadResponse(body))
        with pytest.raises(RuntimeError, match="empty response"):
            provider.summarize("transcript")
