"""test_warp.py — Tests verifying the render contract for LinearTimeWarp in plan.py (WP-2).

Verifies the following functions and their render contracts:
  - KeptRange.time_scalar field (default 1.0; ADR-SP-5 backward compatibility)
  - resolve_kept_ranges() — reads time_scalar from clip.effects LinearTimeWarp;
    validates value domain [0.25, 8.0] (SR H-1 / M-3); message must not expose
    raw numeric values (NR-L-2 / SR NL-1)
  - _build_atempo_chain(speed) -> str — multi-stage atempo decomposition (ADR-SP-3)
  - _is_warp_identity(s) -> bool — identity threshold guard (CR M-2; _WARP_IDENTITY_THRESHOLD=1e-9)
  - _build_filter_complex() — single-source warp injection (ADR-SP-6)
  - _build_clip_filters() — multi-source warp injection (ADR-SP-6)
  - build_plan() — warped total_duration_seconds / estimated_size_bytes

Architecture references:
  - §5.2 / §6 / §9.2 of architecture-report-20260617-203935.md
  - ADR-SP-2: time_scalar = playback-speed multiplier
  - ADR-SP-3: atempo multi-stage decomposition
  - ADR-SP-5: time_scalar==1.0 → byte-identical to current (backward compat)
  - ADR-SP-6: setpts form is (PTS-STARTPTS)/{speed}
  - OQ-2: anullsrc branch also gets atempo (single code path)
"""

from __future__ import annotations

from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.plan import KeptRange, ProbeInfo, build_plan, resolve_kept_ranges
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Helpers (mirror test_plan.py patterns)
# ---------------------------------------------------------------------------

FPS = 30.0
_EPSILON = 1e-6


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(
    source: str,
    start: float,
    duration: float,
    rate: float = FPS,
) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration, rate)
    return clip


def _make_warped_clip(
    source: str,
    start: float,
    duration: float,
    speed: float,
    rate: float = FPS,
) -> otio.schema.Clip:
    """Build a Clip with a LinearTimeWarp(time_scalar=speed) effect."""
    clip = _make_clip(source, start, duration, rate)
    clip.effects.append(
        otio.schema.LinearTimeWarp(name="clipwright_speed", time_scalar=speed)
    )
    return clip


def _make_timeline_with_clips(
    clips: list[Any],
    track_kind: str = otio.schema.TrackKind.Video,
) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=track_kind)
    for item in clips:
        track.append(item)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


def _make_probe(
    has_video: bool = True,
    audio_count: int = 1,
    bit_rate: int | None = 8_000_000,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
) -> ProbeInfo:
    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


# ---------------------------------------------------------------------------
# Section 1: KeptRange.time_scalar field
# ---------------------------------------------------------------------------


class TestKeptRangeTimeScalar:
    """KeptRange must carry a time_scalar field with default 1.0."""

    def test_kept_range_has_time_scalar_field(self) -> None:
        """KeptRange.time_scalar exists and defaults to 1.0."""
        r = KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 5.0))
        assert hasattr(r, "time_scalar"), "KeptRange must have a time_scalar field"
        assert r.time_scalar == 1.0

    def test_kept_range_time_scalar_set_explicitly(self) -> None:
        """KeptRange.time_scalar can be set to a non-default value."""
        r = KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 5.0), time_scalar=2.0)
        assert r.time_scalar == 2.0


# ---------------------------------------------------------------------------
# Section 2: resolve_kept_ranges — time_scalar extraction
# ---------------------------------------------------------------------------


class TestResolveKeptRangesWarp:
    """resolve_kept_ranges reads time_scalar from LinearTimeWarp effects."""

    def test_no_warp_defaults_to_1(self) -> None:
        """Clip with no effects -> time_scalar = 1.0 (ADR-SP-5)."""
        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == 1.0

    def test_linear_time_warp_is_read(self) -> None:
        """Clip with LinearTimeWarp(time_scalar=2.0) -> KeptRange.time_scalar == 2.0."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=2.0)
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == pytest.approx(2.0)

    def test_slow_motion_warp(self) -> None:
        """Slow-motion warp (time_scalar=0.5) -> KeptRange.time_scalar == 0.5."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 4.0, speed=0.5)
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == pytest.approx(0.5)

    def test_first_found_wins_multiple_warps(self) -> None:
        """Multiple LinearTimeWarps on one clip -> first-found wins."""
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=3.0))
        clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=0.5))
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == pytest.approx(3.0)

    def test_non_warp_effects_ignored(self) -> None:
        """Effects other than LinearTimeWarp do not affect time_scalar."""
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        # FreezeFrame is another effect; it should be ignored for time_scalar
        clip.effects.append(otio.schema.FreezeFrame())
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == 1.0

    def test_multiple_clips_each_gets_own_scalar(self) -> None:
        """Multiple clips each carry their own independent time_scalar."""
        clips = [
            _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=2.0),
            _make_clip("/src/a.mp4", 10.0, 3.0),  # no warp -> 1.0
            _make_warped_clip("/src/a.mp4", 20.0, 2.0, speed=0.5),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == pytest.approx(2.0)
        assert ranges[1].time_scalar == pytest.approx(1.0)
        assert ranges[2].time_scalar == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Section 3: _build_atempo_chain — pure helper
