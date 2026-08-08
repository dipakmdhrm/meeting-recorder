"""
Tests for the Gemini provider's response-extraction helper ``_require_text``.

The module imports the ``google.genai`` SDK lazily (inside functions), so this helper is
importable and testable without the SDK installed. These tests pin two behaviours:

- a normal (``STOP``) response is returned verbatim even when its output-token count is
  large — no "near 8192 limit" truncation *warning* is emitted (that stale heuristic was
  removed; current models all allow 65,536 output tokens and the app requests the full
  budget), and
- a genuine ``MAX_TOKENS`` finish still raises the hard truncation error.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from meeting_recorder.processing.providers.gemini import _require_text

_LOGGER_NAME = "meeting_recorder.processing.providers.gemini"


def _response(
    text: str,
    *,
    out_tokens: int = 100,
    finish_reason: object = "STOP",
    prompt_feedback: object = None,
) -> SimpleNamespace:
    usage = SimpleNamespace(
        prompt_token_count=1000,
        candidates_token_count=out_tokens,
        total_token_count=1000 + out_tokens,
    )
    candidate = SimpleNamespace(finish_reason=finish_reason)
    return SimpleNamespace(
        usage_metadata=usage,
        candidates=[candidate],
        text=text,
        prompt_feedback=prompt_feedback,
    )


def test_large_output_returns_text_without_truncation_warning(caplog):
    """A 13k-token STOP response is complete — return it, don't warn about a 8192 limit."""
    response = _response("  transcript body  ", out_tokens=13191)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = _require_text(response, "transcription")

    assert result == "transcript body"
    # The removed heuristic used to log these; they must not reappear.
    assert "8192" not in caplog.text
    assert "truncat" not in caplog.text.lower()


def test_empty_text_raises():
    response = _response("", out_tokens=0)
    with pytest.raises(RuntimeError, match="returned no text"):
        _require_text(response, "transcription")


def test_max_tokens_finish_reason_still_raises():
    """The legitimate hard-truncation error (finish_reason == MAX_TOKENS) is preserved."""
    pytest.importorskip("google.genai")
    from google.genai import types

    response = _response("partial…", finish_reason=types.FinishReason.MAX_TOKENS)
    with pytest.raises(RuntimeError, match="truncated"):
        _require_text(response, "transcription")
