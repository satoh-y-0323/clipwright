"""test_plan_nle_relativize.py — Red tests for TC-origin coordinate
relativization in render (ADR-NI-1, architecture-report-20260715-191151.md).

Scope (requirements-report-20260715-190935.md FR-7, AC-6 (unit portion),
AC-7, AC-8):

  NLE-interop create tools (trim/silence/sequence/... via
  clipwright.nle_interop.conform_timeline_for_nle, out of scope for this
  package) may write timecode-origin coordinates into a Clip's
  ExternalReference.available_range and the Clip's own source_range (both
  shifted by the same TC offset — OTIO's native available_range/source_range
  semantics). render must consume such timelines by relativizing
  source_range back to a 0-origin *file* second before it reaches ffmpeg
  trim/atrim arguments:

      rel_start = source_range.start_time - available_range.start_time

  This module tests the two chokepoints identified in the architecture
  report where source_range enters a KeptRange/BgmClip:
    - resolve_kept_ranges (plan.py, ~line 1392: `mr` is available_range's
      owner, in the same scope as source_range)
    - resolve_bgm (plan.py, ~line 1515; defensive — BGM clips are not
      produced by NLE-interop today, but the relativization must apply
      uniformly wherever a Clip's source_range flows into a KeptRange-like
      value object)

  Pure OTIO-level tests only — no ffmpeg/ffprobe dependency, no spike
  dependency. Rate-arithmetic assumptions (RationalTime subtraction across
  differing rates auto-rescales to the minuend's rate) were confirmed
  directly against the installed opentimelineio build before writing these
  assertions.

  All tests are now regression guards: resolve_kept_ranges/resolve_bgm apply
  relativization via _relativize_source_range_to_file_seconds (ADR-NI-1).
  Tests 1, 2, 4, 5, 6, 7 verify the relativization logic works correctly
  (absolute → file-relative seconds, rate mismatches, TC underflow detection,
  BGM defensive application, retiming parity). Tests 3 (backward-compat, no
  available_range / available_range.start == 0) confirm the no-op path is
  still honored.
"""

from __future__ import annotations

import re

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import (
    BgmClip,
    ProbeInfo,
    build_plan,
    resolve_bgm,
    resolve_kept_ranges,
)
from clipwright_render.retiming import build_program_time_map
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FPS = 25.0

# 01:00:00:00 @ 25fps (a common non-drop-frame broadcast start timecode; see
# ADR-NI-2 spike scope). Expressed in seconds for readability in helpers.
TC_START_SEC = 3600.0

# Large-enough placeholder available_range duration (the whole media asset);
# never asserted on directly, only its start_time matters for these tests.
_AVAILABLE_DURATION_SEC = 7200.0

_VALID_BGM_DIRECTIVE: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
}


# ---------------------------------------------------------------------------
# Helpers (self-contained; mirrors test_plan.py's _make_clip pattern with an
# added available_range parameter — ADR-NI-1 needs a variant that test_plan.py
# does not have).
# ---------------------------------------------------------------------------


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    """Convert seconds to RationalTime."""
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    """Return a TimeRange of start seconds and duration seconds."""
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_tc_clip(
    source: str,
    source_start: float,
    duration: float,
    *,
    available_start: float | None = TC_START_SEC,
    available_duration: float = _AVAILABLE_DURATION_SEC,
    source_rate: float = FPS,
    available_rate: float = FPS,
) -> otio.schema.Clip:
    """Build a Clip whose ExternalReference.available_range may start at a
    non-zero timecode origin (ADR-NI-1).

    available_start=None omits available_range entirely (NFR-1 backward
    compat: no NLE-interop conform was ever applied to this timeline).
    source_rate/available_rate may differ to exercise cross-rate
    relativization (observation 6).
    """
    clip = otio.schema.Clip()
    ref = otio.schema.ExternalReference(target_url=source)
    if available_start is not None:
        ref.available_range = _tr(available_start, available_duration, available_rate)
    clip.media_reference = ref
    clip.source_range = _tr(source_start, duration, source_rate)
    return clip


