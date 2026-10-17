"""
Tests for turning Gemini 3.5 Transcribe word annotations into the app's
``[HH:MM:SS] **Speaker:** text`` transcript format.

Covers response parsing (including the byte-index slicing that keeps the
model's own punctuation and spacing), the cross-chunk speaker matching that
makes a split recording read as one conversation, seam stitching, turn
grouping, and the best-effort speaker-name resolution.
"""

import pytest

from meeting_recorder.processing.diarization import (
    Turn,
    Word,
    apply_speaker_names,
    default_speaker_labels,
    format_timestamp,
    format_transcript,
    group_turns,
    interaction_text,
    map_speakers,
    merge_chunks,
    parse_offset,
    parse_speaker_name_mapping,
    render_words,
    shift_words,
    words_from_interaction,
    words_in_region,
)


def _word_info(text, start, end, speaker, offset=None, end_offset=None):
    """A word_info annotation carrying byte indices into the block text."""
    return {
        "type": "word_info",
        "text": text,
        "start_index": start,
        "end_index": end,
        "speaker": speaker,
        "start_offset": offset,
        "end_offset": end_offset,
    }


def _interaction(blocks, output_text=None, status="completed", **extra):
    content = [
        {"type": "text", "text": text, "annotations": annotations} for text, annotations in blocks
    ]
    return {
        "status": status,
        "steps": [{"type": "model_output", "content": content}],
        "output_text": output_text
        if output_text is not None
        else " ".join(text for text, _ in blocks),
        **extra,
    }


def _annotated(text, words, speaker_of=None, base=0.0):
    """Build (text, annotations) by locating each word in ``text`` in order."""
    annotations = []
    cursor = 0
    raw = text.encode("utf-8")
    for i, word in enumerate(words):
        start = raw.index(word.encode("utf-8"), cursor)
        end = start + len(word.encode("utf-8"))
        cursor = end
        annotations.append(
            _word_info(
                word,
                start,
                end,
                (speaker_of or {}).get(i, "spk_1"),
                offset=f"{base + i:.1f}s",
                end_offset=f"{base + i + 0.5:.1f}s",
            )
        )
    return text, annotations


class TestParseOffset:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("12.345s", 12.345),
            ("12.345", 12.345),
            ("0s", 0.0),
            (12.5, 12.5),
            (12, 12.0),
            ({"seconds": 12, "nanos": 500_000_000}, 12.5),
            ({"seconds": 3}, 3.0),
            (None, None),
            ("", None),
            ("abc", None),
            (True, None),
        ],
    )
    def test_accepts_the_duration_shapes_the_api_uses(self, value, expected):
        result = parse_offset(value)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected)


