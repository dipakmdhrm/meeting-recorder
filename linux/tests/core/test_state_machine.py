"""Tests for the recording lifecycle transition table."""

import pytest

from meeting_recorder.core.state_machine import State, can_transition


class TestLegalTransitions:
    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (State.IDLE, State.RECORDING),
            (State.RECORDING, State.PAUSED),
            (State.RECORDING, State.COUNTDOWN),
            (State.RECORDING, State.IDLE),
            (State.PAUSED, State.RECORDING),
            (State.PAUSED, State.COUNTDOWN),
            (State.PAUSED, State.IDLE),
            (State.COUNTDOWN, State.IDLE),
        ],
    )
    def test_allowed(self, current, new):
        assert can_transition(current, new) is True

    @pytest.mark.parametrize("state", list(State))
    def test_self_transition_always_allowed(self, state):
        # The UI re-enters the current state to refresh status text.
        assert can_transition(state, state) is True


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (State.IDLE, State.PAUSED),  # can't pause without recording
            (State.IDLE, State.COUNTDOWN),  # countdown only follows a stop
            (State.COUNTDOWN, State.RECORDING),  # must return to idle first
            (State.COUNTDOWN, State.PAUSED),
        ],
    )
    def test_rejected(self, current, new):
        assert can_transition(current, new) is False


class TestExhaustiveness:
    def test_every_state_pair_has_a_defined_answer(self):
        # The table must never raise for any pair — the UI depends on it.
        for a in State:
            for b in State:
                assert can_transition(a, b) in (True, False)