def _make_timeline_with_clips(
    clips: list[otio.schema.Clip | otio.schema.Gap],
    track_kind: str = otio.schema.TrackKind.Video,
) -> otio.schema.Timeline:
    """Build a single-track Timeline containing the given clips."""
    track = otio.schema.Track(kind=track_kind)
    for item in clips:
        track.append(item)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


def _make_bgm_only_timeline(clip: otio.schema.Clip) -> otio.schema.Timeline:
    """Build a Timeline containing only a single Audio track with a
    kind=="bgm" Clip (resolve_bgm does not require a V1 video track)."""
    audio_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
    audio_track.append(clip)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(audio_track)
    return timeline


# ===========================================================================
# Observation 1: resolve_kept_ranges relativizes source_range to a 0-origin
# file second using available_range.start (ADR-NI-1 / FR-7).
# ===========================================================================


class TestResolveKeptRangesRelativizesToAvailableRange:
    def test_source_range_relativized_to_available_range_start(self) -> None:
        """A TC-origin clip (available_range.start=3600s, source_range.start=
        3605s) must yield a KeptRange whose source_range is 0-origin: start=5s.

        Regression test: verify that resolve_kept_ranges correctly relativizes
        source_range using available_range.start_time to convert from absolute
        timecode-origin coordinates to file-relative seconds (ADR-NI-1).
        """
        clip = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC + 5.0, duration=3.0
        )
        tl = _make_timeline_with_clips([clip])

        ranges = resolve_kept_ranges(tl)

        assert len(ranges) == 1
        expected = _tr(5.0, 3.0)
        got = ranges[0].source_range
        assert got.start_time == expected.start_time, (
            f"expected relativized start {expected.start_time!r}, got "
            f"{got.start_time!r} — TC-origin relativization (ADR-NI-1) is not"
            " yet implemented in resolve_kept_ranges"
        )
        assert got.duration == expected.duration

    def test_multiple_tc_clips_all_relativized(self) -> None:
        """All Clips in the video track are individually relativized against
        their own available_range.start, not just the first one.

        Regression test: verify that resolve_kept_ranges applies relativization
        to each clip independently (ADR-NI-1).
        """
        clip1 = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC + 0.0, duration=2.0
        )
        clip2 = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC + 10.0, duration=4.0
        )
        tl = _make_timeline_with_clips([clip1, clip2])

        ranges = resolve_kept_ranges(tl)

        assert len(ranges) == 2
        assert ranges[0].source_range.start_time == _rt(0.0)
        assert ranges[1].source_range.start_time == _rt(10.0)


# ===========================================================================
# Observation 2: build_plan trim=start uses the relative value, never the
# raw TC-origin absolute value (FR-7 / AC-6 unit portion).
# ===========================================================================


class TestBuildPlanRelativizesTcOriginTrim:
    def test_filter_complex_trim_start_is_relative_not_absolute(self) -> None:
        """ffmpeg filter_complex trim=start must be the relativized 5.0s, and
        must never contain the raw absolute TC-origin value (3605s).

        Regression test: verify that build_plan uses the relativized source_range
        from resolve_kept_ranges, so filter_complex contains trim=start=5.0 (file
        seconds), not the raw TC-absolute value (ADR-NI-1, AC-6 unit portion).
        """
        clip = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC + 5.0, duration=3.0
        )
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)

        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex

        assert "trim=start=5.0" in fc or "trim=start=5:" in fc, (
            f"expected relative trim start 5.0 in filter_complex, got: {fc!r}"
        )
        assert "3605" not in fc, (
            "raw absolute TC-origin value leaked into ffmpeg filter_complex"
            f" (must be relativized to file seconds): {fc!r}"
        )