class TestWordsFromInteraction:
    def test_slices_words_by_byte_index_preserving_punctuation(self):
        text = "Hello, can everyone hear me?"
        block = _annotated(text, ["Hello,", "can", "everyone", "hear", "me?"])
        words = words_from_interaction(_interaction([block]))
        assert [w.text for w in words] == ["Hello,", "can", "everyone", "hear", "me?"]
        # Reassembling with the captured separators reproduces the original.
        rebuilt = "".join(
            (w.text if i == 0 else w.sep_before + w.text) for i, w in enumerate(words)
        )
        assert rebuilt == text

    def test_non_ascii_text_round_trips(self):
        # Byte indices, not character indices: a naive slice would corrupt this.
        text = "héllo wörld"
        block = _annotated(text, ["héllo", "wörld"])
        words = words_from_interaction(_interaction([block]))
        assert [w.text for w in words] == ["héllo", "wörld"]

    def test_reads_timing_and_speaker(self):
        block = _annotated(text="A B", words=["A", "B"], speaker_of={1: "spk_2"})
        words = words_from_interaction(_interaction([block]))
        assert words[0].start == pytest.approx(0.0)
        assert words[0].end == pytest.approx(0.5)
        assert words[0].speaker == "spk_1"
        assert words[1].speaker == "spk_2"

    def test_falls_back_to_the_annotation_text_without_indices(self):
        annotations = [
            {"type": "word_info", "text": "Hello", "start_offset": "0s"},
            {"type": "word_info", "text": "there", "start_offset": "1s"},
        ]
        words = words_from_interaction(_interaction([("Hello there", annotations)]))
        assert [w.text for w in words] == ["Hello", "there"]
        assert words[1].sep_before == " "

    def test_out_of_range_indices_fall_back_to_the_text_field(self):
        annotations = [{"type": "word_info", "text": "Hello", "start_index": 0, "end_index": 999}]
        words = words_from_interaction(_interaction([("Hi", annotations)]))
        assert [w.text for w in words] == ["Hello"]

    def test_multiple_blocks_are_concatenated_with_a_separator(self):
        first = _annotated("one two", ["one", "two"])
        second = _annotated("three four", ["three", "four"])
        words = words_from_interaction(_interaction([first, second]))
        assert [w.text for w in words] == ["one", "two", "three", "four"]
        assert words[2].sep_before == " "

    def test_non_word_annotations_are_skipped(self):
        annotations = [
            {"type": "speech_metadata", "speaker": "spk_1"},
            _word_info("Hi", 0, 2, "spk_1", "0s", "0.5s"),
            {"type": "url_citation", "uri": "http://example.com"},
        ]
        words = words_from_interaction(_interaction([("Hi", annotations)]))
        assert [w.text for w in words] == ["Hi"]

    def test_response_without_annotations_yields_no_words(self):
        assert words_from_interaction(_interaction([("Plain text", [])])) == []

    def test_empty_response_is_tolerated(self):
        assert words_from_interaction({}) == []
        assert words_from_interaction({"steps": None}) == []


class TestInteractionText:
    def test_prefers_output_text(self):
        assert interaction_text(_interaction([("a", [])], output_text="  hi  ")) == "hi"

    def test_falls_back_to_joining_blocks(self):
        payload = _interaction([("one", []), ("two", [])], output_text="")
        assert interaction_text(payload) == "one\ntwo"


class TestShiftWords:
    def test_offsets_move_to_absolute_time(self):
        words = [Word("a", 1.0, 1.5, "spk_1"), Word("b", 2.0, None, "spk_1")]
        shifted = shift_words(words, 1200.0)
        assert shifted[0].start == pytest.approx(1201.0)
        assert shifted[0].end == pytest.approx(1201.5)
        assert shifted[1].start == pytest.approx(1202.0)
        assert shifted[1].end is None

    def test_zero_shift_is_a_copy(self):
        words = [Word("a", 1.0, 1.5, "spk_1")]
        assert shift_words(words, 0.0) == words


class TestWordsInRegion:
    def test_selects_intersecting_words_only(self):
        words = [
            Word("before", 1.0, 2.0, "spk_1"),
            Word("inside", 11.0, 12.0, "spk_1"),
            Word("straddling", 19.5, 21.0, "spk_1"),
            Word("after", 30.0, 31.0, "spk_1"),
        ]
        found = words_in_region(words, (10.0, 20.0))
        assert [w.text for w in found] == ["inside", "straddling"]

    def test_untimed_words_are_excluded(self):
        assert words_in_region([Word("x", None, None, "spk_1")], (0.0, 10.0)) == []


