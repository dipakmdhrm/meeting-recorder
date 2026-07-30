"""Tests for autostart entry migration (pre-split window entry -> daemon entry)."""

from meeting_recorder.utils.autostart import (
    migrate_autostart_exec,
    needs_autostart_migration,
)

LEGACY = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Meeting Recorder
Exec=/usr/bin/meeting-recorder
Icon=meeting-recorder
Terminal=false
"""

MIGRATED = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Meeting Recorder
Exec=/usr/bin/meeting-recorder --daemon
Icon=meeting-recorder
Terminal=false
"""


def test_legacy_entry_needs_migration():
    assert needs_autostart_migration(LEGACY) is True


def test_already_migrated_entry_is_noop():
    assert needs_autostart_migration(MIGRATED) is False
    # Idempotent: migrating an already-daemon entry changes nothing.
    assert migrate_autostart_exec(MIGRATED) == MIGRATED


def test_migration_appends_daemon_flag():
    result = migrate_autostart_exec(LEGACY)
    assert "Exec=/usr/bin/meeting-recorder --daemon" in result
    assert needs_autostart_migration(result) is False


def test_migration_preserves_other_lines():
    result = migrate_autostart_exec(LEGACY)
    for line in LEGACY.splitlines():
        if line.startswith("Exec="):
            continue
        assert line in result.splitlines()


def test_migration_preserves_foreign_keys():
    # Keys this app does not own must survive a rewrite untouched.
    contents = LEGACY + "X-GNOME-Autostart-enabled=true\n"
    result = migrate_autostart_exec(contents)
    assert "X-GNOME-Autostart-enabled=true" in result


def test_no_exec_line_needs_no_migration():
    contents = "[Desktop Entry]\nType=Application\nName=Meeting Recorder\n"
    assert needs_autostart_migration(contents) is False
    assert migrate_autostart_exec(contents) == contents


def test_exec_with_extra_args_still_migrated():
    contents = "[Desktop Entry]\nExec=/usr/bin/meeting-recorder --verbose\n"
    assert needs_autostart_migration(contents) is True
    result = migrate_autostart_exec(contents)
    assert "Exec=/usr/bin/meeting-recorder --verbose --daemon" in result


def test_custom_exec_path_migrated():
    contents = "[Desktop Entry]\nExec=/home/u/.local/bin/meeting-recorder\n"
    result = migrate_autostart_exec(contents)
    assert "Exec=/home/u/.local/bin/meeting-recorder --daemon" in result
