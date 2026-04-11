"""
Implementation of the Google Gemini AI provider. It handles the specific requirements of the Gemini API, including uploading audio files, polling for processing status, and executing both transcription and summarization prompts with appropriate model configurations.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from ...config.defaults import (
    GEMINI_TRANSCRIPTION_PROMPT,
    SUMMARIZATION_PROMPT,
)

logger = logging.getLogger(__name__)

# Polling interval when waiting for Gemini file processing
_POLL_INTERVAL = 3  # seconds
_POLL_TIMEOUT = 300  # 5 minutes

# Temperature for transcription: 0 = deterministic, sticks closely to spoken words
_TRANSCRIPTION_TEMPERATURE = 0


# Request the maximum output tokens so long transcripts are never silently truncated.
# The API caps this at the model's own limit, so it is safe to set high.
_MAX_OUTPUT_TOKENS = 65_536


def _require_text(response, context: str) -> str:
    """Extract text from a GenerateContentResponse, raising clearly if empty or truncated."""
    # Log token usage and finish_reason for diagnostics
    _truncation_error = None
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage:
            logger.info(
                "%s token usage — input: %s, output: %s, total: %s",
                context,
                getattr(usage, "prompt_token_count", "?"),
                getattr(usage, "candidates_token_count", "?"),
                getattr(usage, "total_token_count", "?"),
            )
        candidate = response.candidates[0] if response.candidates else None
        if candidate:
            finish_reason = getattr(candidate, "finish_reason", None)
            logger.info("%s finish_reason: %s", context, finish_reason)
            try:
                from google.genai import types
                if finish_reason == types.FinishReason.MAX_TOKENS:
                    _truncation_error = RuntimeError(
                        f"Gemini output was truncated ({context}): the response hit the token limit. "
                        "Try a shorter recording, or switch to gemini-2.5-flash / gemini-2.5-pro "
                        "which support up to 65,536 output tokens."
                    )
            except ImportError:
                pass
            # Some models report STOP even when truncated — warn if output tokens
            # are suspiciously close to a common model limit (8192)
            if usage and not _truncation_error:
                out_tokens = getattr(usage, "candidates_token_count", 0) or 0
                if out_tokens >= 8000:
                    logger.warning(
                        "%s: output tokens (%d) near 8192 limit — transcript may be truncated. "
                        "Consider using gemini-2.5-flash which supports up to 65,536 output tokens.",
                        context, out_tokens,
                    )
    except Exception:
        pass
    if _truncation_error:
        raise _truncation_error

    text = response.text
    if not text:
        feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(
            f"Gemini returned no text for {context}. "
            f"prompt_feedback={feedback}"
        )
    return text.strip()


def _wrap_timeout(exc: Exception, context: str, timeout_ms: int) -> Exception:
    """Convert httpx/httpcore timeout errors into a readable RuntimeError."""
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        minutes = timeout_ms // 60_000
        return RuntimeError(
            f"Gemini did not respond within {minutes} minutes ({context}). "
            "The audio may be too long, or Gemini may be overloaded. "
            "Try again, or use a shorter recording."
        )
    return exc


class GeminiProvider:
    """
    Handles both transcription (audio → text) and summarization (text → notes).

    When used for transcription: uploads audio, polls until ACTIVE, transcribes.
    When used for summarization: sends text prompt.
    The pipeline checks for the dual-call optimization.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        transcription_prompt: str = "",
        summarization_prompt: str = "",
        timeout_minutes: int = 3,
    ) -> None:
        self._api_key = api_key
        self._model = model
        # Fall back to built-in defaults if no custom prompt is configured.
        self._transcription_prompt = transcription_prompt or GEMINI_TRANSCRIPTION_PROMPT
        self._summarization_prompt = summarization_prompt or SUMMARIZATION_PROMPT
        self._generate_timeout_ms = timeout_minutes * 60_000
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise ImportError(
                    "google-genai is not installed. "
                    "Run: pip install google-genai"
                )
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Transcribe audio file using Gemini Files API."""
        client = self._get_client()

        if on_status:
            on_status("Uploading audio to Gemini…")

        logger.info("Uploading %s to Gemini Files API", audio_path)
        uploaded = client.files.upload(
            file=str(audio_path),
            # mime_type must be specified explicitly; the SDK does not infer it from
            # the file extension for audio files, and omitting it causes a 400 error.
            config={"mime_type": "audio/mpeg"},
        )

        # After upload, Google's servers transcode and analyse the audio. The file
        # state transitions PROCESSING → ACTIVE before it can be referenced in prompts.
        uploaded = self._wait_for_active(client, uploaded, on_status)

        if on_status:
            on_status("Transcribing with Gemini…")

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[uploaded, self._transcription_prompt],
                config={
                    "temperature": _TRANSCRIPTION_TEMPERATURE,
                    "max_output_tokens": _MAX_OUTPUT_TOKENS,
                    "http_options": {"timeout": self._generate_timeout_ms},
                },
            )
        except Exception as exc:
            raise _wrap_timeout(exc, "transcription", self._generate_timeout_ms) from exc
        return _require_text(response, "transcription")

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Summarize transcript text using Gemini."""
        client = self._get_client()

        if on_status:
            on_status("Summarizing with Gemini…")

        try:
            prompt = self._summarization_prompt.format(transcript=transcript)
        except KeyError:
            # User removed {transcript} from their custom prompt — append it manually.
            prompt = self._summarization_prompt + f"\n\nTRANSCRIPT:\n{transcript}"
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[prompt],
                config={
                    "max_output_tokens": _MAX_OUTPUT_TOKENS,
                    "http_options": {"timeout": self._generate_timeout_ms},
                },
            )
        except Exception as exc:
            raise _wrap_timeout(exc, "summarization", self._generate_timeout_ms) from exc
        return _require_text(response, "summarization")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wait_for_active(self, client, file_obj, on_status):
        """Poll until the uploaded file reaches ACTIVE state."""
        from google.genai import types

        deadline = time.time() + _POLL_TIMEOUT
        while True:
            state = file_obj.state

            if state == types.FileState.ACTIVE:
                return file_obj
            if state in (types.FileState.FAILED, types.FileState.STATE_UNSPECIFIED):
                raise RuntimeError(
                    f"Gemini file processing failed (state={state})"
                )

            if time.time() > deadline:
                raise TimeoutError("Timed out waiting for Gemini file to become active")

            state_label = state.value if state else "unknown"
            if on_status:
                on_status(f"Waiting for Gemini to process audio… ({state_label})")

            time.sleep(_POLL_INTERVAL)
            file_obj = client.files.get(name=file_obj.name)

