"""
The recording lifecycle state machine, extracted from the main window so the
legal transitions are explicit and unit-tested instead of implied by button
guard clauses scattered through the UI.

State → what the user can do:

- IDLE:       start recording (→ RECORDING)
- RECORDING:  pause (→ PAUSED), stop with countdown (→ COUNTDOWN),
              stop/cancel/cancel-save (→ IDLE)
- PAUSED:     resume (→ RECORDING), stop with countdown (→ COUNTDOWN),
              stop/cancel/cancel-save (→ IDLE)
- COUNTDOWN:  countdown expires or is cancelled (→ IDLE)

Self-transitions are legal everywhere: the UI re-enters the current state to
refresh status text (e.g. IDLE → IDLE with a new message).
"""

from __future__ import annotations

from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()
    COUNTDOWN = auto()


_ALLOWED: dict[State, frozenset[State]] = {
    State.IDLE: frozenset({State.RECORDING}),
    State.RECORDING: frozenset({State.PAUSED, State.COUNTDOWN, State.IDLE}),
    State.PAUSED: frozenset({State.RECORDING, State.COUNTDOWN, State.IDLE}),
    State.COUNTDOWN: frozenset({State.IDLE}),
}


def can_transition(current: State, new: State) -> bool:
    """True if moving from *current* to *new* is a legal lifecycle transition."""
    if current == new:
        return True
    return new in _ALLOWED[current]
