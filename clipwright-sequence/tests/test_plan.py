"""test_plan.py — Tests for resolve_clip_specs (pure logic, no OTIO, no subprocess).

Covers the contract of clipwright_sequence.plan:
  - SourceProbe / ResolvedClip dataclasses (frozen)
  - resolve_clip_specs(probes, clips) -> (list[ResolvedClip], list[str] warnings)

All tests are expected to FAIL (Red phase) until impl-schemas-plan lands,
because clipwright_sequence.plan (and its symbols) do not yet exist.
Failure reason: ModuleNotFoundError / ImportError — not a test logic error.

Architecture references:
  - architecture-report-20260621-205501.md §3 ADR-SEQ-3
  - §V2.3 (DC-AS-003 tolerance: end within 1 frame is accepted and clipped)
  - §V2.6 (DC-AM-002 resolved-key probes)
"""

from __future__ import annotations

import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright_sequence.plan import (
    _EPSILON,
    ResolvedClip,
    SourceProbe,
    resolve_clip_specs,
)
from clipwright_sequence.schemas import SequenceClip

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

RATE = 30.0  # default test frame rate (fps)
DURATION = 60.0  # default test source duration (seconds)

# resolved absolute path used as probes dict key (DC-AM-002)
ABS_PATH = "/project/source.mp4"
ABS_PATH_B = "/project/source_b.mp4"


def _probe(
    abs_path: str = ABS_PATH,
    duration_sec: float = DURATION,
    rate: float = RATE,
    has_video: bool = True,
) -> SourceProbe:
    """Build a SourceProbe for tests."""
    return SourceProbe(
        abs_path=abs_path,
        duration_sec=duration_sec,
        rate=rate,
        has_video=has_video,
    )


def _clip(
    media: str = ABS_PATH,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> SequenceClip:
    """Build a SequenceClip for tests.

    For unit tests the media string equals the resolved abs_path
    so the probes dict can be keyed directly.
    """
    return SequenceClip(media=media, start_sec=start_sec, end_sec=end_sec)


def _probes(**kwargs: SourceProbe) -> dict[str, SourceProbe]:
    """Build probes dict from keyword args (key=abs_path, value=SourceProbe)."""
    return dict(kwargs)


# --------------------------------------------------------------------------- #
# SourceProbe dataclass
# --------------------------------------------------------------------------- #


class TestSourceProbe:
    """SourceProbe is a frozen dataclass with the required fields."""

    def test_fields_exist(self) -> None:
        """SourceProbe has abs_path, duration_sec, rate, has_video fields."""
        p = _probe()
        assert p.abs_path == ABS_PATH
        assert p.duration_sec == DURATION
        assert p.rate == RATE
        assert p.has_video is True

    def test_is_frozen(self) -> None:
        """SourceProbe is frozen — attribute assignment must raise."""
        p = _probe()
        with pytest.raises((AttributeError, TypeError)):
            p.abs_path = "/other/path.mp4"  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two SourceProbes with identical fields compare equal."""
        p1 = _probe()
        p2 = _probe()
        assert p1 == p2

    def test_inequality(self) -> None:
        """SourceProbes with different fields are not equal."""
        p1 = _probe(duration_sec=30.0)
        p2 = _probe(duration_sec=60.0)
        assert p1 != p2


# --------------------------------------------------------------------------- #
# ResolvedClip dataclass
# --------------------------------------------------------------------------- #


class TestResolvedClip:
    """ResolvedClip is a frozen dataclass with source/start_sec/end_sec/rate/index."""

    def test_fields_exist(self) -> None:
        """ResolvedClip has source, start_sec, end_sec, rate, index fields."""
        rc = ResolvedClip(
            source=ABS_PATH,
            start_sec=0.0,
            end_sec=30.0,
            rate=RATE,
            index=0,
        )
        assert rc.source == ABS_PATH
        assert rc.start_sec == 0.0
        assert rc.end_sec == 30.0
        assert rc.rate == RATE
        assert rc.index == 0

    def test_is_frozen(self) -> None:
        """ResolvedClip is frozen — attribute assignment must raise."""
        rc = ResolvedClip(
            source=ABS_PATH, start_sec=0.0, end_sec=30.0, rate=RATE, index=0
        )
        with pytest.raises((AttributeError, TypeError)):
            rc.index = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two ResolvedClips with identical fields compare equal."""
        rc1 = ResolvedClip(
            source=ABS_PATH, start_sec=0.0, end_sec=30.0, rate=RATE, index=0
        )
        rc2 = ResolvedClip(
            source=ABS_PATH, start_sec=0.0, end_sec=30.0, rate=RATE, index=0
        )
        assert rc1 == rc2


# --------------------------------------------------------------------------- #
# resolve_clip_specs — empty clips → INVALID_INPUT
# --------------------------------------------------------------------------- #


class TestEmptyClips:
    """Empty clips list is rejected with INVALID_INPUT (defensive layer)."""

    def test_empty_list_raises_invalid_input(self) -> None:
        """resolve_clip_specs with empty clips raises INVALID_INPUT."""
        probes: dict[str, SourceProbe] = {}
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, [])
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_empty_list_message_contains_no_clips(self) -> None:
        """Error message mentions that no clips were provided."""
        probes: dict[str, SourceProbe] = {}
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, [])
        # Message should mention "No clips" or similar (ADR-SEQ-3 §1)
        assert (
            "No clips" in exc_info.value.message
            or "no clips" in exc_info.value.message.lower()
        )


