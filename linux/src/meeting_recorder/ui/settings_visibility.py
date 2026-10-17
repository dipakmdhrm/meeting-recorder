"""
Pure visibility policy for the Settings → Models tab.

Kept GTK-free (no ``gi`` import) so it can be unit-tested without the GTK
typelib, matching the repo's pattern of extracting pure decision logic out of
GTK-bound code.
"""

from __future__ import annotations

from meeting_recorder.config.defaults import GEMINI_TRANSCRIPTION_SERVICES

# Models-tab sections in display order. The last section has no trailing separator.
MODEL_SECTION_ORDER = ["gemini", "whisper", "wcpp", "ollama", "gpu"]

__all__ = [
    "GEMINI_TRANSCRIPTION_SERVICES",
    "MODEL_SECTION_ORDER",
    "compute_gemini_row_visibility",
    "compute_section_visibility",
    "transcription_prompt_note",
]


def compute_section_visibility(ts: str, ss: str) -> dict[str, bool]:
    """Decide which Models-tab sections — and their trailing separators — are
    visible for the selected transcription (``ts``) and summarization (``ss``)
    services.

    Returns a dict keyed by section name
    (``gemini``/``whisper``/``wcpp``/``ollama``/``gpu``) plus ``<name>_sep`` for
    each non-final section. A separator is visible only when its section is
    visible AND some later section is also visible.
    """
    sections = {
        # Both Gemini transcription paths share the section's API key and
        # processing timeout, so either one keeps it visible.
        "gemini": ts in GEMINI_TRANSCRIPTION_SERVICES or ss == "gemini",
        "whisper": ts == "whisper",
        "wcpp": ts == "whisper_cpp",
        "ollama": ts == "ollama" or ss == "ollama",
    }
    # The GPU-acceleration section is relevant to either local STT engine.
    sections["gpu"] = sections["whisper"] or sections["wcpp"]

    result = dict(sections)
    for i, name in enumerate(MODEL_SECTION_ORDER[:-1]):
        later_visible = any(
            sections[MODEL_SECTION_ORDER[j]] for j in range(i + 1, len(MODEL_SECTION_ORDER))
        )
        result[f"{name}_sep"] = sections[name] and later_visible
    return result


def compute_gemini_row_visibility(ts: str, ss: str) -> dict[str, bool]:
    """Decide which rows inside the Gemini section apply to the selected services.

    The section holds three model pickers that are mutually exclusive in
    practice: the prompt-based transcription model, the dedicated
    speech-to-text model (which cannot summarize, so it has its own list), and
    the summarization model. Showing only the relevant ones keeps a model that
    the selected service would ignore from looking like it is in use.
    """
    return {
        "ts_model": ts == "gemini",
        "transcribe_model": ts == "gemini_transcribe",
        "transcribe_options": ts == "gemini_transcribe",
        "ss_model": ss == "gemini",
    }


def transcription_prompt_note(ts_service: str) -> str:
    """The explanatory note shown under the Prompts tab's transcription editor.

    The dedicated speech-to-text model accepts no prompt at all, so saying
    "applies to Gemini only" would be misleading while it is selected.
    """
    if ts_service == "gemini_transcribe":
        return (
            "Not used by the selected speech-to-text model, which takes no prompt. "
            "Switch the transcription service to Google Gemini to use a prompt."
        )
    return "Transcription prompts apply to Gemini only. Whisper does not use prompts."
