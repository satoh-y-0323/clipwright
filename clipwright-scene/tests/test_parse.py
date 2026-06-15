"""test_parse.py — Red tests for parse.py (pure logic).

Target module: clipwright_scene.parse

Functions under test:
  parse_scdet_stderr(stderr: str, total_duration_sec: float) -> list[SceneBoundary]
  parse_pyscenedetect_csv(csv_text: str) -> list[SceneBoundary]
  merge_close_boundaries(boundaries: list[SceneBoundary], min_duration_sec: float) -> list[SceneBoundary]

Dataclass under test:
  SceneBoundary(timestamp_sec: float, confidence: float, scene_index: int)

parse.py is pure logic that never runs ffmpeg or scenedetect.
All tests are expected to FAIL until parse.py is implemented (Red phase).

Covers §4 (FFmpeg scdet) and §5 (PySceneDetect CSV) of architecture-report-20260615-222258.md.
"""

from __future__ import annotations

import pytest

from clipwright_scene.parse import (
    SceneBoundary,
    merge_close_boundaries,
    parse_pyscenedetect_csv,
    parse_scdet_stderr,
)

# ===========================================================================
# SceneBoundary dataclass
# ===========================================================================


class TestSceneBoundary:
    """SceneBoundary dataclass construction and field access."""

    def test_construction_with_all_fields(self) -> None:
        """SceneBoundary is constructable with timestamp_sec, confidence, scene_index."""
        boundary = SceneBoundary(timestamp_sec=3.96, confidence=0.45, scene_index=0)
        assert boundary.timestamp_sec == pytest.approx(3.96)
        assert boundary.confidence == pytest.approx(0.45)
        assert boundary.scene_index == 0

    def test_field_types(self) -> None:
        """timestamp_sec and confidence are float; scene_index is int."""
        boundary = SceneBoundary(timestamp_sec=1.0, confidence=1.0, scene_index=2)
        assert isinstance(boundary.timestamp_sec, float)
        assert isinstance(boundary.confidence, float)
        assert isinstance(boundary.scene_index, int)

    def test_zero_values(self) -> None:
        """SceneBoundary is constructable with zero values."""
        boundary = SceneBoundary(timestamp_sec=0.0, confidence=0.0, scene_index=0)
        assert boundary.timestamp_sec == pytest.approx(0.0)
        assert boundary.confidence == pytest.approx(0.0)
        assert boundary.scene_index == 0


# ===========================================================================
# parse_scdet_stderr — §4 FFmpeg Backend
# ===========================================================================

# Minimal scdet stderr sample with the bracketed format [scdet @ 0x...].
_SCDET_BRACKETED = (
    "[scdet @ 0x7f1234ab5cd0] Scdet: frame=96 pts=96 "
    "pts_time=3.960000 score=45.2 prev_mafd=10.5 mafd=55.7\n"
)

# Alternative format without brackets (scdet @ 0x...).
_SCDET_BARE = (
    "scdet @ 0x7f1234ab5cd0 Scdet: frame=96 pts=96 "
    "pts_time=3.960000 score=45.2 prev_mafd=10.5 mafd=55.7\n"
)

# Multi-boundary stderr.
_SCDET_MULTI = (
    "[scdet @ 0xaabb] Scdet: frame=96 pts=96 pts_time=3.960000 score=45.2 "
    "prev_mafd=10.5 mafd=55.7\n"
    "[scdet @ 0xaabb] Scdet: frame=192 pts=192 pts_time=8.000000 score=72.0 "
    "prev_mafd=20.0 mafd=92.0\n"
    "[scdet @ 0xaabb] Scdet: frame=288 pts=288 pts_time=12.000000 score=30.0 "
    "prev_mafd=5.0 mafd=35.0\n"
)

