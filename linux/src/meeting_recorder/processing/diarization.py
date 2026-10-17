"""
Pure helpers that turn Gemini 3.5 Transcribe word annotations into the
``[HH:MM:SS] **Speaker:** text`` transcript format the rest of the app expects.

The dedicated transcription model does not accept a prompt, so it cannot be
asked to format its output or to guess speakers' real names the way the
general-purpose Gemini path is. It returns the plain transcript plus per-word
annotations carrying ``speaker`` ("spk_1", "spk_2", ...) and start/end offsets.
Everything needed to rebuild our transcript format from that - including
tying chunk N+1's speaker labels back to chunk N's across a split - lives here,
GTK-free and network-free so it can be unit-tested directly.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

# A speaker turn is broken up after this long so timestamps stay useful during
# a monologue, but only at a real pause.
MAX_TURN_SECONDS = 60.0
MIN_TURN_SPLIT_GAP = 0.6

# Fallback span for a word the model timed but did not give an end offset to.
_NOMINAL_WORD_SECONDS = 0.25

# Values a name-resolution pass may use to say "I could not tell".
_UNKNOWN_NAMES = {"", "unknown", "unclear", "n/a", "na", "none", "null", "?"}

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Word:
    """One transcribed word with its timing, speaker, and original spacing.

    ``sep_before`` is the exact text that separated this word from the previous
    one in the model's output, so a turn can be reassembled byte-faithfully
    instead of space-joined (which would corrupt languages that do not put
    spaces between words).
    """

    text: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    sep_before: str = " "


@dataclass(frozen=True)
class Turn:
    """A run of consecutive words by one speaker."""

    speaker: str | None
    start: float | None
    text: str


# ----------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------


def _field(obj: Any, name: str) -> Any:
    """Read ``name`` off an SDK model or a plain dict."""
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def parse_offset(value: Any) -> float | None:
    """Parse a protobuf Duration as seconds.

    Accepts the JSON string form ("12.345s"), a bare number or numeric string,
    and the expanded ``{"seconds": 12, "nanos": 345000000}`` object.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, Mapping):
        seconds = value.get("seconds") or 0
        nanos = value.get("nanos") or 0
        try:
            return float(seconds) + float(nanos) / 1_000_000_000
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().rstrip("sS")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _iter_text_blocks(interaction: Any) -> Iterator[tuple[str, list[Any]]]:
    """Yield (text, annotations) for every text content block in the response."""
    for step in _field(interaction, "steps") or []:
        for content in _field(step, "content") or []:
            if _field(content, "type") != "text":
                continue
            text = _field(content, "text")
            if not text:
                continue
            yield str(text), list(_field(content, "annotations") or [])


def _words_from_block(text: str, annotations: Sequence[Any]) -> list[Word]:
    """Build words from one text block's ``word_info`` annotations.

    Each annotation carries byte indices into ``text``; using them preserves the
    model's own punctuation and spacing exactly. Annotations without usable
    indices fall back to their own ``text`` field, space separated.
    """
    raw = text.encode("utf-8")
    words: list[Word] = []
    previous_end: int | None = None

    for annotation in annotations:
        if _field(annotation, "type") != "word_info":
            continue

        start_index = _field(annotation, "start_index")
        end_index = _field(annotation, "end_index")
        word_text: str | None = None
        separator = " "

        usable = (
            isinstance(start_index, int)
            and not isinstance(start_index, bool)
            and isinstance(end_index, int)
            and not isinstance(end_index, bool)
            and 0 <= start_index <= end_index <= len(raw)
        )
        if usable:
            assert isinstance(start_index, int) and isinstance(end_index, int)
            word_text = raw[start_index:end_index].decode("utf-8", "replace")
            if previous_end is None:
                separator = "" if not words else " "
            elif previous_end <= start_index:
                separator = raw[previous_end:start_index].decode("utf-8", "replace")
            previous_end = end_index

        if not word_text:
            word_text = str(_field(annotation, "text") or "")
            previous_end = None

        if not word_text:
            continue

        words.append(
            Word(
                text=word_text,
                start=parse_offset(_field(annotation, "start_offset")),
                end=parse_offset(_field(annotation, "end_offset")),
                speaker=_field(annotation, "speaker") or None,
                sep_before=separator,
            )
        )

    return words


