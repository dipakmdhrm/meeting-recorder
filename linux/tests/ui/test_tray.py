"""
Tests for the pure tray backend-selection policy.

``_choose_tray_backend`` decides which tray backend the app uses, in priority
order: an *embedded* Gtk.StatusIcon (the only backend that gives a separate
left-click action) wins, else AppIndicator, else pystray, else nothing. The GTK
click wiring and the embed probe itself are GTK-bound and stay outside unit
scope (see the test-coverage boundaries in CLAUDE.md); this guards the policy.

Importing ``tray`` is CI-safe: its module-level gi/AppIndicator import is wrapped
in try/except and no GTK widgets are constructed at import time.
"""

import pytest

from meeting_recorder.ui.tray import _choose_tray_backend


class TestChooseTrayBackend:
    def test_embedded_statusicon_wins(self):
        # Even when every backend is available, an embedded Gtk.StatusIcon is
        # preferred because it is the only one with a custom left-click action.
        assert _choose_tray_backend(True, True, True) == "statusicon"
        assert _choose_tray_backend(True, False, False) == "statusicon"

    def test_falls_back_to_indicator_when_not_embedded(self):
        assert _choose_tray_backend(False, True, True) == "indicator"
        assert _choose_tray_backend(False, True, False) == "indicator"

    def test_falls_back_to_pystray_when_no_indicator(self):
        assert _choose_tray_backend(False, False, True) == "pystray"

    def test_none_when_nothing_available(self):
        assert _choose_tray_backend(False, False, False) is None

    @pytest.mark.parametrize(
        "embedded,indicator,pystray,expected",
        [
            (True, True, True, "statusicon"),
            (True, False, True, "statusicon"),
            (False, True, True, "indicator"),
            (False, False, True, "pystray"),
            (False, False, False, None),
        ],
    )
    def test_priority_matrix(self, embedded, indicator, pystray, expected):
        assert _choose_tray_backend(embedded, indicator, pystray) == expected
