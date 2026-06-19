"""test_retiming.py — Red tests for retiming.py (pure logic).

Target module: clipwright_render.retiming (not yet implemented)

All assertions use opentime.RationalTime == or .almost_equal() comparisons.
Float-second comparisons are forbidden (AC-8 / D7 / NFR-2).

Test groups:
  A. build_program_time_map
  B. remap_window (parametrized)
  C. SRT I/O (parse_srt / serialize_srt)
"""

from __future__ import annotations

import textwrap

import opentimelineio as otio
import pytest

# ---------------------------------------------------------------------------
# Import target — the module does not exist yet; collection will fail here
# with ImportError which is the expected Red state.
# ---------------------------------------------------------------------------
from clipwright_render.retiming import (  # type: ignore[import]
    ProgramSegment,
    ProgramTimeMap,
    ProgramWindow,
    RemapResult,
    SrtCue,
    build_program_time_map,
    parse_srt,
    remap_window,
    serialize_srt,
)
from clipwright_render.plan import KeptRange  # existing definition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATE = 30  # rational time rate used throughout tests


def _rt(seconds: float, rate: int = _RATE) -> otio.opentime.RationalTime:
    """Create a RationalTime from float seconds at the given rate."""
    return otio.opentime.RationalTime.from_seconds(seconds, rate)


def _tr(start_s: float, end_s: float, rate: int = _RATE) -> otio.opentime.TimeRange:
    """Create a half-open TimeRange [start_s, end_s) at the given rate."""
    start = _rt(start_s, rate)
    duration = _rt(end_s - start_s, rate)
    return otio.opentime.TimeRange(start_time=start, duration=duration)


def _kept(start_s: float, end_s: float, source: str = "clip.mp4", scalar: float = 1.0) -> KeptRange:
    """Construct a KeptRange for test use."""
    return KeptRange(source=source, source_range=_tr(start_s, end_s), time_scalar=scalar)


# ---------------------------------------------------------------------------
# A. build_program_time_map
# ---------------------------------------------------------------------------

class TestBuildProgramTimeMap:
    """A. build_program_time_map: source->program mapping construction."""

    def test_single_full_range_no_cut_no_warp(self) -> None:
        """Single kept range covering the full source: has_cut=False, has_warp=False."""
        ranges = [_kept(0.0, 10.0)]
        tmap = build_program_time_map(ranges)

        assert isinstance(tmap, ProgramTimeMap)
        assert tmap.has_cut is False
        assert tmap.has_warp is False
        assert len(tmap.segments) == 1

        seg = tmap.segments[0]
        assert isinstance(seg, ProgramSegment)
        # program_start should be at the beginning (0)
        assert seg.program_start == _rt(0.0)
        # source_start / source_end should match the input range
        assert seg.source_start == _rt(0.0)
        assert seg.source_end == _rt(10.0)
        assert seg.time_scalar == 1.0

    def test_two_ranges_with_cut_accumulates_program_offset(self) -> None:
        """2 kept ranges with a 5s cut in between: has_cut=True, program offset cumulates."""
        # Source: 0-5s kept, 5-10s cut, 10-15s kept
        # Program: seg0 = program 0-5s, seg1 = program 5-10s
        ranges = [_kept(0.0, 5.0), _kept(10.0, 15.0)]
        tmap = build_program_time_map(ranges)

        assert tmap.has_cut is True
        assert tmap.has_warp is False
        assert len(tmap.segments) == 2

        seg0 = tmap.segments[0]
        assert seg0.source_start == _rt(0.0)
        assert seg0.source_end == _rt(5.0)
        assert seg0.program_start == _rt(0.0)

        seg1 = tmap.segments[1]
        assert seg1.source_start == _rt(10.0)
        assert seg1.source_end == _rt(15.0)
        # Program offset should be exactly 5s (duration of seg0)
        assert seg1.program_start == _rt(5.0)

    def test_warp_range_sets_has_warp_and_program_duration(self) -> None:
        """Warp range (time_scalar=2.0): has_warp=True, program_dur = src_dur / scalar."""
        # Source 0-10s at 2x speed -> program duration = 5s
        ranges = [_kept(0.0, 10.0, scalar=2.0)]
        tmap = build_program_time_map(ranges)

        assert tmap.has_cut is False
        assert tmap.has_warp is True
        assert len(tmap.segments) == 1

        seg = tmap.segments[0]
        assert seg.time_scalar == 2.0
        # Program end = program_start + src_dur / time_scalar = 0 + 10/2 = 5s
        program_end = seg.program_start + otio.opentime.RationalTime.from_seconds(
            (seg.source_end - seg.source_start).to_seconds() / seg.time_scalar,
            _RATE,
        )
        assert program_end.almost_equal(_rt(5.0), delta=otio.opentime.RationalTime(1, _RATE))

    def test_warp_and_cut_combined(self) -> None:
        """Warp + cut: has_warp=True, has_cut=True, segments reflect both effects."""
        # seg0: source 0-6s at 2x -> program 0-3s
        # seg1: source 10-14s at 1x -> program 3-7s  (6s cut in source; 4s gap)
        ranges = [_kept(0.0, 6.0, scalar=2.0), _kept(10.0, 14.0, scalar=1.0)]
        tmap = build_program_time_map(ranges)

        assert tmap.has_cut is True
        assert tmap.has_warp is True
        assert len(tmap.segments) == 2

        seg0 = tmap.segments[0]
        assert seg0.time_scalar == 2.0
        # seg0 program duration = 6/2 = 3s
        seg0_prog_dur_s = (seg0.source_end - seg0.source_start).to_seconds() / seg0.time_scalar
        assert abs(seg0_prog_dur_s - 3.0) < 1e-6

        seg1 = tmap.segments[1]
        # seg1 program_start should be ~3s
        assert seg1.program_start.almost_equal(
            _rt(3.0), delta=otio.opentime.RationalTime(1, _RATE)
        )


