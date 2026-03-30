"""
Handles the generation of structured output paths for recording sessions. It includes logic for sanitizing user-provided titles and organizing files into a readable date-based directory hierarchy.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path


def sanitize_title(title: str) -> str:
    """Remove characters unsafe for filenames, collapse spaces."""
    # Replace path separators and null bytes
    sanitized = re.sub(r'[/\\:\*\?"<>\|]', "", title)
    # Collapse whitespace to single underscore
    sanitized = re.sub(r"\s+", "_", sanitized.strip())
    # 50 chars keeps the full path well under the 255-byte filename limit even
    # when prepended with a timestamp (e.g. "14-30_").
    return sanitized[:50]



def output_paths(
    output_folder: str,
    title: str | None = None,
    dt: datetime | None = None,
) -> tuple[Path, Path, Path]:
    """Return (audio_path, transcript_path, notes_path) for a recording session.

    Structure: <output_folder>/<YYYY>/<Month>/<DD>/<HH-MM[_title]>/
    e.g. ~/meetings/2026/March/01/14-30_Standup/

    The year/month/day hierarchy prevents a flat directory from accumulating hundreds
    of entries. Month names (not numbers) make browsing more readable.
    """
    if dt is None:
        dt = datetime.now()
    time_part = dt.strftime("%H-%M")
    if title and title.strip():
        folder_name = f"{time_part}_{sanitize_title(title)}"
    else:
        folder_name = time_part
    session_dir = (
        Path(os.path.expanduser(output_folder))
        / dt.strftime("%Y")
        / dt.strftime("%B")
        / dt.strftime("%d")
        / folder_name
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    return (
        session_dir / "recording.mp3",
        session_dir / "transcript.md",
        session_dir / "notes.md",
    )
