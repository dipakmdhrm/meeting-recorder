"""
Pure chunk-planning policy for long-audio transcription.

Gemini 3.5 Transcribe caps a single request at 1 hour of audio, and at 30
minutes once speaker diarization or word timestamps are requested - which this
app always does. Anything longer has to be split, transcribed in pieces, and
stitched back together.

The geometry here is deliberately arithmetic rather than silence-driven:
``plan_chunks`` guarantees every chunk is under the cap and that consecutive
chunks overlap, and ``choose_seam`` then picks the *switch-over point* inside
that overlap, preferring a silence. A word clipped at a chunk's edge is
therefore always discarded in favour of the neighbouring chunk, which captured
it intact.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# 28 minutes, not 30: a safety margin against the API measuring duration
# slightly differently than ffprobe does.
CHUNK_MAX_SECONDS = 1680.0

# Both chunks transcribe this much shared audio. It has to be long enough to
# usually contain more than one speaker, because the overlap is what lets
# ``diarization.map_speakers`` tie chunk N+1's speaker labels to chunk N's.
CHUNK_OVERLAP_SECONDS = 45.0

# A seam is only placed in a silence at least this long.
SEAM_MIN_SILENCE_SECONDS = 0.4


@dataclass(frozen=True)
class Chunk:
    """One sub-request slice of the recording, in absolute seconds."""

    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_chunks(
    duration: float,
    *,
    max_chunk: float = CHUNK_MAX_SECONDS,
    overlap: float = CHUNK_OVERLAP_SECONDS,
) -> list[Chunk]:
    """Split ``duration`` seconds into evenly sized overlapping chunks.

    Returns a single chunk covering the whole recording when it already fits.
    Otherwise the chunk count is the minimum that keeps every chunk under
    ``max_chunk``, and the length is then shared out evenly - so an hour
    becomes 3 x 20.5 min rather than 28 + 28 + 4, which keeps every request
    well clear of the cap and makes a retry cost the same wherever it happens.
    """
    if duration <= 0:
        raise ValueError(f"duration must be positive, got {duration!r}")
    if overlap < 0:
        raise ValueError(f"overlap must not be negative, got {overlap!r}")
    if max_chunk <= overlap:
        raise ValueError(f"max_chunk ({max_chunk}) must exceed overlap ({overlap})")

    if duration <= max_chunk:
        return [Chunk(index=0, start=0.0, end=duration)]

    count = math.ceil((duration - overlap) / (max_chunk - overlap))
    # Each chunk advances by this much; its length is advance + overlap, so the
    # last chunk ends exactly at `duration`.
    advance = (duration - overlap) / count
    chunks = []
    for i in range(count):
        start = i * advance
        end = duration if i == count - 1 else start + advance + overlap
        chunks.append(Chunk(index=i, start=start, end=end))
    return chunks


def overlap_region(prev: Chunk, nxt: Chunk) -> tuple[float, float]:
    """Return the (start, end) window both chunks transcribed."""
    return (max(prev.start, nxt.start), min(prev.end, nxt.end))


def choose_seam(
    prev: Chunk,
    nxt: Chunk,
    silences: Sequence[tuple[float, float]] = (),
    *,
    min_silence: float = SEAM_MIN_SILENCE_SECONDS,
) -> float:
    """Pick the absolute time at which to stop using ``prev`` and start ``nxt``.

    Defaults to the middle of the overlap - the point furthest from both
    chunks' clipped edges - and moves to the midpoint of the nearest long
    enough silence when one is available, so the switch does not land inside a
    word.
    """
    region_start, region_end = overlap_region(prev, nxt)
    if region_end <= region_start:
        # Chunks do not actually overlap (overlap=0); switch at the boundary.
        return max(region_start, region_end)

    midpoint = (region_start + region_end) / 2.0

    best: float | None = None
    best_distance = float("inf")
    for silence_start, silence_end in silences:
        clipped_start = max(silence_start, region_start)
        clipped_end = min(silence_end, region_end)
        if clipped_end - clipped_start < min_silence:
            continue
        candidate = (clipped_start + clipped_end) / 2.0
        distance = abs(candidate - midpoint)
        if distance < best_distance:
            best = candidate
            best_distance = distance

    return best if best is not None else midpoint


def plan_seams(
    chunks: Sequence[Chunk],
    silences: Sequence[tuple[float, float]] = (),
) -> list[float]:
    """Seam times between consecutive chunks (one fewer than the chunk count)."""
    return [choose_seam(chunks[i], chunks[i + 1], silences) for i in range(len(chunks) - 1)]
