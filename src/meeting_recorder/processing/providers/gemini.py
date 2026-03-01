"""Google Gemini provider: audio transcription + summarization (single or dual call)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from ...config.defaults import (
    GEMINI_DUAL_PROMPT,
    GEMINI_TRANSCRIPTION_PROMPT,
    SUMMARIZATION_PROMPT,
)

logger = logging.getLogger(__name__)

# Polling interval when waiting for Gemini file processing
_POLL_INTERVAL = 3  # seconds
_POLL_TIMEOUT = 300  # 5 minutes

# Temperature for transcription: 0 = deterministic, sticks closely to spoken words
_TRANSCRIPTION_TEMPERATURE = 0

# Timeout for generate_content calls in milliseconds (as required by HttpOptions.timeout).
# Gemini can take several minutes to process long audio before returning any response.
_GENERATE_TIMEOUT_MS = 60_000  # 1 minute


def _require_text(response, context: str) -> str:
    """Extract text from a GenerateContentResponse, raising clearly if empty."""
    text = response.text
    if not text:
        feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(
            f"Gemini returned no text for {context}. "
            f"prompt_feedback={feedback}"
        )
    return text.strip()


def _wrap_timeout(exc: Exception, context: str) -> Exception:
    """Convert httpx/httpcore timeout errors into a readable RuntimeError."""
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        minutes = _GENERATE_TIMEOUT_MS // 60_000
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

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._api_key = api_key
        self._model = model
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
            config={"mime_type": "audio/mpeg"},
        )

        uploaded = self._wait_for_active(client, uploaded, on_status)

        if on_status:
            on_status("Transcribing with Gemini…")

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[uploaded, GEMINI_TRANSCRIPTION_PROMPT],
                config={
                    "temperature": _TRANSCRIPTION_TEMPERATURE,
                    "http_options": {"timeout": _GENERATE_TIMEOUT_MS},
                },
            )
        except Exception as exc:
            raise _wrap_timeout(exc, "transcription") from exc
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

        prompt = SUMMARIZATION_PROMPT.format(transcript=transcript)
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[prompt],
                config={"http_options": {"timeout": _GENERATE_TIMEOUT_MS}},
            )
        except Exception as exc:
            raise _wrap_timeout(exc, "summarization") from exc
        return _require_text(response, "summarization")

    # ------------------------------------------------------------------
    # Dual-call: single API call for both transcription + summarization
    # ------------------------------------------------------------------

    def transcribe_and_summarize(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> tuple[str, str]:
        """
        Single Gemini call that returns (transcript, notes).
        Used when both services are Gemini for maximum efficiency.
        """
        client = self._get_client()

        if on_status:
            on_status("Uploading audio to Gemini…")

        uploaded = client.files.upload(
            file=str(audio_path),
            config={"mime_type": "audio/mpeg"},
        )
        uploaded = self._wait_for_active(client, uploaded, on_status)

        if on_status:
            on_status("Processing with Gemini (transcription + notes)…")

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[uploaded, GEMINI_DUAL_PROMPT],
                config={
                    "temperature": _TRANSCRIPTION_TEMPERATURE,
                    "http_options": {"timeout": _GENERATE_TIMEOUT_MS},
                },
            )
        except Exception as exc:
            raise _wrap_timeout(exc, "transcription+summarization") from exc
        text = _require_text(response, "transcription+summarization")
        return self._parse_dual_response(text)

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

    @staticmethod
    def _parse_dual_response(text: str) -> tuple[str, str]:
        """Parse response containing --- TRANSCRIPT --- and --- NOTES --- sections."""
        transcript = ""
        notes = ""

        transcript_marker = "--- TRANSCRIPT ---"
        notes_marker = "--- NOTES ---"

        if transcript_marker in text and notes_marker in text:
            t_start = text.index(transcript_marker) + len(transcript_marker)
            n_start = text.index(notes_marker)
            transcript = text[t_start:n_start].strip()
            notes = text[n_start + len(notes_marker):].strip()
        elif transcript_marker in text:
            t_start = text.index(transcript_marker) + len(transcript_marker)
            transcript = text[t_start:].strip()
        else:
            transcript = text.strip()

        return transcript, notes