# ===========================================================================
# Observation 3: backward compatibility — no available_range, or
# available_range.start == 0, is a no-op (NFR-1 / AC-7). These must stay
# green both before and after the Green-phase implementation.
# ===========================================================================


class TestResolveKeptRangesBackwardCompatNoAvailableRange:
    def test_no_available_range_is_unchanged(self) -> None:
        """No available_range at all (legacy timeline, no NLE-interop conform
        applied) must leave source_range untouched."""
        clip = _make_tc_clip(
            "/src/a.mp4", source_start=1.5, duration=3.25, available_start=None
        )
        tl = _make_timeline_with_clips([clip])

        ranges = resolve_kept_ranges(tl)

        assert ranges[0].source_range == _tr(1.5, 3.25)

    def test_available_range_start_zero_is_unchanged(self) -> None:
        """available_range.start == 0 (the pre-ADR-NI-1 wiring, e.g. current
        clipwright-trim ADR-3 output) must leave source_range untouched
        (subtracting zero is a no-op)."""
        clip = _make_tc_clip(
            "/src/a.mp4", source_start=1.5, duration=3.25, available_start=0.0
        )
        tl = _make_timeline_with_clips([clip])

        ranges = resolve_kept_ranges(tl)

        assert ranges[0].source_range == _tr(1.5, 3.25)


# ===========================================================================
# Observation 4: source_range.start < available_range.start is a data error
# (TC underflow) → INVALID_INPUT with a fixed-literal message + hint
# (AC-8 / NFR-7 / SR NL-1 pattern: no raw untrusted value in message).
# ===========================================================================


class TestResolveKeptRangesTcUnderflowRejected:
    def test_source_range_before_available_range_raises_invalid_input(self) -> None:
        """source_range.start 1s before available_range.start must raise
        ClipwrightError(INVALID_INPUT), not silently pass through or crash
        with a negative trim value.

        Regression test: verify that resolve_kept_ranges rejects TC underflow
        (source_range.start < available_range.start) with a fixed-literal message
        and actionable hint (ADR-NI-1 / AC-8 / SR NL-1 compliance).
        """
        clip = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC - 1.0, duration=2.0
        )
        tl = _make_timeline_with_clips([clip])

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)

        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.hint, "hint must be non-empty (points to the next action)"
        assert isinstance(err.message, str) and err.message != ""
        # SR NL-1 / CWE-209: message must be a fixed literal, not an
        # interpolation of the raw (untrusted) OTIO time values.
        assert not re.search(r"\d", err.message), (
            "message must not embed raw numeric time values"
            f" (fixed literal expected): {err.message!r}"
        )


# ===========================================================================
# Observation 5: resolve_bgm applies the same relativization defensively,
# even though NLE-interop conform does not produce BGM clips today
# (architecture-report §2 "resolve_bgm (:1515・防御的)").
# ===========================================================================


class TestResolveBgmRelativizesToAvailableRange:
    def test_bgm_clip_source_range_relativized_to_available_range_start(self) -> None:
        """A TC-origin BGM clip (available_range.start=3600s, source_range.
        start=3602s) must yield a BgmClip whose source_range is 0-origin:
        start=2s.

        Regression test: verify that resolve_bgm defensively applies the same
        relativization, even though NLE-interop conform does not produce BGM
        clips today (ADR-NI-1 defensive application, architecture-report §2).
        """
        clip = otio.schema.Clip()
        ref = otio.schema.ExternalReference(target_url="/proj/bgm.mp3")
        ref.available_range = _tr(TC_START_SEC, _AVAILABLE_DURATION_SEC)
        clip.media_reference = ref
        clip.source_range = _tr(TC_START_SEC + 2.0, 3.0)
        clip.metadata["clipwright"] = dict(_VALID_BGM_DIRECTIVE)
        tl = _make_bgm_only_timeline(clip)

        result = resolve_bgm(tl)

        assert isinstance(result, BgmClip)
        expected = _tr(2.0, 3.0)
        assert result.source_range.start_time == expected.start_time, (
            f"expected relativized BGM start {expected.start_time!r}, got "
            f"{result.source_range.start_time!r}"
        )
        assert result.source_range.duration == expected.duration


