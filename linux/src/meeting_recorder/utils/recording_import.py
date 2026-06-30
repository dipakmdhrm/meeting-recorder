"""
Pure decision logic for importing an existing audio recording.

Extracted from ``MainWindow.on_use_existing_clicked`` so the in-tree-reuse vs.
copy-to-new-directory branch can be unit-tested without GTK. (Mirrors the
repo's pattern of pulling pure policy out of GTK-bound code, e.g.
``RecordingStopDecision`` on Android.)
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_existing_recording_target(
    selected: Path, output_folder: Path
) -> tuple[bool, tuple[Path, Path, Path] | None]:
    """Decide how to import an audio file the user picked.

    If ``selected`` already lives inside a meeting subdirectory of
    ``output_folder`` (i.e. a previous recording), it is reused in place and its
    sibling ``transcript.md`` / ``notes.md`` paths are returned. Otherwise the
    file is external and the caller should create a fresh meeting directory and
    copy it in.

    Returns ``(reuse_in_place, paths)`` where ``paths`` is
    ``(audio, transcript, notes)`` when ``reuse_in_place`` is True, else None.
    """
    selected = selected.resolve()
    output_folder = output_folder.resolve()
    inside_subdir = (
        selected.parent != output_folder
        and str(selected).startswith(str(output_folder) + os.sep)
    )
    if inside_subdir:
        session_dir = selected.parent
        return True, (
            selected,
            session_dir / "transcript.md",
            session_dir / "notes.md",
        )
    return False, None
