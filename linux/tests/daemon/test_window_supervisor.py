"""Tests for the window-child spawn/present supervision decisions."""

from meeting_recorder.daemon.window_supervisor import WindowSupervisor


def _sup():
    calls = {"spawn": 0, "present": 0}
    sup = WindowSupervisor(
        spawn_fn=lambda: calls.__setitem__("spawn", calls["spawn"] + 1),
        present_fn=lambda: calls.__setitem__("present", calls["present"] + 1),
    )
    return sup, calls


def test_first_open_spawns():
    sup, calls = _sup()
    assert sup.is_alive is False
    sup.open()
    assert calls == {"spawn": 1, "present": 0}
    assert sup.is_alive is True


def test_second_open_presents_not_spawns():
    sup, calls = _sup()
    sup.open()
    sup.open()
    assert calls == {"spawn": 1, "present": 1}


def test_exit_allows_respawn():
    sup, calls = _sup()
    sup.open()
    sup.on_child_exit()
    assert sup.is_alive is False
    sup.open()
    assert calls == {"spawn": 2, "present": 0}


def test_exit_when_not_alive_is_safe():
    sup, calls = _sup()
    sup.on_child_exit()  # no window ever opened
    assert sup.is_alive is False
    assert calls == {"spawn": 0, "present": 0}
