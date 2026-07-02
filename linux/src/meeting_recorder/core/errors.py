"""
Pure error-presentation policy: how an error message reaches the user.

The app used to surface errors inconsistently (alert vs toast vs log-only,
chosen ad-hoc per call site). The policy is now one function:

- ``"dialog"`` — actionable configuration problems the user must fix
  (missing API key, missing tools/devices, install problems). A modal alert
  makes sure they see it.
- ``"toast"`` — everything else (transient/runtime failures). A dismissable
  toast informs without interrupting.

Errors are always logged regardless of presentation.
"""

from __future__ import annotations

from typing import Literal

Presentation = Literal["dialog", "toast"]

# Substrings that mark an error as an actionable configuration problem.
_ACTIONABLE_MARKERS = (
    "api key",
    "not configured",
    "not installed",
    "not found. please install",
    "open settings",
    "audio device error",
    "permission",
)


def error_presentation(message: str) -> Presentation:
    """Classify *message* as needing a modal dialog or a transient toast."""
    lowered = message.lower()
    if any(marker in lowered for marker in _ACTIONABLE_MARKERS):
        return "dialog"
    return "toast"
