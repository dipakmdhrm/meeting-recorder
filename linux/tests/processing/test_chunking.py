"""
Tests for the pure long-audio chunk planner.

Gemini 3.5 Transcribe rejects more than 30 minutes of audio once diarization or
word timestamps are requested, so these invariants are what keep a long
recording from failing outright: every chunk under the cap, consecutive chunks
overlapping, and full coverage with no gaps.
"""

import pytest

from meeting_recorder.processing.chunking import (
    CHUNK_MAX_SECONDS,
    CHUNK_OVERLAP_SECONDS,
    Chunk,
    choose_seam,
    overlap_region,
    plan_chunks,
    plan_seams,
)


def _assert_valid_plan(chunks, duration, max_chunk, overlap):
    assert chunks[0].start == pytest.approx(0.0)
    assert chunks[-1].end == pytest.approx(duration)
    for chunk in chunks:
        assert chunk.duration <= max_chunk + 1e-6, f"{chunk} exceeds the cap"
        assert chunk.duration > 0
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        # No gap, and enough shared audio to map speakers across the seam.
        assert later.start < earlier.end
        assert earlier.end - later.start == pytest.approx(overlap)
    assert [c.index for c in chunks] == list(range(len(chunks)))


class TestPlanChunks:
    def test_short_recording_is_a_single_chunk(self):
        chunks = plan_chunks(600.0)
        assert chunks == [Chunk(index=0, start=0.0, end=600.0)]

    def test_exactly_at_the_cap_is_not_split(self):
        chunks = plan_chunks(CHUNK_MAX_SECONDS)
        assert len(chunks) == 1

    def test_one_second_over_the_cap_splits_in_two(self):
        chunks = plan_chunks(CHUNK_MAX_SECONDS + 1)
        assert len(chunks) == 2
        _assert_valid_plan(chunks, CHUNK_MAX_SECONDS + 1, CHUNK_MAX_SECONDS, CHUNK_OVERLAP_SECONDS)

    def test_one_hour_splits_into_three_even_chunks(self):
        chunks = plan_chunks(3600.0)
        assert len(chunks) == 3
        # Evenly shared out rather than greedily filled: 3 x 20.5 min, not
        # 28 + 28 + 4. Every request stays well clear of the cap.
        assert [c.duration for c in chunks] == pytest.approx([1230.0] * 3)
        assert [c.start for c in chunks] == pytest.approx([0.0, 1185.0, 2370.0])
        _assert_valid_plan(chunks, 3600.0, CHUNK_MAX_SECONDS, CHUNK_OVERLAP_SECONDS)

    @pytest.mark.parametrize(
        "duration,expected_count",
        [
            (2100.0, 2),  # 35 min
            (3600.0, 3),  # 1 h
            (5400.0, 4),  # 1.5 h
            (10800.0, 7),  # 3 h
            (36000.0, 22),  # 10 h
        ],
    )
    def test_plan_is_valid_across_durations(self, duration, expected_count):
        chunks = plan_chunks(duration)
        assert len(chunks) == expected_count
        _assert_valid_plan(chunks, duration, CHUNK_MAX_SECONDS, CHUNK_OVERLAP_SECONDS)

    def test_chunk_count_is_the_minimum_that_fits(self):
        duration = 10800.0
        chunks = plan_chunks(duration)
        # One chunk fewer would push at least one chunk over the cap.
        fewer = len(chunks) - 1
        assert (duration + (fewer - 1) * CHUNK_OVERLAP_SECONDS) / fewer > CHUNK_MAX_SECONDS

    def test_custom_geometry_is_honoured(self):
        chunks = plan_chunks(100.0, max_chunk=40.0, overlap=10.0)
        _assert_valid_plan(chunks, 100.0, 40.0, 10.0)
        assert len(chunks) == 3

    def test_zero_overlap_chunks_abut_exactly(self):
        chunks = plan_chunks(100.0, max_chunk=40.0, overlap=0.0)
        assert [(c.start, c.end) for c in chunks] == [
            pytest.approx((0.0, 100.0 / 3)),
            pytest.approx((100.0 / 3, 200.0 / 3)),
            pytest.approx((200.0 / 3, 100.0)),
        ]

    @pytest.mark.parametrize("duration", [0.0, -1.0])
    def test_non_positive_duration_rejected(self, duration):
        with pytest.raises(ValueError, match="duration"):
            plan_chunks(duration)

    def test_negative_overlap_rejected(self):
        with pytest.raises(ValueError, match="overlap"):
            plan_chunks(100.0, overlap=-1.0)

    def test_overlap_not_smaller_than_chunk_rejected(self):
        with pytest.raises(ValueError, match="must exceed overlap"):
            plan_chunks(1000.0, max_chunk=45.0, overlap=45.0)


class TestOverlapRegion:
    def test_shared_window(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        assert overlap_region(a, b) == (80.0, 100.0)


class TestChooseSeam:
    def test_defaults_to_the_overlap_midpoint(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        assert choose_seam(a, b, []) == pytest.approx(90.0)

    def test_snaps_to_a_silence_inside_the_overlap(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        assert choose_seam(a, b, [(84.0, 86.0)]) == pytest.approx(85.0)

    def test_prefers_the_silence_nearest_the_midpoint(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        silences = [(81.0, 82.0), (89.0, 90.0), (98.0, 99.0)]
        assert choose_seam(a, b, silences) == pytest.approx(89.5)

    def test_ignores_silences_outside_the_overlap(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        # Both are real silences, but neither is in 80-100.
        assert choose_seam(a, b, [(10.0, 20.0), (120.0, 130.0)]) == pytest.approx(90.0)

    def test_ignores_silences_shorter_than_the_minimum(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        assert choose_seam(a, b, [(84.0, 84.1)]) == pytest.approx(90.0)

    def test_clips_a_silence_that_straddles_the_overlap_edge(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 80.0, 180.0)
        # 70-84 is clipped to 80-84, whose midpoint is 82.
        assert choose_seam(a, b, [(70.0, 84.0)]) == pytest.approx(82.0)

    def test_no_overlap_falls_back_to_the_boundary(self):
        a = Chunk(0, 0.0, 100.0)
        b = Chunk(1, 100.0, 200.0)
        assert choose_seam(a, b, [(10.0, 20.0)]) == pytest.approx(100.0)


class TestPlanSeams:
    def test_one_seam_per_boundary(self):
        chunks = plan_chunks(3600.0)
        seams = plan_seams(chunks)
        assert len(seams) == len(chunks) - 1
        for i, seam in enumerate(seams):
            start, end = overlap_region(chunks[i], chunks[i + 1])
            assert start <= seam <= end

    def test_single_chunk_has_no_seams(self):
        assert plan_seams(plan_chunks(600.0)) == []