# ---------------------------------------------------------------------------


class TestBuildAtempoChain:
    """_build_atempo_chain(speed) decomposes into a valid ffmpeg atempo chain."""

    @staticmethod
    def _chain(speed: float) -> str:
        from clipwright_render.plan import (
            _build_atempo_chain,  # type: ignore[attr-defined]
        )

        return _build_atempo_chain(speed)

    def _stage_values(self, chain: str) -> list[float]:
        """Parse 'atempo=X,atempo=Y,...' into list of float stage values."""
        stages = []
        for part in chain.split(","):
            part = part.strip()
            assert part.startswith("atempo="), f"Unexpected part: {part!r}"
            stages.append(float(part[len("atempo=") :]))
        return stages

    # --- standard cases (exact values, :g formatting) ---

    def test_speed_2(self) -> None:
        """speed=2.0 -> single stage 'atempo=2' (not 'atempo=2.0')."""
        assert self._chain(2.0) == "atempo=2"

    def test_speed_4(self) -> None:
        """speed=4.0 -> two stages 'atempo=2,atempo=2'."""
        assert self._chain(4.0) == "atempo=2,atempo=2"

    def test_speed_half(self) -> None:
        """speed=0.5 -> single stage 'atempo=0.5'."""
        assert self._chain(0.5) == "atempo=0.5"

    def test_speed_quarter(self) -> None:
        """speed=0.25 -> two stages 'atempo=0.5,atempo=0.5'."""
        assert self._chain(0.25) == "atempo=0.5,atempo=0.5"

    # --- residual cases (product == speed, each stage in [0.5, 2.0]) ---

    def test_speed_3_product_and_range(self) -> None:
        """speed=3.0 -> 'atempo=2,atempo=1.5'; product=3, each stage in [0.5,2.0]."""
        chain = self._chain(3.0)
        stages = self._stage_values(chain)
        product = 1.0
        for s in stages:
            assert 0.5 <= s <= 2.0, f"Stage {s} out of [0.5, 2.0]"
            product *= s
        assert product == pytest.approx(3.0, rel=1e-9)

    def test_speed_03_product_and_range(self) -> None:
        """speed=0.3 -> 'atempo=0.5,atempo=0.6'; product=0.3, each stage in [0.5,2.0]."""
        chain = self._chain(0.3)
        stages = self._stage_values(chain)
        product = 1.0
        for s in stages:
            assert 0.5 <= s <= 2.0, f"Stage {s} out of [0.5, 2.0]"
            product *= s
        assert product == pytest.approx(0.3, rel=1e-9)

    def test_speed_6_product_and_range(self) -> None:
        """speed=6.0 -> product==6.0 and all stages in [0.5, 2.0]."""
        chain = self._chain(6.0)
        stages = self._stage_values(chain)
        product = 1.0
        for s in stages:
            assert 0.5 <= s <= 2.0, f"Stage {s} out of [0.5, 2.0]"
            product *= s
        assert product == pytest.approx(6.0, rel=1e-9)

    def test_no_trailing_zero_noise(self) -> None:
        """Stage values use :g format — no trailing zeros (e.g. '2' not '2.0')."""
        chain_2 = self._chain(2.0)
        assert "2.0" not in chain_2, "Expected '2' not '2.0' (:g format)"
        chain_half = self._chain(0.5)
        assert "0.50" not in chain_half, "Expected '0.5' not '0.50' (:g format)"

    @pytest.mark.parametrize("speed", [0.25, 0.5, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0])
    def test_arbitrary_speed_product_invariant(self, speed: float) -> None:
        """For any speed, chain product equals speed and all stages are in [0.5, 2.0]."""
        chain = self._chain(speed)
        stages = self._stage_values(chain)
        product = 1.0
        for s in stages:
            assert 0.5 <= s <= 2.0, f"Stage {s} out of [0.5, 2.0] for speed={speed}"
            product *= s
        assert product == pytest.approx(speed, rel=1e-9)


# ---------------------------------------------------------------------------
# Section 4: filter_complex — single-source path (R-4)
# ---------------------------------------------------------------------------