class TestMapSpeakers:
    def test_matches_labels_by_overlapping_speech(self):
        previous = [
            Word("hello", 10.0, 11.0, "spk_1"),
            Word("there", 12.0, 13.0, "spk_2"),
        ]
        # The same audio, transcribed by the next chunk with its own numbering.
        following = [
            Word("hello", 10.0, 11.0, "spk_2"),
            Word("there", 12.0, 13.0, "spk_1"),
        ]
        assert map_speakers(previous, following) == {"spk_2": "spk_1", "spk_1": "spk_2"}

    def test_assignment_is_one_to_one(self):
        previous = [
            Word("a", 10.0, 11.0, "spk_1"),
            Word("b", 11.0, 12.0, "spk_2"),
        ]
        # Both of the new labels overlap spk_1, but only the stronger match
        # may claim it; the other must be left for a fresh id.
        following = [
            Word("a", 10.0, 11.0, "spk_9"),
            Word("b", 10.8, 11.2, "spk_8"),
        ]
        mapping = map_speakers(previous, following)
        assert mapping["spk_9"] == "spk_1"
        assert mapping.get("spk_8") != "spk_1"

    def test_weights_by_overlap_duration_not_word_count(self):
        previous = [Word("long", 10.0, 20.0, "spk_1"), Word("brief", 20.0, 20.2, "spk_2")]
        following = [Word("long", 10.0, 20.0, "spk_5")]
        assert map_speakers(previous, following) == {"spk_5": "spk_1"}

    def test_no_overlap_yields_no_mapping(self):
        previous = [Word("a", 0.0, 1.0, "spk_1")]
        following = [Word("b", 50.0, 51.0, "spk_1")]
        assert map_speakers(previous, following) == {}

    def test_unlabelled_words_are_ignored(self):
        previous = [Word("a", 10.0, 11.0, None)]
        following = [Word("a", 10.0, 11.0, "spk_1")]
        assert map_speakers(previous, following) == {}


class TestMergeChunks:
    def _two_chunks(self):
        # Chunk 0 covers 0-100 (overlap 80-100), chunk 1 covers 80-180.
        first = [
            Word("one", 10.0, 11.0, "spk_1"),
            Word("shared", 85.0, 86.0, "spk_2"),
            Word("clipped", 99.0, 100.0, "spk_2"),
        ]
        second = [
            Word("shared", 85.0, 86.0, "spk_7"),
            Word("later", 120.0, 121.0, "spk_7"),
        ]
        return first, second

    def test_single_chunk_passes_through(self):
        words = [Word("a", 0.0, 1.0, "spk_1")]
        assert merge_chunks([words], [], []) == words

    def test_seam_splits_the_timeline_without_duplicating(self):
        first, second = self._two_chunks()
        merged = merge_chunks([first, second], [90.0], [(80.0, 100.0)])
        # "shared" (85s) comes from the first chunk; "clipped" (99s) is past
        # the seam and so is dropped in favour of the second chunk's coverage.
        assert [w.text for w in merged] == ["one", "shared", "later"]

    def test_speaker_labels_are_translated_across_the_seam(self):
        first, second = self._two_chunks()
        merged = merge_chunks([first, second], [90.0], [(80.0, 100.0)])
        # spk_7 spoke the shared words, so it is the same person as spk_2.
        assert [w.speaker for w in merged] == ["spk_1", "spk_2", "spk_2"]

    def test_unmatched_label_becomes_a_new_speaker(self):
        first = [Word("hi", 10.0, 11.0, "spk_1"), Word("hmm", 85.0, 86.0, "spk_1")]
        # The newcomer never speaks inside the overlap, so it cannot be matched.
        second = [
            Word("hmm", 85.0, 86.0, "spk_1"),
            Word("newcomer", 120.0, 121.0, "spk_2"),
        ]
        merged = merge_chunks([first, second], [90.0], [(80.0, 100.0)])
        speakers = [w.speaker for w in merged]
        assert speakers[:2] == ["spk_1", "spk_1"]
        assert speakers[2] not in {"spk_1"}

    def test_three_chunks_keep_one_speaker_namespace(self):
        a = [Word("a", 10.0, 11.0, "spk_1"), Word("x", 85.0, 86.0, "spk_1")]
        b = [Word("x", 85.0, 86.0, "spk_4"), Word("y", 165.0, 166.0, "spk_4")]
        c = [Word("y", 165.0, 166.0, "spk_9"), Word("z", 200.0, 201.0, "spk_9")]
        merged = merge_chunks([a, b, c], [90.0, 170.0], [(80.0, 100.0), (160.0, 180.0)])
        assert [w.text for w in merged] == ["a", "x", "y", "z"]
        assert {w.speaker for w in merged} == {"spk_1"}

    def test_first_word_after_a_seam_gets_a_separator(self):
        first = [Word("one", 10.0, 11.0, "spk_1")]
        second = [Word("two", 120.0, 121.0, "spk_1", sep_before="")]
        merged = merge_chunks([first, second], [90.0], [(80.0, 100.0)])
        assert merged[1].sep_before == " "

    def test_missing_timestamps_fail_with_an_explanation(self):
        first = [Word("a", 1.0, 2.0, "spk_1")]
        second = [Word("b", None, None, "spk_1")]
        with pytest.raises(RuntimeError, match="without timestamps in part 2"):
            merge_chunks([first, second], [90.0], [(80.0, 100.0)])

    def test_seam_count_is_validated(self):
        words = [Word("a", 0.0, 1.0, "spk_1")]
        with pytest.raises(ValueError, match="seams and overlaps"):
            merge_chunks([words, words], [], [])

    def test_no_chunks(self):
        assert merge_chunks([], [], []) == []