# --------------------------------------------------------------------------- #
# resolve_clip_specs — full-length defaulting
# --------------------------------------------------------------------------- #


class TestFullLengthDefaulting:
    """start_sec=None defaults to 0.0; end_sec=None defaults to probe.duration_sec."""

    def test_both_none_defaults_full_duration(self) -> None:
        """start_sec=None, end_sec=None → resolved (0.0, duration)."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=None, end_sec=None)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert len(resolved) == 1
        rc = resolved[0]
        assert rc.start_sec == 0.0
        assert rc.end_sec == DURATION

    def test_start_none_defaults_to_zero(self) -> None:
        """start_sec=None → 0.0; explicit end_sec is preserved."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=None, end_sec=30.0)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert resolved[0].start_sec == 0.0
        assert resolved[0].end_sec == 30.0

    def test_end_none_defaults_to_duration(self) -> None:
        """end_sec=None → probe.duration_sec; explicit start_sec is preserved."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=10.0, end_sec=None)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert resolved[0].start_sec == 10.0
        assert resolved[0].end_sec == DURATION

    def test_warnings_empty_for_default_case(self) -> None:
        """No warnings for a simple full-duration clip."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip()]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert warnings == []


# --------------------------------------------------------------------------- #
# resolve_clip_specs — range inversion
# --------------------------------------------------------------------------- #