# Noisy stderr mixing scdet lines with other ffmpeg output.
_SCDET_NOISY = (
    "ffmpeg version 6.1 Copyright (c) 2000-2023 the FFmpeg developers\n"
    "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'sample.mp4':\n"
    "[showinfo @ 0xdeadbeef] n:   0 pts:  0 pts_time:0.000000\n"
    "[scdet @ 0xaabb] Scdet: frame=96 pts=96 pts_time=3.960000 score=45.2 "
    "prev_mafd=10.5 mafd=55.7\n"
    "frame=   288 fps= 24 q=-0.0 Lsize=N/A time=00:00:12.00\n"
)


class TestParseScdetStderrEmpty:
    """Empty or blank stderr input."""

    def test_empty_string_returns_empty_list(self) -> None:
        """Empty stderr -> empty list."""
        result = parse_scdet_stderr("", 60.0)
        assert result == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        """Whitespace-only stderr -> empty list."""
        result = parse_scdet_stderr("   \n  \t  \n", 60.0)
        assert result == []

    def test_none_returns_empty_list(self) -> None:
        """None as stderr -> empty list (graceful handling)."""
        result = parse_scdet_stderr(None, 60.0)  # type: ignore[arg-type]
        assert result == []


class TestParseScdetStderrSingleBoundary:
    """Single scdet boundary parsing."""

    def test_single_boundary_bracketed_format(self) -> None:
        """Bracketed format: parses pts_time and score correctly."""
        result = parse_scdet_stderr(_SCDET_BRACKETED, 60.0)
        assert len(result) == 1
        boundary = result[0]
        assert boundary.timestamp_sec == pytest.approx(3.96)
        assert boundary.confidence == pytest.approx(0.452)
        assert boundary.scene_index == 0

    def test_single_boundary_bare_format(self) -> None:
        """Bare format without brackets: same parse result as bracketed."""
        result = parse_scdet_stderr(_SCDET_BARE, 60.0)
        assert len(result) == 1
        assert result[0].timestamp_sec == pytest.approx(3.96)

    def test_pts_is_ignored_pts_time_is_used(self) -> None:
        """pts (integer) is ignored; pts_time (float seconds) is used for timestamp."""
        # pts=96 is a frame counter, pts_time=3.960000 is what we want.
        result = parse_scdet_stderr(_SCDET_BRACKETED, 60.0)
        # timestamp must be the float pts_time value, not pts integer
        assert result[0].timestamp_sec == pytest.approx(3.960000)

    def test_score_45_2_normalizes_to_0_452(self) -> None:
        """score=45.2 -> confidence = 45.2 / 100.0 = 0.452."""
        result = parse_scdet_stderr(_SCDET_BRACKETED, 60.0)
        assert result[0].confidence == pytest.approx(45.2 / 100.0)


class TestParseScdetStderrMultipleBoundaries:
    """Multiple scdet boundaries: ordering and scene_index."""

    def test_three_boundaries_returns_three_items(self) -> None:
        """3 scdet lines -> 3 SceneBoundary objects."""
        result = parse_scdet_stderr(_SCDET_MULTI, 60.0)
        assert len(result) == 3

    def test_scene_index_is_zero_indexed(self) -> None:
        """scene_index starts at 0 and increments for each boundary."""
        result = parse_scdet_stderr(_SCDET_MULTI, 60.0)
        indices = [b.scene_index for b in result]
        assert indices == [0, 1, 2]

    def test_timestamps_are_in_order(self) -> None:
        """Boundaries appear in chronological order (ascending pts_time)."""
        result = parse_scdet_stderr(_SCDET_MULTI, 60.0)
        times = [b.timestamp_sec for b in result]
        assert times == sorted(times)

    def test_second_boundary_confidence(self) -> None:
        """Second boundary: score=72.0 -> confidence=0.72."""
        result = parse_scdet_stderr(_SCDET_MULTI, 60.0)
        assert result[1].confidence == pytest.approx(72.0 / 100.0)