def words_from_interaction(interaction: Any) -> list[Word]:
    """Collect every annotated word in an Interaction response, in order."""
    words: list[Word] = []
    for text, block_annotations in _iter_text_blocks(interaction):
        block_words = _words_from_block(text, block_annotations)
        if block_words and words:
            # Keep blocks from running together when the second one's first
            # word had no separator of its own.
            block_words[0] = replace(block_words[0], sep_before=" ")
        words.extend(block_words)
    return words


def interaction_text(interaction: Any) -> str:
    """The plain transcript text, for the no-annotation fallback path."""
    direct = _field(interaction, "output_text")
    if direct:
        return str(direct).strip()
    return "\n".join(text for text, _ in _iter_text_blocks(interaction)).strip()


# ----------------------------------------------------------------------
# Chunk stitching
# ----------------------------------------------------------------------


def shift_words(words: Sequence[Word], delta: float) -> list[Word]:
    """Move every timestamp by ``delta`` seconds (chunk start -> absolute time)."""
    if not delta:
        return list(words)
    return [
        replace(
            word,
            start=None if word.start is None else word.start + delta,
            end=None if word.end is None else word.end + delta,
        )
        for word in words
    ]


def _span(word: Word) -> tuple[float, float] | None:
    if word.start is None:
        return None
    end = word.end if word.end is not None else word.start + _NOMINAL_WORD_SECONDS
    return (word.start, max(end, word.start))


def words_in_region(words: Sequence[Word], region: tuple[float, float]) -> list[Word]:
    """Words whose span intersects ``region``."""
    start, end = region
    found = []
    for word in words:
        span = _span(word)
        if span is None:
            continue
        if span[1] > start and span[0] < end:
            found.append(word)
    return found


def map_speakers(
    previous: Sequence[Word],
    following: Sequence[Word],
) -> dict[str, str]:
    """Match ``following``'s speaker labels to ``previous``'s over shared audio.

    Both sequences must be restricted to the same absolute time window, which
    both chunks transcribed. Votes are weighted by how much time two words
    overlap, then assigned greedily one-to-one, so the most confident pairing
    wins and no two labels collapse onto the same speaker.
    """
    votes: dict[tuple[str, str], float] = {}
    for new_word in following:
        if not new_word.speaker:
            continue
        new_span = _span(new_word)
        if new_span is None:
            continue
        for old_word in previous:
            if not old_word.speaker:
                continue
            old_span = _span(old_word)
            if old_span is None:
                continue
            shared = min(new_span[1], old_span[1]) - max(new_span[0], old_span[0])
            if shared <= 0:
                continue
            key = (new_word.speaker, old_word.speaker)
            votes[key] = votes.get(key, 0.0) + shared

    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    for (new_label, old_label), weight in sorted(
        votes.items(), key=lambda item: (-item[1], item[0])
    ):
        if weight <= 0 or new_label in mapping or old_label in claimed:
            continue
        mapping[new_label] = old_label
        claimed.add(old_label)
    return mapping


def _next_label(known: set[str]) -> str:
    index = 1
    while f"spk_{index}" in known:
        index += 1
    return f"spk_{index}"


