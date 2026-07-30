"""Tests for the pure run-mode resolver."""

from meeting_recorder.core.run_mode import CLIENT, DAEMON, PROCESS, WINDOW, resolve_run_mode


def test_no_flags_is_client():
    assert resolve_run_mode(["meeting-recorder"]) == CLIENT


def test_empty_argv_is_client():
    # Defensive: even a completely empty argv resolves to the user-facing role.
    assert resolve_run_mode([]) == CLIENT


def test_daemon_flag():
    assert resolve_run_mode(["meeting-recorder", "--daemon"]) == DAEMON


def test_window_flag():
    assert resolve_run_mode(["meeting-recorder", "--window"]) == WINDOW


def test_process_flag():
    assert resolve_run_mode(["meeting-recorder", "--process", "a.mp3", "t.md", "n.md"]) == PROCESS


def test_daemon_wins_over_window():
    # A daemon must never also try to be a window if both flags appear.
    assert resolve_run_mode(["meeting-recorder", "--window", "--daemon"]) == DAEMON


def test_unrelated_args_ignored():
    assert resolve_run_mode(["meeting-recorder", "--verbose"]) == CLIENT