class TestGroupTurns:
    def test_new_turn_on_speaker_change(self):
        words = [
            Word("Hello", 0.0, 0.5, "spk_1", ""),
            Word("there", 0.6, 1.0, "spk_1"),
            Word("Hi", 2.0, 2.4, "spk_2"),
        ]
        turns = group_turns(words)
        assert turns == [
            Turn("spk_1", 0.0, "Hello there"),
            Turn("spk_2", 2.0, "Hi"),
        ]

    def test_long_monologue_splits_at_a_pause(self):
        words = [Word("w0", 0.0, 0.5, "spk_1", "")]
        words += [Word(f"w{i}", float(i), i + 0.5, "spk_1") for i in range(1, 61)]
        # A 2 s pause after 60 s of one speaker.
        words.append(Word("w61", 63.0, 63.5, "spk_1"))
        turns = group_turns(words, max_turn_seconds=60.0, min_split_gap=0.6)
        assert len(turns) == 2
        assert turns[1].start == pytest.approx(63.0)

    def test_long_monologue_without_a_pause_stays_one_turn(self):
        words = [Word("w0", 0.0, 0.9, "spk_1", "")]
        words += [Word(f"w{i}", float(i), i + 0.9, "spk_1") for i in range(1, 120)]
        assert len(group_turns(words, max_turn_seconds=60.0, min_split_gap=0.6)) == 1

    def test_original_spacing_is_preserved(self):
        words = [
            Word("Hello", 0.0, 0.5, "spk_1", ""),
            Word("world", 0.6, 1.0, "spk_1", "  "),
        ]
        assert group_turns(words)[0].text == "Hello  world"

    def test_words_without_speakers_form_one_turn(self):
        words = [Word("a", 0.0, 1.0, None, ""), Word("b", 1.0, 2.0, None)]
        turns = group_turns(words)
        assert len(turns) == 1
        assert turns[0].speaker is None

    def test_empty_input(self):
        assert group_turns([]) == []


class TestDefaultSpeakerLabels:
    def test_numbered_by_first_appearance(self):
        words = [
            Word("a", 0.0, 1.0, "spk_3"),
            Word("b", 1.0, 2.0, "spk_1"),
            Word("c", 2.0, 3.0, "spk_3"),
        ]
        assert default_speaker_labels(words) == {"spk_3": "Person 1", "spk_1": "Person 2"}

    def test_unlabelled_words_are_skipped(self):
        assert default_speaker_labels([Word("a", 0.0, 1.0, None)]) == {}


