"""Tests for the pure window-close policy."""

from meeting_recorder.config.defaults import DEFAULT_CONFIG
from meeting_recorder.core.window_close import (
    CLOSE_EXIT,
    CLOSE_HIDE,
    resolve_close_action,
)


def test_default_config_hides():
    # The shipped default keeps the window resident for instant reopen.
    assert resolve_close_action(DEFAULT_CONFIG) == CLOSE_HIDE


def test_missing_key_hides():
    assert resolve_close_action({}) == CLOSE_HIDE


def test_low_memory_mode_exits():
    assert resolve_close_action({"low_memory_mode": True}) == CLOSE_EXIT


def test_low_memory_mode_off_hides():
    assert resolve_close_action({"low_memory_mode": False}) == CLOSE_HIDE


def test_truthy_non_bool_exits():
    # Config is JSON-loaded; tolerate a truthy value that isn't a strict bool.
    assert resolve_close_action({"low_memory_mode": 1}) == CLOSE_EXIT


def test_falsy_non_bool_hides():
    assert resolve_close_action({"low_memory_mode": 0}) == CLOSE_HIDE
