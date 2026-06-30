"""
Pure visibility policy for the Settings → Models tab.

Kept GTK-free (no ``gi`` import) so it can be unit-tested without the GTK
typelib, matching the repo's pattern of extracting pure decision logic out of
GTK-bound code.
"""

from __future__ import annotations

# Models-tab sections in display order. The last section has no trailing separator.
MODEL_SECTION_ORDER = ["gemini", "whisper", "wcpp", "ollama", "gpu"]


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
        "gemini":  ts == "gemini" or ss == "gemini",
        "whisper": ts == "whisper",
        "wcpp":    ts == "whisper_cpp",
        "ollama":  ts == "ollama" or ss == "ollama",
    }
    # The GPU-acceleration section is relevant to either local STT engine.
    sections["gpu"] = sections["whisper"] or sections["wcpp"]

    result = dict(sections)
    for i, name in enumerate(MODEL_SECTION_ORDER[:-1]):
        later_visible = any(
            sections[MODEL_SECTION_ORDER[j]]
            for j in range(i + 1, len(MODEL_SECTION_ORDER))
        )
        result[f"{name}_sep"] = sections[name] and later_visible
    return result
