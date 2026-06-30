"""
Pure tray icon/menu policy.

Kept GTK-free and gi-free (no ``gi`` import) so it can be unit-tested without
PyGObject — the CI test environment installs only pytest. The D-Bus
StatusNotifierItem wiring that consumes these lives in ``tray.py``.
"""

from __future__ import annotations


def icon_for_state(recording_state: str, jobs: list) -> str:
    """Return the themed icon name for the current state.

    Priority: recording > paused > jobs processing > idle.
    """
    if recording_state == "recording":
        return "media-record"
    if recording_state == "paused":
        return "media-playback-pause"
    if jobs:
        return "system-run"
    return "audio-input-microphone"


def build_menu_model(recording_state: str, jobs: list) -> list[dict]:
    """Build the tray menu as a flat list of item descriptors.

    Pure — no GTK or window references — so it can be unit-tested. Each item is
    one of:
      - ``{"type": "action", "label": str, "action": str, "enabled": bool}``
      - ``{"type": "action", "label": str, "action": "cancel_job",
           "job_index": int, "enabled": True}``
      - ``{"type": "label",  "label": str, "enabled": False}``  (inert header)
      - ``{"type": "separator"}``

    Mirrors the GTK3 menu the app used previously.
    """
    items: list[dict] = []

    def action(label: str, act: str) -> None:
        items.append({"type": "action", "label": label, "action": act, "enabled": True})

    # Recording controls reflect the current recording state.
    if recording_state == "idle":
        action("Record (Headphones)", "record_headphones")
        action("Record (Speaker)", "record_speaker")
        action("Use Existing Recording", "use_existing")
    elif recording_state == "recording":
        action("Pause Recording", "pause")
        action("Stop Recording", "stop")
        action("Cancel (save recording)", "cancel_save")
        action("Cancel", "cancel")
    elif recording_state == "paused":
        action("Resume Recording", "resume")
        action("Stop Recording", "stop")
        action("Cancel (save recording)", "cancel_save")
        action("Cancel", "cancel")

    # Background-jobs section (only when jobs are active).
    if jobs:
        items.append({"type": "separator"})
        items.append({
            "type": "label",
            "label": f"Processing ({len(jobs)} active)",
            "enabled": False,
        })
        for i, (label, _cancel_fn) in enumerate(jobs):
            items.append({
                "type": "action",
                "label": f"  Cancel: {label}",
                "action": "cancel_job",
                "job_index": i,
                "enabled": True,
            })

    # Footer.
    items.append({"type": "separator"})
    action("Show Window", "show")
    action("Quit", "quit")
    return items