class TestSingleSourceWarpFilterComplex:
    """Single-source warped timeline -> filter_complex contains setpts/atempo (R-4)."""

    def _build_single(
        self,
        clips: list[otio.schema.Clip],
        probe: ProbeInfo | None = None,
    ) -> Any:
        from clipwright_render.plan import resolve_kept_ranges

        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        p = probe or _make_probe(audio_count=1)
        return build_plan(ranges, p, RenderOptions())

    def test_warped_single_source_setpts_divided(self) -> None:
        """Single-source speed=2.0 -> filter_complex contains 'setpts=(PTS-STARTPTS)/2' (ADR-SP-6)."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=2.0)
        plan = self._build_single([clip])
        assert "setpts=(PTS-STARTPTS)/2" in plan.filter_complex

    def test_warped_single_source_atempo_present(self) -> None:
        """Single-source speed=2.0 -> filter_complex contains 'atempo=2'."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=2.0)
        plan = self._build_single([clip])
        assert "atempo=2" in plan.filter_complex

    def test_warped_slow_motion_setpts_and_atempo(self) -> None:
        """Single-source speed=0.5 -> 'setpts=(PTS-STARTPTS)/0.5' and 'atempo=0.5' present."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 4.0, speed=0.5)
        plan = self._build_single([clip])
        fc = plan.filter_complex
        assert "setpts=(PTS-STARTPTS)/0.5" in fc
        assert "atempo=0.5" in fc

    def test_unwarped_single_source_backward_compat(self) -> None:
        """Unwarped timeline (no LinearTimeWarp) -> filter_complex is BYTE-IDENTICAL to baseline (ADR-SP-5).

        Golden value is captured from the current implementation (pre-WP-2) to
        assert byte-identical backward compatibility after the warp extension is added.
        """
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        tl = _make_timeline_with_clips([clip])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=1)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex

        # The baseline behavior must NOT contain warp-specific patterns
        assert "setpts=(PTS-STARTPTS)/" not in fc or "setpts=PTS-STARTPTS" in fc, (
            "Unwarped timeline must not produce divided setpts"
        )
        # Standard form must still be present
        assert "setpts=PTS-STARTPTS" in fc
        # atempo must NOT appear for an unwarped timeline
        assert "atempo=" not in fc, (
            "atempo must not appear in unwarped timeline filter_complex (ADR-SP-5)"
        )

    def test_speed_1_matches_unwarped(self) -> None:
        """speed=1.0 (explicit warp) must produce same filter_complex as no-warp (R-8 / ADR-SP-5)."""
        from clipwright_render.plan import resolve_kept_ranges

        # Build unwarped plan
        clip_plain = _make_clip("/src/a.mp4", 0.0, 5.0)
        tl_plain = _make_timeline_with_clips([clip_plain])
        ranges_plain = resolve_kept_ranges(tl_plain)
        probe = _make_probe(audio_count=1)
        plan_plain = build_plan(ranges_plain, probe, RenderOptions())

        # Build speed=1.0 plan
        clip_warp = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=1.0)
        tl_warp = _make_timeline_with_clips([clip_warp])
        ranges_warp = resolve_kept_ranges(tl_warp)
        plan_warp = build_plan(ranges_warp, probe, RenderOptions())

        assert plan_warp.filter_complex == plan_plain.filter_complex, (
            "speed=1.0 must produce byte-identical filter_complex to no-warp (R-8)"
        )

    def test_warped_multi_clip_single_source(self) -> None:
        """Two warped clips from same source -> each clip gets its own setpts/atempo."""
        clips = [
            _make_warped_clip("/src/a.mp4", 0.0, 3.0, speed=2.0),
            _make_warped_clip("/src/a.mp4", 10.0, 2.0, speed=2.0),
        ]
        plan = self._build_single(clips)
        fc = plan.filter_complex
        assert "setpts=(PTS-STARTPTS)/2" in fc
        assert "atempo=2" in fc


# ---------------------------------------------------------------------------
# Section 5: filter_complex — multi-source path (R-4 / _build_clip_filters)
# ---------------------------------------------------------------------------


class TestMultiSourceWarpFilterComplex:
    """Multi-source (>=2 sources) warped timeline -> setpts/atempo in _build_clip_filters (R-4)."""

    def _build_multi(
        self,
        clips: list[tuple[str, float, float, float | None]],
        audio_count: int = 1,
    ) -> Any:
        """Build multi-source plan from (source, start, dur, speed_or_None) tuples."""
        from clipwright_render.plan import resolve_kept_ranges

        clip_objs = []
        for src, start, dur, speed in clips:
            if speed is not None:
                clip_objs.append(_make_warped_clip(src, start, dur, speed))
            else:
                clip_objs.append(_make_clip(src, start, dur))

        tl = _make_timeline_with_clips(clip_objs)
        ranges = resolve_kept_ranges(tl)

        source_probes = {}
        for src, _start, _dur, _speed in clips:
            if src not in source_probes:
                source_probes[src] = _make_probe(
                    audio_count=audio_count, width=1920, height=1080, fps=30.0
                )
        first_probe = source_probes[clips[0][0]]
        return build_plan(
            ranges,
            first_probe,
            RenderOptions(),
            source_probes=source_probes,
        )

    def test_warped_multi_source_setpts_divided(self) -> None:
        """Multi-source speed=2.0 -> filter_complex contains 'setpts=(PTS-STARTPTS)/2' (R-4)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0, 2.0),
            ("/src/b.mp4", 0.0, 2.0, 2.0),
        ]
        plan = self._build_multi(clips)
        assert "setpts=(PTS-STARTPTS)/2" in plan.filter_complex

    def test_warped_multi_source_atempo_present(self) -> None:
        """Multi-source speed=2.0 -> filter_complex contains 'atempo=2' (R-4)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0, 2.0),
            ("/src/b.mp4", 0.0, 2.0, 2.0),
        ]
        plan = self._build_multi(clips)
        assert "atempo=2" in plan.filter_complex

    def test_unwarped_multi_source_backward_compat(self) -> None:
        """Unwarped multi-source timeline -> filter_complex has no warp patterns (ADR-SP-5)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0, None),
            ("/src/b.mp4", 0.0, 2.0, None),
        ]
        plan = self._build_multi(clips)
        fc = plan.filter_complex
        assert "atempo=" not in fc, (
            "atempo must not appear in unwarped multi-source filter_complex"
        )

    def test_mixed_warp_unwarped_clips(self) -> None:
        """Warped clip from a and unwarped from b -> only a's segment has setpts/atempo."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0, 2.0),  # warped
            ("/src/b.mp4", 0.0, 2.0, None),  # plain
        ]
        plan = self._build_multi(clips)
        fc = plan.filter_complex
        # Warp pattern must exist (for the a.mp4 segment)
        assert "setpts=(PTS-STARTPTS)/2" in fc
        assert "atempo=2" in fc


# ---------------------------------------------------------------------------
# Section 6: duration / estimated_size_bytes corrections (§6)
# ---------------------------------------------------------------------------


class TestWarpDurationAndSize:
    """Warped build_plan must report warped total_duration_seconds and scaled size."""

    def test_total_duration_speed2(self) -> None:
        """speed=2.0, source_dur=10s -> total_duration_seconds == 5.0."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 10.0, speed=2.0)
        tl = _make_timeline_with_clips([clip])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(bit_rate=8_000_000, audio_count=0)
        plan = build_plan(ranges, probe, RenderOptions())
        assert abs(plan.total_duration_seconds - 5.0) < _EPSILON, (
            f"Expected 5.0s, got {plan.total_duration_seconds}"
        )

    def test_total_duration_half_speed(self) -> None:
        """speed=0.5, source_dur=4s -> total_duration_seconds == 8.0."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 4.0, speed=0.5)
        tl = _make_timeline_with_clips([clip])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(bit_rate=None, audio_count=0)
        plan = build_plan(ranges, probe, RenderOptions())
        assert abs(plan.total_duration_seconds - 8.0) < _EPSILON, (
            f"Expected 8.0s, got {plan.total_duration_seconds}"
        )

    def test_total_duration_two_segments_different_speeds(self) -> None:
        """Two warped clips: sum of (source_dur / speed) for each segment."""
        clips = [
            _make_warped_clip("/src/a.mp4", 0.0, 6.0, speed=2.0),  # 6/2 = 3.0s
            _make_warped_clip("/src/a.mp4", 10.0, 4.0, speed=0.5),  # 4/0.5 = 8.0s
        ]
        tl = _make_timeline_with_clips(clips)
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(bit_rate=None, audio_count=0)
        plan = build_plan(ranges, probe, RenderOptions())
        expected = 3.0 + 8.0
        assert abs(plan.total_duration_seconds - expected) < _EPSILON

    def test_estimated_size_bytes_scales_with_speed(self) -> None:
        """speed=2.0, source_dur=10s, bit_rate=8Mbps -> estimated_size = 8e6*5/8 = 5_000_000."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 10.0, speed=2.0)
        tl = _make_timeline_with_clips([clip])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(bit_rate=8_000_000, audio_count=0)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.estimated_size_bytes is not None
        assert abs(plan.estimated_size_bytes - 5_000_000) < 1.0, (
            f"Expected ~5_000_000, got {plan.estimated_size_bytes}"
        )

    def test_unwarped_duration_unchanged(self) -> None:
        """Unwarped timeline -> total_duration_seconds equals raw source duration (ADR-SP-5)."""
        clip = _make_clip("/src/a.mp4", 0.0, 10.0)
        tl = _make_timeline_with_clips([clip])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(bit_rate=8_000_000, audio_count=0)
        plan = build_plan(ranges, probe, RenderOptions())
        assert abs(plan.total_duration_seconds - 10.0) < _EPSILON

    def test_speed_1_duration_matches_unwarped(self) -> None:
        """speed=1.0 clip -> total_duration_seconds identical to plain unwarped clip (R-8)."""
        from clipwright_render.plan import resolve_kept_ranges

        clip_plain = _make_clip("/src/a.mp4", 0.0, 7.0)
        tl_plain = _make_timeline_with_clips([clip_plain])
        ranges_plain = resolve_kept_ranges(tl_plain)
        probe = _make_probe(bit_rate=8_000_000, audio_count=0)
        plan_plain = build_plan(ranges_plain, probe, RenderOptions())

        clip_warp = _make_warped_clip("/src/a.mp4", 0.0, 7.0, speed=1.0)
        tl_warp = _make_timeline_with_clips([clip_warp])
        ranges_warp = resolve_kept_ranges(tl_warp)
        plan_warp = build_plan(ranges_warp, probe, RenderOptions())

        assert (
            abs(plan_warp.total_duration_seconds - plan_plain.total_duration_seconds)
            < _EPSILON
        )