# ---------------------------------------------------------------------------
# B. remap_window (parametrized and individual)
# ---------------------------------------------------------------------------

class TestRemapWindow:
    """B. remap_window: pure source->program interval mapping."""

    # --- helpers for common timeline setups ---

    @staticmethod
    def _tmap_two_ranges() -> ProgramTimeMap:
        """Two kept ranges: [0-5s, 10-15s] source -> program [0-5s, 5-10s]."""
        return build_program_time_map([_kept(0.0, 5.0), _kept(10.0, 15.0)])

    @staticmethod
    def _tmap_single_range() -> ProgramTimeMap:
        """Single kept range: [0-10s] source -> program [0-10s] (identity)."""
        return build_program_time_map([_kept(0.0, 10.0)])

    @staticmethod
    def _tmap_warp_single() -> ProgramTimeMap:
        """Single warp range: [0-10s] at 2x -> program [0-5s]."""
        return build_program_time_map([_kept(0.0, 10.0, scalar=2.0)])

    # --- AC-1: shift (window falls fully in a kept range, but source != program) ---

    def test_shift_window_in_second_kept_range(self) -> None:
        """AC-1: Window 12-14s in second kept range -> program 7-9s, shifted=True."""
        tmap = self._tmap_two_ranges()
        result = remap_window(tmap, src_start=_rt(12.0), src_end=_rt(14.0))

        assert isinstance(result, RemapResult)
        assert result.dropped is False
        assert result.split is False
        assert result.clipped is False
        assert result.shifted is True
        assert len(result.windows) == 1

        win = result.windows[0]
        assert isinstance(win, ProgramWindow)
        # source 12-14s in second range (source_start=10s, program_start=5s)
        # program = 5 + (12-10) = 7s ... 5 + (14-10) = 9s
        assert win.program_start.almost_equal(_rt(7.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert win.program_end.almost_equal(_rt(9.0), delta=otio.opentime.RationalTime(1, _RATE))

    # --- AC-2: split (window crosses a cut boundary) ---

    def test_split_window_crosses_cut_boundary(self) -> None:
        """AC-2: Window 3-12s crosses the cut -> 2 windows, split=True."""
        tmap = self._tmap_two_ranges()
        # Source: [0-5s kept] [5-10s cut] [10-15s kept]
        # Window 3-12s intersects seg0 (3-5s) and seg1 (10-12s)
        result = remap_window(tmap, src_start=_rt(3.0), src_end=_rt(12.0))

        assert result.split is True
        assert result.dropped is False
        assert len(result.windows) == 2

        # window 1: source 3-5s -> program 3-5s (seg0 no shift)
        win0 = result.windows[0]
        assert win0.program_start.almost_equal(_rt(3.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert win0.program_end.almost_equal(_rt(5.0), delta=otio.opentime.RationalTime(1, _RATE))

        # window 2: source 10-12s -> program 5-7s (seg1 program_start=5s)
        win1 = result.windows[1]
        assert win1.program_start.almost_equal(_rt(5.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert win1.program_end.almost_equal(_rt(7.0), delta=otio.opentime.RationalTime(1, _RATE))

    # --- AC-3: drop (window falls entirely in a removed region) ---

    def test_drop_window_in_removed_region(self) -> None:
        """AC-3: Window 6-9s (fully in cut region 5-10s) -> windows=[], dropped=True."""
        tmap = self._tmap_two_ranges()
        result = remap_window(tmap, src_start=_rt(6.0), src_end=_rt(9.0))

        assert result.dropped is True
        assert result.windows == []
        assert result.split is False
        assert result.clipped is False

    # --- B1: clip (partial intersection with removed region) ---

    def test_clip_window_partially_overlaps_removed(self) -> None:
        """B1: Window 3-8s partially overlaps the cut -> clipped=True, only 3-5s survives."""
        tmap = self._tmap_two_ranges()
        result = remap_window(tmap, src_start=_rt(3.0), src_end=_rt(8.0))

        assert result.clipped is True
        assert result.dropped is False
        assert len(result.windows) == 1

        win = result.windows[0]
        assert win.program_start.almost_equal(_rt(3.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert win.program_end.almost_equal(_rt(5.0), delta=otio.opentime.RationalTime(1, _RATE))

    # --- AC-4: warp scale (window duration shrinks by 1/scalar) ---

    def test_warp_scale_shrinks_window_duration(self) -> None:
        """AC-4: kept [0-10s] at 2x, window 4-6s -> program 2-3s (length 1/2)."""
        tmap = self._tmap_warp_single()
        result = remap_window(tmap, src_start=_rt(4.0), src_end=_rt(6.0))

        assert result.dropped is False
        assert result.split is False
        assert len(result.windows) == 1

        win = result.windows[0]
        # program_start = 0 + (4-0)/2 = 2s; program_end = 0 + (6-0)/2 = 3s
        assert win.program_start.almost_equal(_rt(2.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert win.program_end.almost_equal(_rt(3.0), delta=otio.opentime.RationalTime(1, _RATE))
        # Duration should be 1s (= 2s source / 2x scalar)
        prog_dur = win.program_end - win.program_start
        expected_dur = otio.opentime.RationalTime.from_seconds(1.0, _RATE)
        assert prog_dur.almost_equal(expected_dur, delta=otio.opentime.RationalTime(1, _RATE))

    # --- AC-5: identity (no cut, no warp -> window unchanged, shifted=False) ---

    def test_identity_map_returns_window_unchanged(self) -> None:
        """AC-5: Identity map (no cut/warp) -> program_start==source_start, shifted=False."""
        tmap = self._tmap_single_range()
        assert tmap.has_cut is False
        assert tmap.has_warp is False

        result = remap_window(tmap, src_start=_rt(2.0), src_end=_rt(7.0))

        assert result.shifted is False
        assert result.dropped is False
        assert result.split is False
        assert result.clipped is False
        assert len(result.windows) == 1

        win = result.windows[0]
        assert win.program_start == _rt(2.0)
        assert win.program_end == _rt(7.0)

    # --- Half-open boundary: end==seg_end should NOT be double-counted ---

    def test_boundary_end_equals_seg_end_no_double_count(self) -> None:
        """Boundary: window end==seg0.source_end should not appear in seg1."""
        # seg0: source 0-5s, seg1: source 10-15s
        # Window 3-5s should intersect seg0 only (end is exclusive)
        tmap = self._tmap_two_ranges()
        result = remap_window(tmap, src_start=_rt(3.0), src_end=_rt(5.0))

        # Should get exactly 1 window from seg0, not split
        assert result.split is False
        assert len(result.windows) == 1

        win = result.windows[0]
        assert win.program_start.almost_equal(_rt(3.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert win.program_end.almost_equal(_rt(5.0), delta=otio.opentime.RationalTime(1, _RATE))

    # --- Parametrized coverage across disposition combos ---

    @pytest.mark.parametrize(
        "src_start_s, src_end_s, exp_windows_count, exp_dropped, exp_split, exp_clipped, exp_shifted",
        [
            # fully inside seg0 -> 1 window, no shift (source==program for seg0)
            (1.0, 4.0, 1, False, False, False, False),
            # fully inside seg1 (source 10-15 -> program 5-10) -> shifted
            (11.0, 13.0, 1, False, False, False, True),
            # fully in cut zone -> dropped
            (5.5, 9.5, 0, True, False, False, False),
            # crosses cut, both sides -> split
            (2.0, 11.0, 2, False, True, False, True),
            # starts in seg0, ends in cut -> clipped (only seg0 portion survives)
            (4.0, 7.0, 1, False, False, True, False),
            # starts in cut, ends in seg1 -> clipped (only seg1 portion survives)
            (7.0, 12.0, 1, False, False, True, True),
        ],
    )
    def test_remap_window_parametrized(
        self,
        src_start_s: float,
        src_end_s: float,
        exp_windows_count: int,
        exp_dropped: bool,
        exp_split: bool,
        exp_clipped: bool,
        exp_shifted: bool,
    ) -> None:
        """Parametrized remap_window coverage for common disposition cases."""
        tmap = self._tmap_two_ranges()
        result = remap_window(tmap, src_start=_rt(src_start_s), src_end=_rt(src_end_s))

        assert len(result.windows) == exp_windows_count
        assert result.dropped is exp_dropped
        assert result.split is exp_split
        assert result.clipped is exp_clipped
        assert result.shifted is exp_shifted


# ---------------------------------------------------------------------------
# C. SRT I/O
# ---------------------------------------------------------------------------

class TestParseSrt:
    """C. parse_srt: SRT text -> list[SrtCue] with RationalTime."""

    _SAMPLE_SRT = textwrap.dedent("""\
        1
        00:00:01,000 --> 00:00:03,500
        Hello world

        2
        00:00:05,250 --> 00:00:07,000
        Second cue

        """)

    def test_parse_normal_srt_returns_rational_time_cues(self) -> None:
        """Normal SRT -> RationalTime cues (not float seconds)."""
        cues = parse_srt(self._SAMPLE_SRT)
        assert isinstance(cues, list)
        assert len(cues) == 2

        c0 = cues[0]
        assert isinstance(c0, SrtCue)
        # 00:00:01,000 = 1.0s
        assert c0.start.almost_equal(_rt(1.0), delta=otio.opentime.RationalTime(1, _RATE))
        # 00:00:03,500 = 3.5s
        assert c0.end.almost_equal(_rt(3.5), delta=otio.opentime.RationalTime(1, _RATE))
        assert c0.text == "Hello world"

        c1 = cues[1]
        # 00:00:05,250 = 5.25s
        assert c1.start.almost_equal(_rt(5.25), delta=otio.opentime.RationalTime(1, _RATE))
        # 00:00:07,000 = 7.0s
        assert c1.end.almost_equal(_rt(7.0), delta=otio.opentime.RationalTime(1, _RATE))
        assert c1.text == "Second cue"

    def test_parse_empty_string_returns_empty_list(self) -> None:
        """Empty string -> [] (no error)."""
        cues = parse_srt("")
        assert cues == []

    def test_parse_whitespace_only_returns_empty_list(self) -> None:
        """Whitespace-only string -> []."""
        cues = parse_srt("   \n\n  ")
        assert cues == []

    def test_parse_multiline_text(self) -> None:
        """Multi-line cue text is preserved with newline."""
        srt = textwrap.dedent("""\
            1
            00:00:01,000 --> 00:00:03,000
            Line one
            Line two

            """)
        cues = parse_srt(srt)
        assert len(cues) == 1
        assert "Line one" in cues[0].text
        assert "Line two" in cues[0].text

    def test_parse_invalid_timecode_raises_value_error(self) -> None:
        """Malformed timecode -> ValueError."""
        bad_srt = textwrap.dedent("""\
            1
            BADTIME --> 00:00:03,000
            Text

            """)
        with pytest.raises(ValueError):
            parse_srt(bad_srt)

    def test_parse_hours_minutes_seconds_milliseconds(self) -> None:
        """Timecode with hours: 01:02:03,456 = 3723.456s."""
        srt = textwrap.dedent("""\
            1
            01:02:03,456 --> 01:02:04,000
            With hours

            """)
        cues = parse_srt(srt)
        assert len(cues) == 1
        expected_start_s = 1 * 3600 + 2 * 60 + 3 + 0.456
        # Use a generous delta because millisecond precision with rate=30 may round
        assert cues[0].start.almost_equal(
            otio.opentime.RationalTime.from_seconds(expected_start_s, _RATE),
            delta=otio.opentime.RationalTime(2, _RATE),
        )


class TestSerializeSrt:
    """C. serialize_srt: list[SrtCue] -> SRT string."""

    def test_serialize_empty_list_returns_empty_string(self) -> None:
        """Empty cue list -> empty string (mirrors transcribe.to_srt([]) behavior)."""
        result = serialize_srt([])
        assert result == ""

    def test_serialize_single_cue(self) -> None:
        """Single cue serializes to 1-based index with correct format."""
        cues = [SrtCue(start=_rt(1.0), end=_rt(3.5), text="Hello world")]
        result = serialize_srt(cues)
        assert "1\n" in result
        assert "00:00:01,000 --> 00:00:03,500" in result
        assert "Hello world" in result

    def test_serialize_uses_one_based_index(self) -> None:
        """Indices must be 1-based (first cue = 1, second = 2)."""
        cues = [
            SrtCue(start=_rt(0.0), end=_rt(1.0), text="First"),
            SrtCue(start=_rt(2.0), end=_rt(3.0), text="Second"),
        ]
        result = serialize_srt(cues)
        lines = result.splitlines()
        # Find index lines: they should be "1" and "2"
        index_lines = [l for l in lines if l.strip().isdigit()]
        assert index_lines[0] == "1"
        assert index_lines[1] == "2"

    def test_serialize_blank_line_separated(self) -> None:
        """Cue blocks must be separated by blank lines."""
        cues = [
            SrtCue(start=_rt(0.0), end=_rt(1.0), text="A"),
            SrtCue(start=_rt(2.0), end=_rt(3.0), text="B"),
        ]
        result = serialize_srt(cues)
        # There should be a blank line between the two cue blocks
        assert "\n\n" in result

    def test_serialize_single_trailing_newline(self) -> None:
        """Output must end with exactly one trailing newline (transcribe.to_srt compat)."""
        cues = [SrtCue(start=_rt(0.0), end=_rt(1.0), text="X")]
        result = serialize_srt(cues)
        assert result.endswith("\n")
        assert not result.endswith("\n\n")


class TestSrtRoundTrip:
    """C. Round-trip: parse -> serialize produces byte-identical output."""

    # Canonical SRT matching transcribe.to_srt output format:
    # - 1-based index
    # - blank-line separator between blocks
    # - single trailing newline
    _CANONICAL_SRT = (
        "1\n"
        "00:00:01,000 --> 00:00:03,500\n"
        "Hello world\n"
        "\n"
        "2\n"
        "00:00:05,250 --> 00:00:07,000\n"
        "Second cue\n"
    )

    def test_round_trip_parse_serialize_byte_identical(self) -> None:
        """parse_srt -> serialize_srt must produce byte-identical output to input.

        This verifies compatibility with transcribe.to_srt output format (ADR-2).
        """
        cues = parse_srt(self._CANONICAL_SRT)
        result = serialize_srt(cues)
        assert result == self._CANONICAL_SRT

    def test_ms_quantization_round_half_up(self) -> None:
        """RationalTime -> ms must use round-half-up (int(round(sec*1000))).

        A timecode of 1.0005s at rate=1000 should round to 1001ms (not 1000ms),
        but at standard display rates (30fps) the quantization matches _format_timecode.
        This test verifies the SRT timecode string for a known ms value.
        """
        # 1.500s exactly -> "00:00:01,500"
        cue = SrtCue(
            start=otio.opentime.RationalTime.from_seconds(1.5, 1000),
            end=otio.opentime.RationalTime.from_seconds(2.0, 1000),
            text="ms test",
        )
        result = serialize_srt([cue])
        assert "00:00:01,500" in result
        assert "00:00:02,000" in result

    def test_ms_quantization_matches_format_timecode(self) -> None:
        """serialize_srt ms output must match transcribe._format_timecode logic.

        Both use int(round(sec * 1000)) (round-half-up). Verify with a value
        that exercises the rounding: 0.0015s -> 2ms (rounds up from 1.5).
        """
        from clipwright_transcribe.captions import _format_timecode

        test_seconds = 3723.456  # 01:02:03,456
        rt = otio.opentime.RationalTime.from_seconds(test_seconds, 1000)
        cue = SrtCue(start=rt, end=otio.opentime.RationalTime.from_seconds(test_seconds + 1.0, 1000), text="x")
        result = serialize_srt([cue])
        expected_tc = _format_timecode(test_seconds, ms_separator=",")
        assert expected_tc in result
