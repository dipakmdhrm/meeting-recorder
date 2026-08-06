"""Tests for the pure version resolver in core.app_info."""

from __future__ import annotations

from meeting_recorder.core import app_info


def _runner(responses: dict[str, str | None]):
    """Build a run_fn keyed by the command name (argv[0])."""

    def run(argv: list[str]) -> str | None:
        return responses.get(argv[0])

    return run


def test_resolves_dpkg_version():
    run = _runner({"dpkg-query": "1.1.35"})
    assert app_info.resolve_version(run) == "1.1.35"


def test_resolves_rpm_when_dpkg_missing():
    run = _runner({"dpkg-query": None, "rpm": "1.1.35"})
    assert app_info.resolve_version(run) == "1.1.35"


def test_resolves_pacman_name_and_version():
    run = _runner({"dpkg-query": None, "rpm": None, "pacman": "meeting-recorder 1.1.35"})
    assert app_info.resolve_version(run) == "1.1.35"


def test_returns_none_when_no_package_manager_knows_it():
    run = _runner({})  # every query returns None (source checkout)
    assert app_info.resolve_version(run) is None


def test_blank_dpkg_output_is_treated_as_unknown():
    run = _runner({"dpkg-query": "  \n", "rpm": None, "pacman": None})
    assert app_info.resolve_version(run) is None


def test_first_matching_package_manager_wins():
    # dpkg answers first, so rpm/pacman are never consulted.
    run = _runner({"dpkg-query": "2.0.0", "rpm": "9.9.9", "pacman": "meeting-recorder 9.9.9"})
    assert app_info.resolve_version(run) == "2.0.0"


def test_pacman_malformed_output_is_unknown():
    run = _runner({"dpkg-query": None, "rpm": None, "pacman": "meeting-recorder"})
    assert app_info.resolve_version(run) is None