class TestFormatting:
    def test_timestamp_format(self):
        assert format_timestamp(0) == "00:00:00"
        assert format_timestamp(5.9) == "00:00:05"
        assert format_timestamp(3725) == "01:02:05"
        assert format_timestamp(-1) == "00:00:00"

    def test_transcript_matches_the_prompt_based_format(self):
        turns = [Turn("spk_1", 5.0, "Hello."), Turn("spk_2", 9.0, "Hi.")]
        rendered = format_transcript(turns, {"spk_1": "Alice", "spk_2": "Bob"})
        assert rendered == "[00:00:05] **Alice:** Hello.\n[00:00:09] **Bob:** Hi."

    def test_unmapped_speaker_keeps_its_raw_label(self):
        assert format_transcript([Turn("spk_1", 0.0, "Hi")], {}) == ("[00:00:00] **spk_1:** Hi")

    def test_turn_without_a_speaker_or_timestamp(self):
        assert format_transcript([Turn(None, None, "Just text")]) == "Just text"

    def test_render_words_end_to_end(self):
        words = [
            Word("Hello", 5.0, 5.4, "spk_2", ""),
            Word("there", 5.5, 6.0, "spk_2"),
            Word("Hi", 9.0, 9.4, "spk_1"),
        ]
        assert render_words(words) == (
            "[00:00:05] **Person 1:** Hello there\n[00:00:09] **Person 2:** Hi"
        )


class TestSpeakerNameResolution:
    def test_plain_json(self):
        assert parse_speaker_name_mapping('{"Person 1": "Alice"}') == {"Person 1": "Alice"}

    def test_fenced_json_with_prose(self):
        raw = 'Sure! Here you go:\n```json\n{"Person 1": "Alice", "Person 2": "Bob"}\n```'
        assert parse_speaker_name_mapping(raw) == {"Person 1": "Alice", "Person 2": "Bob"}

    @pytest.mark.parametrize("value", ["unknown", "Unknown", "", "n/a", "none", "?"])
    def test_unknown_placeholders_are_dropped(self, value):
        assert parse_speaker_name_mapping(f'{{"Person 1": "{value}"}}') == {}

    def test_identity_mapping_is_dropped(self):
        assert parse_speaker_name_mapping('{"Person 1": "Person 1"}') == {}

    def test_implausible_values_are_dropped(self):
        long_name = "x" * 61
        assert parse_speaker_name_mapping(f'{{"Person 1": "{long_name}"}}') == {}
        assert parse_speaker_name_mapping('{"Person 1": 42}') == {}

    @pytest.mark.parametrize("raw", ["", "no json here", "[1, 2, 3]", "{not json}"])
    def test_unparseable_input_yields_nothing(self, raw):
        assert parse_speaker_name_mapping(raw) == {}

    def test_applies_names_to_the_transcript(self):
        transcript = "[00:00:05] **Person 1:** Hello.\n[00:00:09] **Person 2:** Hi."
        result = apply_speaker_names(transcript, {"Person 1": "Alice"})
        assert result == "[00:00:05] **Alice:** Hello.\n[00:00:09] **Person 2:** Hi."

    def test_double_digit_labels_are_not_confused_with_single_digit_ones(self):
        transcript = "**Person 1:** a\n**Person 10:** b"
        result = apply_speaker_names(transcript, {"Person 1": "Alice"})
        assert result == "**Alice:** a\n**Person 10:** b"

    def test_two_labels_may_resolve_to_the_same_person(self):
        transcript = "**Person 1:** a\n**Person 3:** b"
        result = apply_speaker_names(transcript, {"Person 1": "Alice", "Person 3": "Alice"})
        assert result == "**Alice:** a\n**Alice:** b"

    def test_empty_mapping_is_a_no_op(self):
        assert apply_speaker_names("**Person 1:** a", {}) == "**Person 1:** a"
