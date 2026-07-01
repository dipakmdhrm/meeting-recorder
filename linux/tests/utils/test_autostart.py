"""
Tests for the autostart .desktop management.

The entry is named after the GTK application id (not the app *name*) so the
session's launched identity matches the installed ``<APP_ID>.desktop`` and the
GNOME shell can tie the running window to the app icon. These tests guard the
app-id naming and the migration/cleanup of the legacy name.
"""

from pathlib import Path

import pytest

from meeting_recorder.config.defaults import APP_ID
from meeting_recorder.utils import autostart


@pytest.fixture
def autostart_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "AUTOSTART_DIR", tmp_path)
    return tmp_path


def test_filename_is_app_id(autostart_dir):
    assert autostart.DESKTOP_FILENAME == f"{APP_ID}.desktop"
    assert autostart.LEGACY_DESKTOP_FILENAME == "meeting-recorder.desktop"


def test_enable_writes_app_id_entry(autostart_dir):
    autostart.update_autostart(True)
    entry = autostart_dir / f"{APP_ID}.desktop"
    assert entry.exists()
    body = entry.read_text()
    assert f"StartupWMClass={APP_ID}" in body
    assert "Icon=meeting-recorder" in body


def test_enable_migrates_legacy_entry(autostart_dir):
    # A pre-rename entry should be removed and replaced by the app-id one.
    legacy = autostart_dir / autostart.LEGACY_DESKTOP_FILENAME
    legacy.write_text("[Desktop Entry]\n")
    autostart.update_autostart(True)
    assert not legacy.exists()
    assert (autostart_dir / f"{APP_ID}.desktop").exists()


def test_disable_removes_both_names(autostart_dir):
    (autostart_dir / f"{APP_ID}.desktop").write_text("x")
    (autostart_dir / autostart.LEGACY_DESKTOP_FILENAME).write_text("x")
    autostart.update_autostart(False)
    assert not (autostart_dir / f"{APP_ID}.desktop").exists()
    assert not (autostart_dir / autostart.LEGACY_DESKTOP_FILENAME).exists()


def test_is_enabled_detects_either_name(autostart_dir):
    assert autostart.is_autostart_enabled() is False
    legacy = autostart_dir / autostart.LEGACY_DESKTOP_FILENAME
    legacy.write_text("x")
    assert autostart.is_autostart_enabled() is True
    legacy.unlink()
    (autostart_dir / f"{APP_ID}.desktop").write_text("x")
    assert autostart.is_autostart_enabled() is True


def test_enable_is_idempotent(autostart_dir):
    autostart.update_autostart(True)
    entry = autostart_dir / f"{APP_ID}.desktop"
    first = entry.read_text()
    entry.write_text(first + "\n# user edit\n")
    autostart.update_autostart(True)  # must not overwrite an existing entry
    assert entry.read_text().endswith("# user edit\n")
