"""
Tests for OllamaClient.

All network calls are replaced with fake context managers so no real HTTP
requests are made.
"""
import json
import pytest
from meeting_recorder.services.ollama_service import OllamaClient


# ── fake HTTP helpers ─────────────────────────────────────────────────────────

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


class FakeStreamResponse:
    """Simulates a streaming urllib response read line-by-line."""
    def __init__(self, lines: list[bytes]):
        self._iter = iter(lines)

    def readline(self) -> bytes:
        return next(self._iter, b"")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _tags_response(model_names: list[str]) -> FakeReadResponse:
    body = json.dumps({"models": [{"name": n} for n in model_names]}).encode()
    return FakeReadResponse(body)


# ── get_installed_models ──────────────────────────────────────────────────────

class TestGetInstalledModels:
    HOST = "http://localhost:11434"

    def test_returns_model_names_on_success(self):
        client = OllamaClient(http_open=lambda *a, **kw: _tags_response(["phi4-mini", "llama3"]))
        assert client.get_installed_models(self.HOST) == ["phi4-mini", "llama3"]

    def test_returns_empty_list_when_no_models_installed(self):
        client = OllamaClient(http_open=lambda *a, **kw: _tags_response([]))
        assert client.get_installed_models(self.HOST) == []

    def test_returns_none_on_connection_error(self):
        def fail(*a, **kw): raise OSError("connection refused")
        client = OllamaClient(http_open=fail)
        assert client.get_installed_models(self.HOST) is None

    def test_returns_none_on_malformed_json(self):
        client = OllamaClient(http_open=lambda *a, **kw: FakeReadResponse(b"not json"))
        assert client.get_installed_models(self.HOST) is None


# ── is_model_installed ────────────────────────────────────────────────────────

class TestIsModelInstalled:
    def _client(self):
        return OllamaClient()  # http_open not used in this method

    def test_exact_name_match(self):
        assert self._client().is_model_installed("phi4-mini", ["phi4-mini"]) is True

    def test_prefix_match_with_tag(self):
        # "phi4-mini" should match "phi4-mini:latest"
        assert self._client().is_model_installed("phi4-mini", ["phi4-mini:latest"]) is True

    def test_no_match_returns_false(self):
        assert self._client().is_model_installed("phi4-mini", ["llama3", "mistral"]) is False

    def test_partial_prefix_does_not_match(self):
        # "phi4" must not match "phi4-mini:latest"
        assert self._client().is_model_installed("phi4", ["phi4-mini:latest"]) is False


# ── pull_model ────────────────────────────────────────────────────────────────

class TestPullModel:
    HOST = "http://localhost:11434"

    def _pull(self, client, model="phi4-mini"):
        msgs: list[str] = []
        result = client.pull_model(model, self.HOST, msgs.append)
        return result, msgs

    # ── happy path ────────────────────────────────────────────────────────────

    def test_returns_true_when_stream_contains_success_status(self):
        lines = [
            json.dumps({"status": "pulling"}).encode() + b"\n",
            json.dumps({"status": "success"}).encode() + b"\n",
        ]
        client = OllamaClient(http_open=lambda *a, **kw: FakeStreamResponse(lines))
        result, _ = self._pull(client)
        assert result is True

    def test_progress_callback_receives_status_text(self):
        lines = [
            json.dumps({"status": "pulling manifest"}).encode() + b"\n",
            json.dumps({"status": "success"}).encode() + b"\n",
        ]
        client = OllamaClient(http_open=lambda *a, **kw: FakeStreamResponse(lines))
        _, msgs = self._pull(client)
        assert "pulling manifest" in msgs

    def test_progress_callback_includes_percentage_when_progress_known(self):
        lines = [
            json.dumps({"status": "downloading", "total": 1000, "completed": 500}).encode() + b"\n",
            json.dumps({"status": "success"}).encode() + b"\n",
        ]
        client = OllamaClient(http_open=lambda *a, **kw: FakeStreamResponse(lines))
        _, msgs = self._pull(client)
        assert any("50%" in m for m in msgs)

    # ── fallback path (stream ends without explicit "success") ────────────────

    def test_fallback_true_when_model_appears_in_installed_list(self):
        # First call → pull stream with no "success"; second call → tags shows model present
        stream_lines = [json.dumps({"status": "done"}).encode() + b"\n"]
        responses = iter([
            FakeStreamResponse(stream_lines),
            _tags_response(["phi4-mini"]),
        ])
        client = OllamaClient(http_open=lambda *a, **kw: next(responses))
        result, _ = self._pull(client)
        assert result is True

    def test_fallback_false_when_model_absent_from_installed_list(self):
        stream_lines = [json.dumps({"status": "done"}).encode() + b"\n"]
        responses = iter([
            FakeStreamResponse(stream_lines),
            _tags_response([]),
        ])
        client = OllamaClient(http_open=lambda *a, **kw: next(responses))
        result, _ = self._pull(client)
        assert result is False

    # ── error handling ────────────────────────────────────────────────────────

    def test_raises_on_network_error(self):
        def fail(*a, **kw): raise OSError("network error")
        client = OllamaClient(http_open=fail)
        with pytest.raises(OSError):
            self._pull(client)

    def test_silently_skips_malformed_json_lines(self):
        lines = [
            b"not json\n",
            json.dumps({"status": "success"}).encode() + b"\n",
        ]
        client = OllamaClient(http_open=lambda *a, **kw: FakeStreamResponse(lines))
        result, _ = self._pull(client)
        assert result is True
