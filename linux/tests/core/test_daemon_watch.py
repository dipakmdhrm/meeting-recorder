"""Tests for the pure daemon-watch exit policy."""

from meeting_recorder.core.daemon_watch import should_exit_on_owner_change


def test_vanished_after_seen_exits():
    # The daemon was owning the name, then it disappeared: orphaned -> exit.
    assert should_exit_on_owner_change(daemon_seen=True, has_owner=False) is True


def test_appeared_does_not_exit():
    # Name gained/kept an owner: the daemon is present, stay.
    assert should_exit_on_owner_change(daemon_seen=True, has_owner=True) is False


def test_vanished_before_ever_seen_does_not_exit():
    # Defensive: a "no owner yet" before the daemon acquired the name at startup
    # must not kill the window prematurely.
    assert should_exit_on_owner_change(daemon_seen=False, has_owner=False) is False


def test_appeared_first_time_does_not_exit():
    assert should_exit_on_owner_change(daemon_seen=False, has_owner=True) is False
