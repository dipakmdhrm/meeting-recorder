"""Tests for the pure stderr-tail formatter shared by the child launchers."""

from meeting_recorder.daemon.child_io import stderr_tail


def test_empty_is_empty():
    assert stderr_tail([]) == ""


def test_joins_lines_with_spaces():
    assert stderr_tail(["a", "b", "c"]) == "a b c"


def test_caps_to_limit_keeping_the_tail():
    lines = ["x" * 100, "y" * 100, "important-tail"]
    out = stderr_tail(lines, limit=20)
    assert len(out) == 20
    assert out.endswith("important-tail")