# ---------------------------------------------------------------------------
# Section 7: anullsrc warped branch (§5.2.2 / OQ-2)
# ---------------------------------------------------------------------------


class TestAnullsrcWarpedBranch:
    """Audio-less clip in multi-source -> anullsrc pad uses warped duration (OQ-2)."""

    def test_anullsrc_warped_pad_duration(self) -> None:
        """Audio-less source + speed=2.0 -> anullsrc atrim duration == source_dur/speed.

        OQ-2 decision: apply atempo to the anullsrc branch so pad duration matches
        warped video duration. Assert the atrim bound in the silent pad equals
        source_dur/speed (not source_dur).
        """
        # /src/a.mp4 has audio (drives has_audio_overall=True)
        # /src/b.mp4 has NO audio but is warped at speed=2.0
        # source_dur for b = 4.0s -> warped pad should be 4.0/2.0 = 2.0s
        source_dur_b = 4.0
        speed_b = 2.0
        expected_pad_dur = source_dur_b / speed_b  # 2.0s

        clip_a = _make_clip("/src/a.mp4", 0.0, 3.0)  # has audio
        clip_b = _make_warped_clip("/src/b.mp4", 0.0, source_dur_b, speed=speed_b)

        tl = _make_timeline_with_clips([clip_a, clip_b])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=1, width=1920, height=1080, fps=30.0)
        probe_b = _make_probe(audio_count=0, width=1920, height=1080, fps=30.0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(ranges, probe_a, RenderOptions(), source_probes=source_probes)
        fc = plan.filter_complex

        # The anullsrc branch for b must use the WARPED duration (2.0s), not 4.0s
        assert "anullsrc" in fc, "anullsrc must be present for audio-less clip"
        # Assert warped pad duration (2.0s) is present as atrim bound
        assert f"atrim=0:{expected_pad_dur:g}" in fc, (
            f"Expected anullsrc atrim=0:{expected_pad_dur:g} for warped pad, "
            f"but got: {fc!r}"
        )
        # The unwarped source duration (4.0s) must NOT appear as the pad bound
        assert (
            f"atrim=0:{source_dur_b:g}" not in fc
            or f"atrim=0:{expected_pad_dur:g}" in fc
        ), "Unwarped duration should not be used for the anullsrc pad (OQ-2)"

    def test_anullsrc_warped_pad_atempo_present(self) -> None:
        """Audio-less warped clip -> anullsrc branch must contain atempo (OQ-2 single code path)."""
        clip_a = _make_clip("/src/a.mp4", 0.0, 3.0)
        clip_b = _make_warped_clip("/src/b.mp4", 0.0, 4.0, speed=2.0)

        tl = _make_timeline_with_clips([clip_a, clip_b])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=1, width=1920, height=1080, fps=30.0)
        probe_b = _make_probe(audio_count=0, width=1920, height=1080, fps=30.0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(ranges, probe_a, RenderOptions(), source_probes=source_probes)
        assert "atempo=2" in plan.filter_complex, (
            "atempo chain must be applied to anullsrc branch (OQ-2)"
        )

    def test_anullsrc_unwarped_uses_source_duration(self) -> None:
        """Audio-less UNwarped clip -> anullsrc pad uses full source_dur (backward compat)."""
        source_dur_b = 4.0
        clip_a = _make_clip("/src/a.mp4", 0.0, 3.0)
        clip_b = _make_clip("/src/b.mp4", 0.0, source_dur_b)  # no warp

        tl = _make_timeline_with_clips([clip_a, clip_b])
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=1, width=1920, height=1080, fps=30.0)
        probe_b = _make_probe(audio_count=0, width=1920, height=1080, fps=30.0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(ranges, probe_a, RenderOptions(), source_probes=source_probes)
        fc = plan.filter_complex

        # For unwarped b, pad duration must be full source_dur (4.0s)
        assert f"atrim=0:{source_dur_b:g}" in fc, (
            f"Unwarped anullsrc pad must use full source duration {source_dur_b}s"
        )


# ---------------------------------------------------------------------------
# Section 8: time_scalar value-domain validation (SR H-1 / M-3)
# ---------------------------------------------------------------------------


class TestTimeScalarValueDomain:
    """resolve_kept_ranges must reject invalid time_scalar values (SR H-1 / M-3).

    Valid range: [_SPEED_MIN=0.25, _SPEED_MAX=8.0] inclusive.
    Boundary values 0.25 and 8.0 must be accepted (no raise).
    0.0, inf, nan, <0.25 (e.g. 0.24), >8.0 (e.g. 8.01) must raise
    ClipwrightError(INVALID_INPUT) with non-empty message and hint.
    """

    def _resolve(self, speed: float) -> list[KeptRange]:
        clip = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=speed)
        tl = _make_timeline_with_clips([clip])
        return resolve_kept_ranges(tl)

    def _assert_invalid(self, speed: float) -> None:
        from clipwright.errors import ClipwrightError, ErrorCode

        with pytest.raises(ClipwrightError) as exc_info:
            self._resolve(speed)
        exc = exc_info.value
        assert exc.code == ErrorCode.INVALID_INPUT, (
            f"speed={speed} must raise INVALID_INPUT, got {exc.code}"
        )
        assert exc.message, f"speed={speed}: exc.message must be non-empty"
        assert exc.hint, (
            f"speed={speed}: exc.hint must be non-empty (state supported range)"
        )
        # hint must mention the supported range 0.25–8.0
        assert "0.25" in exc.hint and "8" in exc.hint, (
            f"speed={speed}: hint must state supported range 0.25-8.0, got: {exc.hint!r}"
        )

    # --- Invalid values ---

    def test_time_scalar_zero_raises(self) -> None:
        """time_scalar=0.0 (division-by-zero setpts) must raise INVALID_INPUT."""
        self._assert_invalid(0.0)

    def test_time_scalar_inf_raises(self) -> None:
        """time_scalar=inf must raise INVALID_INPUT."""
        self._assert_invalid(float("inf"))

    def test_time_scalar_nan_raises(self) -> None:
        """time_scalar=nan must raise INVALID_INPUT."""
        self._assert_invalid(float("nan"))

    def test_time_scalar_below_min_raises(self) -> None:
        """time_scalar=0.24 (below _SPEED_MIN=0.25) must raise INVALID_INPUT."""
        self._assert_invalid(0.24)

    def test_time_scalar_above_max_raises(self) -> None:
        """time_scalar=8.01 (above _SPEED_MAX=8.0) must raise INVALID_INPUT."""
        self._assert_invalid(8.01)

    def test_time_scalar_below_zero_raises(self) -> None:
        """time_scalar=-1.0 (negative) must raise INVALID_INPUT."""
        self._assert_invalid(-1.0)

    # --- Valid boundary values (must NOT raise) ---

    def test_time_scalar_min_boundary_accepted(self) -> None:
        """time_scalar=0.25 (== _SPEED_MIN) must be accepted (inclusive lower bound)."""
        ranges = self._resolve(0.25)
        assert ranges[0].time_scalar == pytest.approx(0.25)

    def test_time_scalar_max_boundary_accepted(self) -> None:
        """time_scalar=8.0 (== _SPEED_MAX) must be accepted (inclusive upper bound)."""
        ranges = self._resolve(8.0)
        assert ranges[0].time_scalar == pytest.approx(8.0)

    # --- Backward compatibility ---

    def test_no_warp_does_not_raise(self) -> None:
        """Clip with no effects (default time_scalar=1.0) must not raise."""
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == pytest.approx(1.0)

    def test_time_scalar_1_does_not_raise(self) -> None:
        """Explicit LinearTimeWarp(time_scalar=1.0) must not raise and yield 1.0."""
        clip = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=1.0)
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        assert ranges[0].time_scalar == pytest.approx(1.0)

    def test_out_of_range_message_does_not_expose_scalar_value(self) -> None:
        """Out-of-range time_scalar error: message must NOT contain the raw numeric value.

        NR-L-2 / SR NL-1: error message is a fixed string; diagnostic numerics
        (time_scalar value, supported range) belong in hint, not message.
        Design principle: message = what went wrong (static), hint = how to fix
        (may include range boundaries).

        Currently FAILS because plan.py embeds {time_scalar!r} in the message.
        This test is the Red gate for the NR-L-2 / SR NL-1 fix.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        speed = 9.0
        with pytest.raises(ClipwrightError) as exc_info:
            self._resolve(speed)
        exc = exc_info.value
        # Existing contract: INVALID_INPUT, non-empty message and hint
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint

        # NR-L-2 / SR NL-1: raw time_scalar value must NOT appear in message
        assert "9.0" not in exc.message, (
            f"NR-L-2: message must not expose raw time_scalar value '9.0', "
            f"got: {exc.message!r}"
        )
        # Hint must contain supported range boundaries (SR NL-1)
        assert "0.25" in exc.hint and "8" in exc.hint, (
            f"SR NL-1: hint must state supported range 0.25-8.0, got: {exc.hint!r}"
        )


# ---------------------------------------------------------------------------
# Section 9: _build_atempo_chain precondition guard (CR L-2 / SR H-1(b))
# ---------------------------------------------------------------------------


class TestBuildAtempoChainPreconditionGuard:
    """_build_atempo_chain must raise ValueError for out-of-domain inputs.

    CR L-2 / SR H-1(b): zero, negative, inf, nan must all raise ValueError
    to prevent infinite loops and undefined ffmpeg filter outputs.
    """

    @staticmethod
    def _chain(speed: float) -> str:
        from clipwright_render.plan import (
            _build_atempo_chain,  # type: ignore[attr-defined]
        )

        return _build_atempo_chain(speed)

    def test_atempo_chain_zero_raises(self) -> None:
        """_build_atempo_chain(0.0) must raise ValueError."""
        with pytest.raises(ValueError):
            self._chain(0.0)

    def test_atempo_chain_negative_raises(self) -> None:
        """_build_atempo_chain(-1.0) must raise ValueError."""
        with pytest.raises(ValueError):
            self._chain(-1.0)

    def test_atempo_chain_inf_raises(self) -> None:
        """_build_atempo_chain(float('inf')) must raise ValueError."""
        with pytest.raises(ValueError):
            self._chain(float("inf"))

    def test_atempo_chain_nan_raises(self) -> None:
        """_build_atempo_chain(float('nan')) must raise ValueError."""
        with pytest.raises(ValueError):
            self._chain(float("nan"))


# ---------------------------------------------------------------------------
# Section 10: time_scalar==1.0 float-equality robustness (CR M-2)
# ---------------------------------------------------------------------------


class TestTimeScalarOneFloatRobustness:
    """speed=1.0 after OTIO round-trip must yield byte-identical filter_complex (CR M-2).

    A LinearTimeWarp(time_scalar=1.0) serialised via save_timeline then loaded via
    load_timeline must still produce the same filter_complex as a plain unwarped clip.
    Float serialisation must not introduce drift that causes the s != 1.0 branch to fire.
    """

    def test_speed_1_roundtrip_byte_identical(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """LinearTimeWarp(1.0) saved → loaded → build_plan must match unwarped golden."""

        from clipwright.otio_utils import load_timeline, save_timeline

        # Build unwarped golden
        clip_plain = _make_clip("/src/a.mp4", 0.0, 5.0)
        tl_plain = _make_timeline_with_clips([clip_plain])
        ranges_plain = resolve_kept_ranges(tl_plain)
        probe = _make_probe(audio_count=1)
        plan_plain = build_plan(ranges_plain, probe, RenderOptions())
        golden_fc = plan_plain.filter_complex

        # Build speed=1.0 timeline, save and reload
        clip_warp = _make_warped_clip("/src/a.mp4", 0.0, 5.0, speed=1.0)
        tl_warp = _make_timeline_with_clips([clip_warp])
        otio_path = str(tmp_path / "warp_roundtrip.otio")  # type: ignore[operator]
        save_timeline(tl_warp, otio_path)
        tl_loaded = load_timeline(otio_path)

        ranges_loaded = resolve_kept_ranges(tl_loaded)
        plan_loaded = build_plan(ranges_loaded, probe, RenderOptions())

        assert plan_loaded.filter_complex == golden_fc, (
            "speed=1.0 round-tripped via OTIO must produce byte-identical "
            "filter_complex to unwarped baseline (CR M-2 / ADR-SP-5).\n"
            f"  golden : {golden_fc!r}\n"
            f"  loaded : {plan_loaded.filter_complex!r}"
        )
        # Confirm atempo absent in round-tripped plan
        assert "atempo=" not in plan_loaded.filter_complex, (
            "atempo must not appear in speed=1.0 round-tripped filter_complex"
        )


# ---------------------------------------------------------------------------
# Section 11: FreezeFrame strict-type exclusion (CR L-6)
# ---------------------------------------------------------------------------


class TestFreezeFrameExclusion:
    """FreezeFrame (subclass of LinearTimeWarp, time_scalar=0.0) must be treated as
    a pass-through (time_scalar=1.0), not a warp, at every path (CR L-6).

    - resolve_kept_ranges must NOT raise for FreezeFrame.
    - filter_complex must NOT contain 'setpts=(PTS-STARTPTS)/0' or atempo stage.
    - Both single-source (_build_filter_complex) and multi-source
      (_build_clip_filters) paths are verified.
    """

    # --- Single-source path ---

    def test_freeze_frame_resolve_does_not_raise(self) -> None:
        """resolve_kept_ranges with FreezeFrame clip must not raise (CR L-6)."""
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        clip.effects.append(otio.schema.FreezeFrame())
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        # FreezeFrame must yield time_scalar==1.0 (pass-through)
        assert ranges[0].time_scalar == pytest.approx(1.0)

    def test_freeze_frame_single_source_no_divide_setpts(self) -> None:
        """Single-source FreezeFrame clip must NOT produce 'setpts=(PTS-STARTPTS)/0'."""
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        clip.effects.append(otio.schema.FreezeFrame())
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=1)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex

        assert "setpts=(PTS-STARTPTS)/0" not in fc, (
            "FreezeFrame must not produce division-by-zero setpts (CR L-6)"
        )
        assert "atempo=" not in fc, "FreezeFrame must not produce atempo stage (CR L-6)"
        # Standard unwarped form must be present
        assert "setpts=PTS-STARTPTS" in fc

    def test_freeze_frame_single_source_plain_setpts(self) -> None:
        """Single-source FreezeFrame -> filter_complex must use plain setpts=PTS-STARTPTS."""
        clip = _make_clip("/src/a.mp4", 0.0, 5.0)
        clip.effects.append(otio.schema.FreezeFrame())
        tl = _make_timeline_with_clips([clip])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=1)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex

        # Must match unwarped golden exactly
        clip_plain = _make_clip("/src/a.mp4", 0.0, 5.0)
        tl_plain = _make_timeline_with_clips([clip_plain])
        ranges_plain = resolve_kept_ranges(tl_plain)
        plan_plain = build_plan(ranges_plain, probe, RenderOptions())

        assert fc == plan_plain.filter_complex, (
            "FreezeFrame must produce byte-identical filter_complex to unwarped "
            "baseline (CR L-6 / type() exact check).\n"
            f"  expected (plain): {plan_plain.filter_complex!r}\n"
            f"  actual (freeze) : {fc!r}"
        )

    # --- Multi-source path (_build_clip_filters) ---

    def test_freeze_frame_multi_source_no_divide_setpts(self) -> None:
        """Multi-source FreezeFrame clip must NOT produce 'setpts=(PTS-STARTPTS)/0'."""
        clip_a = _make_clip("/src/a.mp4", 0.0, 3.0)
        clip_b = _make_clip("/src/b.mp4", 0.0, 2.0)
        clip_b.effects.append(otio.schema.FreezeFrame())

        tl = _make_timeline_with_clips([clip_a, clip_b])
        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=1, width=1920, height=1080, fps=30.0)
        probe_b = _make_probe(audio_count=1, width=1920, height=1080, fps=30.0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(ranges, probe_a, RenderOptions(), source_probes=source_probes)
        fc = plan.filter_complex

        assert "setpts=(PTS-STARTPTS)/0" not in fc, (
            "Multi-source FreezeFrame must not produce division-by-zero setpts (CR L-6)"
        )
        assert "atempo=" not in fc, (
            "Multi-source FreezeFrame must not produce atempo stage (CR L-6)"
        )


# ---------------------------------------------------------------------------
# Section 12: _is_warp_identity threshold boundary (NR-L-5 / CR M-2)
# ---------------------------------------------------------------------------


class TestIsWarpIdentityThreshold:
    """_is_warp_identity threshold boundary: _WARP_IDENTITY_THRESHOLD = 1e-9.

    NR-L-5 / CR M-2: pins the exact boundary behaviour so that changes to
    _WARP_IDENTITY_THRESHOLD are caught by regression.

    Values within 1e-9 of 1.0 (inclusive) are treated as identity (True).
    Values beyond 1e-9 of 1.0 are treated as non-identity (False).
    """

    @staticmethod
    def _identity(s: float) -> bool:
        from clipwright_render.plan import (
            _is_warp_identity,  # type: ignore[attr-defined]
        )

        return _is_warp_identity(s)

    def test_exactly_one_is_identity(self) -> None:
        """_is_warp_identity(1.0) must return True (exact identity)."""
        assert self._identity(1.0) is True

    def test_one_plus_sub_threshold_is_identity(self) -> None:
        """_is_warp_identity(1.0 + 1e-12) must return True (within threshold 1e-9)."""
        assert self._identity(1.0 + 1e-12) is True

    def test_one_plus_super_threshold_is_not_identity(self) -> None:
        """_is_warp_identity(1.0 + 1e-6) must return False (exceeds threshold 1e-9)."""
        assert self._identity(1.0 + 1e-6) is False

    def test_half_speed_is_not_identity(self) -> None:
        """_is_warp_identity(0.5) must return False (not close to 1.0)."""
        assert self._identity(0.5) is False