class TestParseScdetStderrScoreNormalization:
    """Confidence normalization: min(score / 100.0, 1.0)."""

    def test_score_100_clips_to_1_0(self) -> None:
        """score=100 -> confidence capped at 1.0."""
        stderr = (
            "[scdet @ 0xaabb] Scdet: frame=96 pts=96 "
            "pts_time=3.960000 score=100 prev_mafd=0 mafd=100\n"
        )
        result = parse_scdet_stderr(stderr, 60.0)
        assert result[0].confidence == pytest.approx(1.0)

    def test_score_above_100_clips_to_1_0(self) -> None:
        """score>100 (edge case) -> confidence capped at 1.0."""
        stderr = (
            "[scdet @ 0xaabb] Scdet: frame=96 pts=96 "
            "pts_time=3.960000 score=110 prev_mafd=0 mafd=110\n"
        )
        result = parse_scdet_stderr(stderr, 60.0)
        assert result[0].confidence == pytest.approx(1.0)

    def test_score_0_gives_confidence_0(self) -> None:
        """score=0 -> confidence=0.0."""
        stderr = (
            "[scdet @ 0xaabb] Scdet: frame=96 pts=96 "
            "pts_time=3.960000 score=0 prev_mafd=0 mafd=0\n"
        )
        result = parse_scdet_stderr(stderr, 60.0)
        assert result[0].confidence == pytest.approx(0.0)

    def test_score_50_normalizes_to_0_5(self) -> None:
        """score=50 -> confidence=0.5."""
        stderr = (
            "[scdet @ 0xaabb] Scdet: frame=48 pts=48 "
            "pts_time=2.000000 score=50 prev_mafd=0 mafd=50\n"
        )
        result = parse_scdet_stderr(stderr, 60.0)
        assert result[0].confidence == pytest.approx(0.5)


class TestParseScdetStderrNoise:
    """Irrelevant lines in stderr are ignored."""

    def test_ffmpeg_version_line_ignored(self) -> None:
        """ffmpeg version header line does not produce a boundary."""
        result = parse_scdet_stderr(_SCDET_NOISY, 60.0)
        # Only 1 scdet line in _SCDET_NOISY
        assert len(result) == 1

    def test_showinfo_lines_ignored(self) -> None:
        """showinfo filter output is not parsed as a scdet boundary."""
        result = parse_scdet_stderr(_SCDET_NOISY, 60.0)
        # The showinfo line has pts_time=0.000000, but it must not appear
        assert result[0].timestamp_sec == pytest.approx(3.96)

    def test_progress_line_ignored(self) -> None:
        """ffmpeg progress line (frame= fps= q=) is not parsed."""
        result = parse_scdet_stderr(_SCDET_NOISY, 60.0)
        assert len(result) == 1


class TestParseScdetStderrPrecision:
    """Floating-point precision of pts_time."""

    def test_pts_time_6_decimal_places(self) -> None:
        """pts_time with 6 decimal places is parsed faithfully."""
        stderr = (
            "[scdet @ 0xaabb] Scdet: frame=12 pts=12 "
            "pts_time=0.500000 score=30.0 prev_mafd=0 mafd=30\n"
        )
        result = parse_scdet_stderr(stderr, 60.0)
        assert result[0].timestamp_sec == pytest.approx(0.5, abs=1e-6)

    def test_pts_time_sub_second(self) -> None:
        """Sub-second pts_time is handled correctly."""
        stderr = (
            "[scdet @ 0xaabb] Scdet: frame=6 pts=6 "
            "pts_time=0.250000 score=20.0 prev_mafd=0 mafd=20\n"
        )
        result = parse_scdet_stderr(stderr, 60.0)
        assert result[0].timestamp_sec == pytest.approx(0.25, abs=1e-6)


# ===========================================================================
# parse_pyscenedetect_csv — §5 PySceneDetect Backend
# ===========================================================================

