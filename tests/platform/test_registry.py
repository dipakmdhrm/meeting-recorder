import pytest
from meeting_recorder.platform.registry import PlatformRegistry


def test_get_audio_backend_unknown_returns_none():
    registry = PlatformRegistry()
    assert registry.get_audio_backend("nonexistent") is None


def test_get_screen_recorder_unknown_returns_none():
    registry = PlatformRegistry()
    assert registry.get_screen_recorder("nonexistent") is None


def test_available_audio_backends_returns_list():
    registry = PlatformRegistry()
    backends = registry.available_audio_backends()
    assert isinstance(backends, list)


def test_available_screen_recorders_returns_list():
    registry = PlatformRegistry()
    recorders = registry.available_screen_recorders()
    assert isinstance(recorders, list)