# ===========================================================================
# Observation 6: rate mismatch between source_range and available_range is
# rescaled correctly via RationalTime subtraction (auto-rescales to the
# minuend's rate; confirmed directly against the installed OTIO build).
# ===========================================================================


class TestResolveKeptRangesRateMismatch:
    def test_relativization_correct_when_rates_differ(self) -> None:
        """source_range at 25fps, available_range at 24fps: the relativized
        start must still be the correct 5.0s (125/25), not a naive same-rate
        value subtraction.

        Regression test: verify that RationalTime subtraction auto-rescaling
        handles rate mismatches correctly (minuend's rate preserved — ADR-NI-1,
        observation 6 in test_plan_nle_relativize.py).
        """
        clip = _make_tc_clip(
            "/src/a.mov",
            source_start=TC_START_SEC + 5.0,
            duration=3.0,
            source_rate=25.0,
            available_rate=24.0,
        )
        tl = _make_timeline_with_clips([clip])

        ranges = resolve_kept_ranges(tl)

        got = ranges[0].source_range.start_time
        assert got == otio.opentime.RationalTime(125, 25.0), (
            f"expected RationalTime(125, 25.0) (5.0s), got {got!r} — rate"
            " mismatch relativization regressed (ADR-NI-1)"
        )


# ===========================================================================
# Observation 7: retiming (build_program_time_map) is unaffected by
# TC-origin coordinates — because resolve_kept_ranges is the single
# chokepoint where relativization happens, the ProgramTimeMap produced from
# a TC-origin timeline must be identical to the one produced from an
# equivalent 0-origin timeline (architecture-report §2 "下流の
# trim/atrim・retiming・字幕/overlay 再タイミングは KeptRange 経由のため
# 修正不要").
# ===========================================================================


class TestRetimingParityWithTcOrigin:
    def test_program_time_map_identical_to_zero_origin_equivalent(self) -> None:
        """Two TC-origin clips with a cut (source gap) between them must
        produce the exact same ProgramTimeMap as the 0-origin equivalent
        (same has_cut / segment source_start / source_end / program_start).

        Regression test: verify that resolve_kept_ranges is the single
        relativization chokepoint, so downstream trim/atrim/retiming produce
        identical results regardless of TC origin (ADR-NI-1, architecture-report
        §2 "下流の修正不要").
        """
        tc_clip1 = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC + 0.0, duration=3.0
        )
        tc_clip2 = _make_tc_clip(
            "/src/a.mov", source_start=TC_START_SEC + 10.0, duration=2.0
        )
        tc_tl = _make_timeline_with_clips([tc_clip1, tc_clip2])

        plain_clip1 = _make_tc_clip(
            "/src/a.mov", source_start=0.0, duration=3.0, available_start=None
        )
        plain_clip2 = _make_tc_clip(
            "/src/a.mov", source_start=10.0, duration=2.0, available_start=None
        )
        plain_tl = _make_timeline_with_clips([plain_clip1, plain_clip2])

        tc_ranges = resolve_kept_ranges(tc_tl)
        plain_ranges = resolve_kept_ranges(plain_tl)

        tc_map = build_program_time_map(list(tc_ranges))
        plain_map = build_program_time_map(list(plain_ranges))

        assert tc_map.has_cut == plain_map.has_cut is True
        assert tc_map.has_warp == plain_map.has_warp
        assert len(tc_map.segments) == len(plain_map.segments) == 2
        for tc_seg, plain_seg in zip(tc_map.segments, plain_map.segments, strict=True):
            assert tc_seg.source_start == plain_seg.source_start
            assert tc_seg.source_end == plain_seg.source_end
            assert tc_seg.program_start == plain_seg.program_start
            assert tc_seg.time_scalar == plain_seg.time_scalar
