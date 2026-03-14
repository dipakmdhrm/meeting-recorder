"""ElevenLabs Scribe v2 transcription provider with native diarization."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class ElevenLabsProvider:
    """Transcription via ElevenLabs Scribe v2 API with native diarization."""

    def __init__(self, api_key: str, timeout: int = 300) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            from elevenlabs import ElevenLabs
            self._client = ElevenLabs(api_key=self._api_key)
        return self._client

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Transcribe audio using ElevenLabs Scribe v2.

        Returns markdown-formatted transcript with speaker labels and timestamps.
        """
        if on_status:
            on_status("Uploading to ElevenLabs...")

        client = self._get_client()

        with open(audio_path, "rb") as f:
            result = client.speech_to_text.convert(
                file=f,
                model_id="scribe_v2",
                tag_audio_events=False,
                diarize=True,
            )

        if on_status:
            on_status("Processing transcript...")

        return self._format_transcript(result)

    def _format_transcript(self, result) -> str:
        """Convert ElevenLabs response to timestamped, speaker-labeled markdown."""
        if not result.words:
            return result.text or ""

        lines = []
        current_speaker = None
        current_text = []
        segment_start = 0.0

        for word in result.words:
            speaker = getattr(word, "speaker_id", None) or "Unknown"

            if speaker != current_speaker:
                # Flush previous segment
                if current_text:
                    timestamp = self._format_timestamp(segment_start)
                    label = self._speaker_label(current_speaker)
                    lines.append(f"[{timestamp}] **{label}:** {' '.join(current_text)}")

                current_speaker = speaker
                current_text = []
                segment_start = getattr(word, "start", 0.0)

            current_text.append(word.text)

        # Flush last segment
        if current_text:
            timestamp = self._format_timestamp(segment_start)
            label = self._speaker_label(current_speaker)
            lines.append(f"[{timestamp}] **{label}:** {' '.join(current_text)}")

        return "\n\n".join(lines)

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _speaker_label(speaker_id: str | None) -> str:
        if not speaker_id:
            return "Unknown"
        # ElevenLabs returns speaker_0, speaker_1, etc.
        try:
            num = int(speaker_id.split("_")[-1]) + 1
            return f"Speaker {num}"
        except (ValueError, IndexError):
            return speaker_id