def merge_chunks(
    chunk_words: Sequence[Sequence[Word]],
    seams: Sequence[float],
    overlaps: Sequence[tuple[float, float]],
) -> list[Word]:
    """Stitch per-chunk words (already shifted to absolute time) into one list.

    At each seam the words before it come from the earlier chunk and the words
    after it from the later one. Because both chunks cover the seam by
    construction, nothing is dropped or duplicated. Speaker labels from the
    later chunk are first translated into the running global namespace using the
    overlap; a label that cannot be matched there becomes a new speaker.
    """
    if not chunk_words:
        return []
    if len(seams) != len(chunk_words) - 1 or len(overlaps) != len(chunk_words) - 1:
        raise ValueError(
            f"expected {len(chunk_words) - 1} seams and overlaps for "
            f"{len(chunk_words)} chunks, got {len(seams)} and {len(overlaps)}"
        )

    if len(chunk_words) == 1:
        return list(chunk_words[0])

    for index, words in enumerate(chunk_words):
        if any(word.start is None for word in words):
            raise RuntimeError(
                "Cannot stitch chunked transcription: Gemini returned words "
                f"without timestamps in part {index + 1}. Word timestamps are "
                "required to merge a recording longer than the single-request "
                "limit."
            )

    merged = list(chunk_words[0])
    known = {word.speaker for word in merged if word.speaker}

    for i in range(1, len(chunk_words)):
        seam = seams[i - 1]
        region = overlaps[i - 1]
        incoming = chunk_words[i]

        mapping = map_speakers(
            words_in_region(merged, region),
            words_in_region(incoming, region),
        )
        for word in incoming:
            if word.speaker and word.speaker not in mapping:
                fresh = _next_label(known | set(mapping.values()))
                mapping[word.speaker] = fresh
        known |= set(mapping.values())

        relabelled = [
            word if not word.speaker else replace(word, speaker=mapping[word.speaker])
            for word in incoming
        ]

        kept_tail = [word for word in relabelled if word.start is not None and word.start >= seam]
        if kept_tail:
            kept_tail[0] = replace(kept_tail[0], sep_before=" ")
        merged = [
            word for word in merged if word.start is not None and word.start < seam
        ] + kept_tail

    return merged


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def group_turns(
    words: Sequence[Word],
    *,
    max_turn_seconds: float = MAX_TURN_SECONDS,
    min_split_gap: float = MIN_TURN_SPLIT_GAP,
) -> list[Turn]:
    """Group words into speaker turns, splitting long monologues at a pause."""
    turns: list[Turn] = []
    current: list[Word] = []
    turn_start: float | None = None

    def flush() -> None:
        if not current:
            return
        text = "".join(
            (word.text if index == 0 else word.sep_before + word.text)
            for index, word in enumerate(current)
        ).strip()
        if text:
            turns.append(Turn(speaker=current[0].speaker, start=turn_start, text=text))

    for word in words:
        gap = 0.0
        if current and word.start is not None:
            previous = current[-1]
            reference = previous.end if previous.end is not None else previous.start
            if reference is not None:
                gap = word.start - reference

        too_long = (
            turn_start is not None
            and word.start is not None
            and word.start - turn_start >= max_turn_seconds
            and gap >= min_split_gap
        )

        if current and (word.speaker != current[0].speaker or too_long):
            flush()
            current = []
            turn_start = None

        if not current:
            turn_start = word.start
        current.append(word)

    flush()
    return turns


def default_speaker_labels(words: Sequence[Word]) -> dict[str, str]:
    """Map raw speaker ids to "Person N" in order of first appearance.

    Numbering follows who speaks first rather than the model's own label
    numbers, which are not necessarily ordered and drift across chunks.
    """
    labels: dict[str, str] = {}
    for word in words:
        if word.speaker and word.speaker not in labels:
            labels[word.speaker] = f"Person {len(labels) + 1}"
    return labels


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def format_transcript(
    turns: Sequence[Turn],
    speaker_names: Mapping[str, str] | None = None,
) -> str:
    """Render turns as ``[HH:MM:SS] **Speaker:** text``, one line per turn.

    Matches the format the Gemini prompt path produces, so transcript.md stays
    interchangeable between engines and with the Android app.
    """
    names = speaker_names or {}
    lines = []
    for turn in turns:
        prefix = "" if turn.start is None else f"[{format_timestamp(turn.start)}] "
        label = names.get(turn.speaker, turn.speaker) if turn.speaker else None
        speaker = f"**{label}:** " if label else ""
        lines.append(f"{prefix}{speaker}{turn.text}")
    return "\n".join(lines)


def render_words(words: Sequence[Word]) -> str:
    """Full word list -> transcript, with default Person N speaker labels."""
    return format_transcript(group_turns(words), default_speaker_labels(words))


# ----------------------------------------------------------------------
# Speaker name resolution
# ----------------------------------------------------------------------


def parse_speaker_name_mapping(raw: str) -> dict[str, str]:
    """Parse a label -> real name JSON object out of an LLM reply.

    Tolerant by design: the reply may be fenced, prefixed with prose, or name
    only some speakers. Anything unparseable yields an empty mapping and the
    caller keeps the default labels.
    """
    if not raw:
        return {}
    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, TypeError):
        logger.debug("Speaker name mapping was not valid JSON")
        return {}
    if not isinstance(parsed, dict):
        return {}

    mapping: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        label = key.strip()
        name = value.strip().strip('"').strip("'").strip()
        if not label or name.lower() in _UNKNOWN_NAMES:
            continue
        if len(name) > 60 or "\n" in name:
            continue
        if name == label:
            continue
        mapping[label] = name
    return mapping


def apply_speaker_names(transcript: str, mapping: Mapping[str, str]) -> str:
    """Rewrite ``**Person 1:**`` style labels to resolved real names."""
    result = transcript
    for label, name in mapping.items():
        result = result.replace(f"**{label}:**", f"**{name}:**")
    return result
