"""Scans the output folder for meetings, reads/writes metadata, handles deletion."""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .filename import sanitize_title

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"}

# Matches flat folder names like "2026-03-01_14-30" or "2026-03-01_14-30_Some_Title"
_FOLDER_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$")


@dataclass
class Meeting:
    path: Path
    time_label: str
    date: datetime
    title: str | None
    has_notes: bool
    has_transcript: bool
    has_audio: bool
    duration_seconds: int | None  # audio duration in seconds, None if unknown


def scan_meetings(output_folder: str) -> list[Meeting]:
    """Walk the output folder and return all meetings, newest first.

    Expects a flat structure: <output_folder>/<YYYY-MM-DD_HH-MM[_title]>/
    """
    import os
    root = Path(os.path.expanduser(output_folder))
    if not root.is_dir():
        return []

    meetings: list[Meeting] = []
    for meeting_dir in _iter_dirs(root):
        match = _FOLDER_PATTERN.match(meeting_dir.name)
        if not match:
            continue
        # Skip active recordings / processing
        if (meeting_dir / ".recording").exists():
            continue

        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hour, minute = int(match.group(4)), int(match.group(5))
        try:
            dt = datetime(year, month, day, hour, minute)
        except ValueError:
            continue

        meta = read_metadata(meeting_dir)
        audio_files = [
            f for f in meeting_dir.iterdir()
            if f.is_file() and f.suffix in _AUDIO_EXTENSIONS
        ]
        duration = meta.get("duration_seconds")
        if duration is None and audio_files:
            duration = _probe_audio_duration(audio_files[0])
            if duration is not None:
                write_metadata(meeting_dir, {"duration_seconds": duration})
        meetings.append(Meeting(
            path=meeting_dir,
            time_label=meeting_dir.name,
            date=dt,
            title=meta.get("title"),
            has_notes=(meeting_dir / "notes.md").exists(),
            has_transcript=(meeting_dir / "transcript.md").exists(),
            has_audio=bool(audio_files),
            duration_seconds=int(duration) if duration is not None else None,
        ))

    meetings.sort(key=lambda m: m.date, reverse=True)
    return meetings


def _iter_dirs(parent: Path):
    """Yield subdirectories of parent, ignoring errors."""
    try:
        return [p for p in parent.iterdir() if p.is_dir()]
    except OSError:
        return []


def _probe_audio_duration(audio_path: Path) -> int | None:
    """Get audio duration in seconds using ffprobe. Returns None on failure."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return None


def read_metadata(meeting_path: Path) -> dict:
    """Read meeting.json from the meeting directory. Returns {} if missing/malformed."""
    meta_file = meeting_path / "meeting.json"
    if not meta_file.exists():
        return {}
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_metadata(meeting_path: Path, metadata: dict) -> None:
    """Write/merge metadata into meeting.json."""
    existing = read_metadata(meeting_path)
    existing.update(metadata)
    meta_file = meeting_path / "meeting.json"
    meta_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def delete_meetings(
    meetings: list[Meeting],
    output_folder: str = "~/meetings",
) -> tuple[list[Meeting], list[tuple[Meeting, str]]]:
    """Delete meeting directories. Returns (succeeded, failures)."""
    succeeded: list[Meeting] = []
    failures: list[tuple[Meeting, str]] = []

    for meeting in meetings:
        try:
            shutil.rmtree(meeting.path)
            succeeded.append(meeting)
        except Exception as exc:
            failures.append((meeting, str(exc)))

    return succeeded, failures


def rename_meeting_path(meeting_dir: Path, new_title: str) -> Path:
    """Rename a meeting directory to {YYYY-MM-DD_HH-MM}_{sanitized_title}. Returns new path."""
    match = _FOLDER_PATTERN.match(meeting_dir.name)
    if not match:
        raise ValueError(f"Cannot parse date-time from folder name: {meeting_dir.name}")

    date_time_part = (
        f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        f"_{match.group(4)}-{match.group(5)}"
    )
    safe_title = sanitize_title(new_title)
    new_name = f"{date_time_part}_{safe_title}"
    new_path = meeting_dir.parent / new_name

    # Handle collision
    if new_path.exists() and new_path != meeting_dir:
        counter = 2
        while True:
            candidate = meeting_dir.parent / f"{new_name}_{counter}"
            if not candidate.exists():
                new_path = candidate
                break
            counter += 1

    meeting_dir.rename(new_path)
    return new_path


def rename_meeting_dir(meeting: Meeting, new_title: str) -> Path:
    """Rename meeting folder to {HH-MM}_{sanitized_title}. Returns new path."""
    return rename_meeting_path(meeting.path, new_title)