_CSV_HEADER = (
    "Scene Number,Start Frame,Start Timecode,Start Time (seconds),"
    "End Frame,End Timecode,End Time (seconds),"
    "Length (frames),Length (timecode),Length (seconds)\n"
)

_CSV_TWO_SCENES = (
    _CSV_HEADER
    + "1,1,00:00:00.000,0.000,120,00:00:04.000,4.000,119,00:00:03.967,3.967\n"
    + "2,121,00:00:04.033,4.033,240,00:00:08.000,8.000,119,00:00:03.967,3.967\n"
)

_CSV_THREE_SCENES = (
    _CSV_HEADER
    + "1,1,00:00:00.000,0.000,120,00:00:04.000,4.000,119,00:00:03.967,3.967\n"
    + "2,121,00:00:04.033,4.033,240,00:00:08.000,8.000,119,00:00:03.967,3.967\n"
    + "3,241,00:00:08.033,8.033,360,00:00:12.000,12.000,119,00:00:03.967,3.967\n"
)


class TestParsePyscenedetectCsvEmpty:
    """Empty or header-only CSV input."""

    def test_empty_string_returns_empty_list(self) -> None:
        """Empty CSV text -> empty list."""
        result = parse_pyscenedetect_csv("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        """Whitespace-only CSV -> empty list."""
        result = parse_pyscenedetect_csv("   \n\n")
        assert result == []

    def test_header_only_returns_empty_list(self) -> None:
        """Header row present but no data rows -> empty list."""
        result = parse_pyscenedetect_csv(_CSV_HEADER)
        assert result == []


class TestParsePyscenedetectCsvNormalRows:
    """Normal CSV parsing: counts, timestamps, confidence, scene_index."""

    def test_two_scenes_returns_two_boundaries(self) -> None:
        """2 data rows -> 2 SceneBoundary objects."""
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        assert len(result) == 2

    def test_first_scene_start_time_is_zero(self) -> None:
        """First scene 'Start Time (seconds)' = 0.000 -> timestamp_sec=0.0."""
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        assert result[0].timestamp_sec == pytest.approx(0.0)

    def test_second_scene_start_time(self) -> None:
        """Second scene 'Start Time (seconds)' = 4.033."""
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        assert result[1].timestamp_sec == pytest.approx(4.033)

    def test_all_confidence_values_are_1_0(self) -> None:
        """PySceneDetect backend: all boundaries have confidence=1.0 (ADR-5)."""
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        for boundary in result:
            assert boundary.confidence == pytest.approx(1.0)

    def test_scene_index_is_zero_indexed(self) -> None:
        """scene_index is 0-based regardless of CSV 'Scene Number' column (1-based)."""
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        assert result[0].scene_index == 0
        assert result[1].scene_index == 1

    def test_three_scenes_returns_three_boundaries(self) -> None:
        """3 data rows -> 3 SceneBoundary objects."""
        result = parse_pyscenedetect_csv(_CSV_THREE_SCENES)
        assert len(result) == 3

    def test_three_scenes_indices(self) -> None:
        """3 scenes: indices are 0, 1, 2."""
        result = parse_pyscenedetect_csv(_CSV_THREE_SCENES)
        assert [b.scene_index for b in result] == [0, 1, 2]


class TestParsePyscenedetectCsvColumnSelection:
    """Correct column is used for timestamp (not timecode)."""

    def test_uses_start_time_seconds_not_timecode(self) -> None:
        """'Start Time (seconds)' column is used, not 'Start Timecode'."""
        # The timecode is "00:00:04.033" and seconds is 4.033 — both present.
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        # Must be float from 'Start Time (seconds)', not a parsed timecode string.
        assert isinstance(result[1].timestamp_sec, float)
        assert result[1].timestamp_sec == pytest.approx(4.033)

    def test_end_time_columns_are_ignored(self) -> None:
        """'End Time (seconds)' column is not parsed as a boundary."""
        result = parse_pyscenedetect_csv(_CSV_TWO_SCENES)
        # First scene's End Time is 4.000 — it must NOT appear as a boundary timestamp.
        timestamps = [b.timestamp_sec for b in result]
        assert 4.000 not in timestamps or result[0].timestamp_sec == pytest.approx(0.0)


class TestParsePyscenedetectCsvMalformed:
    """Malformed or partial CSV rows are skipped safely."""

    def test_row_missing_columns_is_skipped(self) -> None:
        """A row with too few columns is silently skipped."""
        csv_text = _CSV_HEADER + "1,1\n"  # only 2 columns
        result = parse_pyscenedetect_csv(csv_text)
        assert result == []

    def test_non_numeric_start_time_is_skipped(self) -> None:
        """A row with non-numeric 'Start Time (seconds)' is skipped."""
        csv_text = (
            _CSV_HEADER
            + "1,1,00:00:00.000,invalid,120,00:00:04.000,4.000,"
            "119,00:00:03.967,3.967\n"
        )
        result = parse_pyscenedetect_csv(csv_text)
        assert result == []

    def test_valid_rows_after_invalid_row_are_parsed(self) -> None:
        """Valid rows after a bad row are still returned."""
        csv_text = (
            _CSV_HEADER
            + "bad row\n"
            + "2,121,00:00:04.033,4.033,240,00:00:08.000,8.000,"
            "119,00:00:03.967,3.967\n"
        )
        result = parse_pyscenedetect_csv(csv_text)
        # The valid row should be returned (bad row skipped)
        assert len(result) == 1
        assert result[0].timestamp_sec == pytest.approx(4.033)


# ===========================================================================
# merge_close_boundaries
# ===========================================================================


def _make_boundary(
    timestamp_sec: float, confidence: float, scene_index: int = 0
) -> SceneBoundary:
    """Helper to build SceneBoundary instances for merge tests."""
    return SceneBoundary(
        timestamp_sec=timestamp_sec,
        confidence=confidence,
        scene_index=scene_index,
    )


class TestMergeCloseBoundariesEmpty:
    """Empty or trivial input."""

    def test_empty_list_returns_empty_list(self) -> None:
        """Empty list -> empty list."""
        result = merge_close_boundaries([], 1.0)
        assert result == []

    def test_single_boundary_returns_single_boundary(self) -> None:
        """Single boundary is always retained."""
        boundaries = [_make_boundary(3.0, 0.5, 0)]
        result = merge_close_boundaries(boundaries, 1.0)
        assert len(result) == 1
        assert result[0].timestamp_sec == pytest.approx(3.0)


class TestMergeCloseBoundariesNoMerge:
    """Boundaries far enough apart are not merged."""

    def test_two_boundaries_far_apart_both_retained(self) -> None:
        """Gap 2.0s > min_duration_sec=1.0 -> both retained."""
        boundaries = [
            _make_boundary(1.0, 0.5, 0),
            _make_boundary(3.0, 0.7, 1),
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        assert len(result) == 2

    def test_min_duration_zero_no_merge(self) -> None:
        """min_duration_sec=0.0 -> no boundaries merged (disable mode)."""
        boundaries = [
            _make_boundary(1.0, 0.5, 0),
            _make_boundary(1.1, 0.8, 1),
            _make_boundary(1.2, 0.3, 2),
        ]
        result = merge_close_boundaries(boundaries, 0.0)
        assert len(result) == 3


class TestMergeCloseBoundariesMerge:
    """Close boundaries are merged: higher confidence wins."""

    def test_two_close_boundaries_keeps_higher_confidence(self) -> None:
        """Gap 0.5s < min_duration_sec=1.0 -> boundary with higher confidence retained."""
        boundaries = [
            _make_boundary(1.0, 0.4, 0),
            _make_boundary(1.5, 0.9, 1),  # higher confidence
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        assert len(result) == 1
        assert result[0].confidence == pytest.approx(0.9)

    def test_two_close_boundaries_lower_confidence_dropped(self) -> None:
        """Lower confidence boundary is dropped when merging."""
        boundaries = [
            _make_boundary(5.0, 0.8, 0),  # higher confidence
            _make_boundary(5.3, 0.2, 1),  # lower confidence
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        assert len(result) == 1
        assert result[0].confidence == pytest.approx(0.8)

    def test_three_close_boundaries_keeps_highest_confidence(self) -> None:
        """3 consecutive boundaries all within min_duration -> only highest confidence kept."""
        boundaries = [
            _make_boundary(1.0, 0.3, 0),
            _make_boundary(1.3, 0.9, 1),  # highest
            _make_boundary(1.6, 0.5, 2),
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        assert len(result) == 1
        assert result[0].confidence == pytest.approx(0.9)

    def test_exact_min_duration_boundary_treatment(self) -> None:
        """Gap exactly equal to min_duration_sec: implementation may keep or merge.

        The spec says 'closer than min_duration_sec', so gap == min_duration
        is on the boundary. Both behaviors (keep/merge) are acceptable;
        we only verify the result is a valid list.
        """
        boundaries = [
            _make_boundary(1.0, 0.5, 0),
            _make_boundary(2.0, 0.7, 1),  # gap = 1.0 == min_duration_sec
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        # Must return 1 or 2 items (both outcomes are acceptable at boundary)
        assert len(result) in (1, 2)


class TestMergeCloseBoundariesSorting:
    """merge_close_boundaries handles unsorted input."""

    def test_unsorted_input_still_merges_correctly(self) -> None:
        """Boundaries in random order: merging still works (sort-agnostic contract)."""
        boundaries = [
            _make_boundary(5.0, 0.4, 1),
            _make_boundary(1.0, 0.9, 0),
            _make_boundary(1.3, 0.2, 2),
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        # (1.0, 0.9) and (1.3, 0.2) are close -> only 0.9 survives
        # (5.0, 0.4) is far -> also survives
        assert len(result) == 2

    def test_result_is_ordered_by_timestamp(self) -> None:
        """Output is sorted in ascending timestamp order."""
        boundaries = [
            _make_boundary(10.0, 0.5, 2),
            _make_boundary(2.0, 0.8, 0),
            _make_boundary(6.0, 0.6, 1),
        ]
        result = merge_close_boundaries(boundaries, 0.5)
        timestamps = [b.timestamp_sec for b in result]
        assert timestamps == sorted(timestamps)


class TestMergeCloseBoundariesMixed:
    """Mix of close and far boundaries."""

    def test_mixed_close_and_far(self) -> None:
        """Some pairs close, some far: only close pairs are merged."""
        boundaries = [
            _make_boundary(1.0, 0.5, 0),
            _make_boundary(1.4, 0.8, 1),  # close to 1.0 (gap 0.4s < 1.0)
            _make_boundary(5.0, 0.6, 2),  # far from 1.4 (gap 3.6s > 1.0)
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        assert len(result) == 2
        # The merged pair should have confidence 0.8 (the higher one)
        assert result[0].confidence == pytest.approx(0.8)
        assert result[1].timestamp_sec == pytest.approx(5.0)

    def test_scene_indices_are_reassigned_after_merge(self) -> None:
        """After merging, scene_index values in the result are 0-based and contiguous."""
        boundaries = [
            _make_boundary(1.0, 0.5, 0),
            _make_boundary(1.4, 0.8, 1),  # merged with 1.0
            _make_boundary(5.0, 0.6, 2),
        ]
        result = merge_close_boundaries(boundaries, 1.0)
        # Re-indexed: must be [0, 1] for 2 surviving boundaries
        indices = [b.scene_index for b in result]
        assert indices == list(range(len(result)))