class TestRangeInversion:
    """start_sec >= end_sec (after defaulting) raises INVALID_INPUT."""

    def test_start_equals_end_raises(self) -> None:
        """start_sec == end_sec after defaulting → INVALID_INPUT."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=30.0, end_sec=30.0)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_start_greater_than_end_raises(self) -> None:
        """start_sec > end_sec → INVALID_INPUT."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=40.0, end_sec=20.0)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_start_just_below_end_is_valid(self) -> None:
        """start_sec slightly below end_sec (> _EPSILON gap) is valid."""
        probes = {ABS_PATH: _probe()}
        # start is end - 1.0 (well above _EPSILON)
        clips = [_clip(start_sec=29.0, end_sec=30.0)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert len(resolved) == 1
        assert resolved[0].start_sec == pytest.approx(29.0)
        assert resolved[0].end_sec == pytest.approx(30.0)

    def test_start_within_epsilon_of_end_raises(self) -> None:
        """start_sec = end_sec - (_EPSILON / 2) is within _EPSILON of end → INVALID_INPUT.

        The inversion check is start >= end - _EPSILON, so a gap smaller than
        _EPSILON (1e-9) is treated as zero-length and must be rejected.
        """
        probes = {ABS_PATH: _probe()}
        # gap = _EPSILON / 2 < _EPSILON → treated as inverted
        gap = _EPSILON / 2
        clips = [_clip(start_sec=30.0 - gap, end_sec=30.0)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_range_inversion_message_mentions_start_end(self) -> None:
        """INVALID_INPUT message for range inversion mentions start_sec and end_sec."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=50.0, end_sec=10.0)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        msg = exc_info.value.message
        # Must mention the relationship (start >= end)
        assert "start_sec" in msg or "start" in msg


# --------------------------------------------------------------------------- #
# resolve_clip_specs — out-of-range (end > duration)
# --------------------------------------------------------------------------- #


class TestOutOfRange:
    """end_sec exceeding duration (beyond tolerance) raises INVALID_INPUT."""

    def test_end_beyond_duration_raises(self) -> None:
        """end_sec > duration + tolerance → INVALID_INPUT."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        # tolerance = max(_EPSILON, 1.0/RATE) = 1/30 ≈ 0.0333
        # set end_sec = DURATION + 1.0 >> tolerance
        clips = [_clip(start_sec=0.0, end_sec=DURATION + 1.0)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_end_beyond_duration_hint_mentions_omit(self) -> None:
        """Hint for out-of-range end_sec mentions omitting end_sec."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=0.0, end_sec=DURATION + 5.0)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        hint = exc_info.value.hint
        assert "omit" in hint.lower() or "end_sec" in hint

    def test_end_at_exactly_duration_is_valid(self) -> None:
        """end_sec == duration exactly is valid (no tolerance needed)."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=0.0, end_sec=DURATION)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert len(resolved) == 1
        assert resolved[0].end_sec == pytest.approx(DURATION)

    def test_end_slightly_beyond_duration_not_epsilon_raises(self) -> None:
        """end_sec just beyond 1 frame tolerance raises INVALID_INPUT."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        one_frame = 1.0 / RATE
        # 2 frames beyond duration — clearly outside tolerance
        clips = [_clip(start_sec=0.0, end_sec=DURATION + 2 * one_frame + 0.001)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------- #
# DC-AS-003: tolerance — end within 1 frame is accepted and clipped to duration
# --------------------------------------------------------------------------- #


class TestTolerance:
    """DC-AS-003: end within (duration, duration + 1/rate] is ACCEPTED.

    end is clipped to duration; NO warning is appended.
    This absorbs probe-measurement error (VFR / container header offset).
    """

    def test_end_within_one_frame_is_accepted(self) -> None:
        """end_sec = duration + (1/rate * 0.5) is accepted without error."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        half_frame = 0.5 / RATE
        clips = [_clip(start_sec=0.0, end_sec=DURATION + half_frame)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert len(resolved) == 1

    def test_end_within_one_frame_is_clipped_to_duration(self) -> None:
        """end_sec within tolerance is clipped to probe.duration_sec."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        half_frame = 0.5 / RATE
        clips = [_clip(start_sec=0.0, end_sec=DURATION + half_frame)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        # end_sec must be clipped to duration, not the raw input
        assert resolved[0].end_sec == pytest.approx(DURATION, abs=1e-9)

    def test_tolerance_no_warning_emitted(self) -> None:
        """Tolerance absorption does NOT emit a warning (probe-error absorption, not clamp)."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        half_frame = 0.5 / RATE
        clips = [_clip(start_sec=0.0, end_sec=DURATION + half_frame)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        # warnings must be empty — this is silent probe-error absorption
        assert warnings == []

    def test_end_at_exactly_one_frame_boundary_accepted(self) -> None:
        """end_sec = duration + exactly 1/rate is accepted (boundary inclusive)."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        one_frame = 1.0 / RATE
        clips = [_clip(start_sec=0.0, end_sec=DURATION + one_frame)]
        resolved, warnings = resolve_clip_specs(probes, clips)
        assert len(resolved) == 1
        assert resolved[0].end_sec == pytest.approx(DURATION, abs=1e-9)
        assert warnings == []

    def test_end_just_beyond_one_frame_raises(self) -> None:
        """end_sec = duration + 1/rate + small_delta raises INVALID_INPUT."""
        probes = {ABS_PATH: _probe(duration_sec=DURATION, rate=RATE)}
        one_frame = 1.0 / RATE
        clips = [_clip(start_sec=0.0, end_sec=DURATION + one_frame + 0.001)]
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_clip_specs(probes, clips)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------- #
# ResolvedClip fields — rate and index
# --------------------------------------------------------------------------- #


class TestResolvedClipFields:
    """rate comes from probe.rate; index is zero-based from enumeration order."""

    def test_rate_comes_from_probe(self) -> None:
        """ResolvedClip.rate equals the source's probe.rate."""
        custom_rate = 24.0
        probes = {ABS_PATH: _probe(rate=custom_rate)}
        clips = [_clip()]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert resolved[0].rate == pytest.approx(custom_rate)

    def test_index_zero_based_single_clip(self) -> None:
        """Single clip → index=0."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip()]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert resolved[0].index == 0

    def test_index_preserves_enumeration_order(self) -> None:
        """Multiple clips → indices 0, 1, 2 in input order."""
        probes = {
            ABS_PATH: _probe(abs_path=ABS_PATH),
            ABS_PATH_B: _probe(abs_path=ABS_PATH_B),
        }
        clips = [
            _clip(media=ABS_PATH, start_sec=0.0, end_sec=10.0),
            _clip(media=ABS_PATH_B, start_sec=5.0, end_sec=20.0),
            _clip(media=ABS_PATH, start_sec=20.0, end_sec=30.0),
        ]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert len(resolved) == 3
        assert resolved[0].index == 0
        assert resolved[1].index == 1
        assert resolved[2].index == 2

    def test_source_equals_probe_abs_path(self) -> None:
        """ResolvedClip.source equals the SourceProbe.abs_path (resolved key)."""
        probes = {ABS_PATH: _probe(abs_path=ABS_PATH)}
        clips = [_clip(media=ABS_PATH)]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert resolved[0].source == ABS_PATH


# --------------------------------------------------------------------------- #
# Same source twice — two distinct ResolvedClips in order
# --------------------------------------------------------------------------- #


class TestSameSourceTwice:
    """Same source with different ranges → two ResolvedClips in enumeration order."""

    def test_two_clips_same_source(self) -> None:
        """Two clips from the same source are returned as two separate ResolvedClips."""
        probes = {ABS_PATH: _probe()}
        clips = [
            _clip(media=ABS_PATH, start_sec=0.0, end_sec=10.0),
            _clip(media=ABS_PATH, start_sec=20.0, end_sec=40.0),
        ]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert len(resolved) == 2
        assert resolved[0].start_sec == pytest.approx(0.0)
        assert resolved[0].end_sec == pytest.approx(10.0)
        assert resolved[1].start_sec == pytest.approx(20.0)
        assert resolved[1].end_sec == pytest.approx(40.0)

    def test_same_source_indices_preserved(self) -> None:
        """Indices reflect position in input list even for same source."""
        probes = {ABS_PATH: _probe()}
        clips = [
            _clip(media=ABS_PATH, start_sec=0.0, end_sec=10.0),
            _clip(media=ABS_PATH, start_sec=20.0, end_sec=40.0),
        ]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert resolved[0].index == 0
        assert resolved[1].index == 1

    def test_three_clips_interleaved_sources(self) -> None:
        """Three clips with interleaved sources preserve enumeration order."""
        probes = {
            ABS_PATH: _probe(abs_path=ABS_PATH),
            ABS_PATH_B: _probe(abs_path=ABS_PATH_B),
        }
        clips = [
            _clip(media=ABS_PATH, start_sec=0.0, end_sec=5.0),
            _clip(media=ABS_PATH_B, start_sec=0.0, end_sec=5.0),
            _clip(media=ABS_PATH, start_sec=10.0, end_sec=15.0),
        ]
        resolved, _ = resolve_clip_specs(probes, clips)
        assert len(resolved) == 3
        assert resolved[0].source == ABS_PATH
        assert resolved[1].source == ABS_PATH_B
        assert resolved[2].source == ABS_PATH


# --------------------------------------------------------------------------- #
# warnings list
# --------------------------------------------------------------------------- #


class TestWarnings:
    """warnings is an empty list in v0.1.0 (no clamping performed)."""

    def test_warnings_is_list(self) -> None:
        """resolve_clip_specs returns a list as the second element."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip()]
        _, warnings = resolve_clip_specs(probes, clips)
        assert isinstance(warnings, list)

    def test_warnings_empty_for_valid_clips(self) -> None:
        """No warnings for well-formed clips in v0.1.0."""
        probes = {ABS_PATH: _probe()}
        clips = [_clip(start_sec=5.0, end_sec=30.0)]
        _, warnings = resolve_clip_specs(probes, clips)
        assert warnings == []

    def test_warnings_empty_for_multiple_clips(self) -> None:
        """No warnings when all clips are valid."""
        probes = {
            ABS_PATH: _probe(abs_path=ABS_PATH),
            ABS_PATH_B: _probe(abs_path=ABS_PATH_B),
        }
        clips = [
            _clip(media=ABS_PATH, start_sec=0.0, end_sec=10.0),
            _clip(media=ABS_PATH_B, start_sec=5.0, end_sec=15.0),
        ]
        _, warnings = resolve_clip_specs(probes, clips)
        assert warnings == []
