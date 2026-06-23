"""test_transition_chain.py — Tests for transition filter_complex in plan.py.

Target: _build_transition_chain and its integration with build_plan via the
`transition` keyword argument (ADR-RT-1..9).

All tests use build_plan directly; ffmpeg is never invoked (pure logic layer).
Follows the same filter_complex string-assert pattern as test_plan.py (concat=n=
pattern).
"""

from __future__ import annotations

import math
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import KeptRange, ProbeInfo
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FPS = 30.0
_EPSILON = 1e-6


# ---------------------------------------------------------------------------
# Helpers: timeline and range construction
# ---------------------------------------------------------------------------


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


def _make_timeline_with_clips(
    clips: list[otio.schema.Clip | otio.schema.Gap | otio.schema.Transition],
    track_kind: str = otio.schema.TrackKind.Video,
) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=track_kind)
    for item in clips:
        track.append(item)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


def _make_kept_range(
    source: str,
    start: float,
    duration: float,
    time_scalar: float = 1.0,
    rate: float = FPS,
) -> KeptRange:
    return KeptRange(
        source=source,
        source_range=_tr(start, duration, rate),
        time_scalar=time_scalar,
    )


def _make_probe(
    has_video: bool = True,
    audio_count: int = 1,
    bit_rate: int | None = 8_000_000,
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
) -> ProbeInfo:
    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _build_single_source_plan(
    clip_durations: list[float],
    has_audio: bool = True,
    transition: dict[str, Any] | None = None,
    time_scalars: list[float] | None = None,
    source: str = "/src/a.mp4",
    fps: float = FPS,
) -> Any:
    """Helper: build a plan for single-source clips via build_plan.

    clip_durations: list of clip source durations in seconds.
    time_scalars: per-clip time_scalar (default 1.0 for all).
    """
    from clipwright_render.plan import build_plan

    if time_scalars is None:
        time_scalars = [1.0] * len(clip_durations)

    ranges = [
        _make_kept_range(source, float(i) * 10.0, dur, ts, fps)
        for i, (dur, ts) in enumerate(zip(clip_durations, time_scalars, strict=True))
    ]
    probe = _make_probe(
        has_video=True,
        audio_count=1 if has_audio else 0,
        bit_rate=8_000_000,
        fps=fps,
    )
    return build_plan(
        ranges,
        probe,
        RenderOptions(),
        transition=transition,  # type: ignore[call-arg]
    )


def _build_multi_source_plan(
    clips: list[tuple[str, float, float]],
    has_audio: bool = True,
    transition: dict[str, Any] | None = None,
    fps: float = FPS,
) -> Any:
    """Helper: build a plan for multi-source clips via build_plan."""
    from clipwright_render.plan import build_plan

    ranges = [_make_kept_range(src, start, dur, 1.0, fps) for src, start, dur in clips]
    source_probes = {}
    for src, _start, _dur in clips:
        if src not in source_probes:
            source_probes[src] = _make_probe(
                has_video=True,
                audio_count=1 if has_audio else 0,
                fps=fps,
            )
    probe_first = source_probes[clips[0][0]]
    return build_plan(
        ranges,
        probe_first,
        RenderOptions(),
        source_probes=source_probes,
        transition=transition,  # type: ignore[call-arg]
    )


# ---------------------------------------------------------------------------
# Transition directive builders
# ---------------------------------------------------------------------------


def _uniform_transition(
    type_: str = "dissolve",
    duration_sec: float = 0.5,
    n_clips: int | None = None,
    *,
    after_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Build a minimal transition directive (per-boundary form)."""
    if after_indices is None:
        # uniform: generate all boundaries
        if n_clips is None:
            raise ValueError("n_clips required when after_indices is None")
        after_indices = list(range(n_clips - 1))
    transitions = [
        {"after_clip_index": i, "type": type_, "duration_sec": duration_sec}
        for i in sorted(after_indices)
    ]
    return {
        "tool": "clipwright_add_transition",
        "version": "0.1.0",
        "kind": "transition",
        "transitions": transitions,
    }


# ===========================================================================
# (1) xfade string: correct pattern and offset formula
# ===========================================================================


class TestXfadeChainString:
    """xfade string generation and offset accuracy (ADR-RT-3)."""

    def test_1a_single_boundary_xfade_string_present(self) -> None:
        """n=2 clips with transition → xfade= string appears in filter_complex."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "xfade=" in plan.filter_complex

    def test_1b_xfade_type_field(self) -> None:
        """xfade=transition=dissolve appears for dissolve type."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(type_="dissolve", n_clips=2),
        )
        assert "xfade=transition=dissolve" in plan.filter_complex

    def test_1c_xfade_type_fade(self) -> None:
        """xfade=transition=fade appears for fade type."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(type_="fade", n_clips=2),
        )
        assert "xfade=transition=fade" in plan.filter_complex

    def test_1d_xfade_duration_g_format(self) -> None:
        """duration is formatted with :g (trailing zeros stripped)."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        # :g for 0.5 → "0.5" (not "0.50000")
        assert "duration=0.5" in plan.filter_complex

    def test_1e_xfade_offset_6decimal_format(self) -> None:
        """offset is formatted with :.6f (6 decimal places)."""
        import re

        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        # offset=X.XXXXXX (6 decimal places)
        matches = re.findall(r"offset=(\d+\.\d+)", plan.filter_complex)
        assert len(matches) > 0, "No offset= found in filter_complex"
        for m in matches:
            decimal_part = m.split(".")[1]
            assert len(decimal_part) == 6, f"offset={m} does not have 6 decimal places"

    def test_1f_single_boundary_offset_value(self) -> None:
        """n=2 clips (3s each), d=0.5s: offset = prog_dur[0] - 0 - d = 3.0 - 0.5 = 2.5."""
        # offset(boundary at clip 0) = prog_cum(0) - overlap_cum_before - clamped_d
        #   prog_cum(0) = program_dur_0 = 3.0
        #   overlap_cum_before = 0  (no prior transition)
        #   clamped_d = 0.5  (no clamping; min(0.5, 3.0, 3.0) = 0.5)
        #   offset = 3.0 - 0 - 0.5 = 2.5
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        assert "offset=2.500000" in plan.filter_complex

    def test_1g_chain_3clips_two_boundaries(self) -> None:
        """n=3 clips with 2 boundaries → xfade appears twice."""
        import re

        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=3),
        )
        matches = re.findall(r"xfade=", plan.filter_complex)
        assert len(matches) == 2, f"Expected 2 xfade filters, got {len(matches)}"

    def test_1h_chain_3clips_offsets(self) -> None:
        """n=3 clips (3s each, d=0.5s):
        boundary 0: offset = 3.0 - 0 - 0.5 = 2.5
        boundary 1: offset = (3.0+3.0) - 0.5 - 0.5 = 5.0
        """
        # Formula (cumulative):
        #   prog_cum after clip 0 = 3.0
        #   overlap_cum before boundary 0 = 0
        #   offset_0 = 3.0 - 0 - 0.5 = 2.5
        #   overlap_cum = 0.5
        #   prog_cum after clip 1 = 3.0 + 3.0 = 6.0
        #   offset_1 = 6.0 - 0.5 - 0.5 = 5.0
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=3),
        )
        fc = plan.filter_complex
        assert "offset=2.500000" in fc
        assert "offset=5.000000" in fc

    def test_1i_intermediate_labels_xf_prefix(self) -> None:
        """Intermediate xfade output labels use [xf{i}] prefix."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=3),
        )
        # First boundary: outputs [xf0], second outputs [outv] (or [xf1] before rename)
        assert "[xf0]" in plan.filter_complex

    def test_1j_multi_source_two_clips_xfade(self) -> None:
        """Multi-source path: n=2 clips → xfade appears in filter_complex."""
        clips = [("/src/a.mp4", 0.0, 3.0), ("/src/b.mp4", 0.0, 3.0)]
        plan = _build_multi_source_plan(
            clips=clips,
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "xfade=" in plan.filter_complex

    def test_1k_multi_source_three_clips_two_xfades(self) -> None:
        """Multi-source path: n=3 clips → 2 xfade filters."""
        import re

        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 3.0),
            ("/src/a.mp4", 5.0, 3.0),
        ]
        plan = _build_multi_source_plan(
            clips=clips,
            has_audio=False,
            transition=_uniform_transition(n_clips=3),
        )
        matches = re.findall(r"xfade=", plan.filter_complex)
        assert len(matches) == 2


# ===========================================================================
# (2) acrossfade: has_audio and non-has_audio
# ===========================================================================


class TestAcrossfadeChain:
    """acrossfade string generation (ADR-RT-3)."""

    def test_2a_acrossfade_present_with_audio(self) -> None:
        """has_audio=True → acrossfade=d= is present in filter_complex."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=True,
            transition=_uniform_transition(n_clips=2),
        )
        assert "acrossfade=d=" in plan.filter_complex

    def test_2b_acrossfade_duration_matches_xfade(self) -> None:
        """acrossfade d= matches the xfade duration (0.5 → 'acrossfade=d=0.5')."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=True,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        assert "acrossfade=d=0.5" in plan.filter_complex

    def test_2c_acrossfade_absent_without_audio(self) -> None:
        """has_audio=False → acrossfade must NOT appear in filter_complex."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "acrossfade" not in plan.filter_complex

    def test_2d_intermediate_acf_labels(self) -> None:
        """Intermediate acrossfade output labels use [acf{i}] prefix."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0, 3.0],
            has_audio=True,
            transition=_uniform_transition(n_clips=3),
        )
        assert "[acf0]" in plan.filter_complex

    def test_2e_multi_source_with_audio_acrossfade(self) -> None:
        """Multi-source path with audio → acrossfade appears."""
        clips = [("/src/a.mp4", 0.0, 3.0), ("/src/b.mp4", 0.0, 3.0)]
        plan = _build_multi_source_plan(
            clips=clips,
            has_audio=True,
            transition=_uniform_transition(n_clips=2),
        )
        assert "acrossfade=d=" in plan.filter_complex

    def test_2f_multi_source_without_audio_no_acrossfade(self) -> None:
        """Multi-source path without audio → acrossfade must NOT appear."""
        clips = [("/src/a.mp4", 0.0, 3.0), ("/src/b.mp4", 0.0, 3.0)]
        plan = _build_multi_source_plan(
            clips=clips,
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "acrossfade" not in plan.filter_complex


# ===========================================================================
# (3) Terminal labels [outv] / [outa] — downstream compatibility
# ===========================================================================


class TestTerminalLabels:
    """Terminal labels [outv]/[outa] after xfade/acrossfade chain (ADR-RT-1)."""

    def test_3a_outv_present_video_only(self) -> None:
        """[outv] is present in filter_complex (video-only path)."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "[outv]" in plan.filter_complex

    def test_3b_outv_present_with_audio(self) -> None:
        """[outv] is present when has_audio=True."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=True,
            transition=_uniform_transition(n_clips=2),
        )
        assert "[outv]" in plan.filter_complex

    def test_3c_outa_present_with_audio(self) -> None:
        """[outa] is present when has_audio=True."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=True,
            transition=_uniform_transition(n_clips=2),
        )
        assert "[outa]" in plan.filter_complex

    def test_3d_outa_absent_without_audio(self) -> None:
        """[outa] is NOT present when has_audio=False."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "[outa]" not in plan.filter_complex

    def test_3e_concat_absent_when_transition(self) -> None:
        """When transition is present, concat= must NOT appear in filter_complex
        (xfade replaces concat; ADR-RT-1)."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        assert "concat=" not in plan.filter_complex

    def test_3f_ffmpeg_args_map_outv(self) -> None:
        """[outv] appears in ffmpeg_args -map (downstream pipeline unchanged)."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        args_str = " ".join(str(a) for a in plan.ffmpeg_args)
        assert "[outv]" in args_str

    def test_3g_ffmpeg_args_map_outa_with_audio(self) -> None:
        """[outa] appears in ffmpeg_args -map when has_audio=True."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=True,
            transition=_uniform_transition(n_clips=2),
        )
        args_str = " ".join(str(a) for a in plan.ffmpeg_args)
        assert "[outa]" in args_str

    def test_3h_multi_source_terminal_labels(self) -> None:
        """Multi-source path: [outv]/[outa] are present after xfade chain."""
        clips = [("/src/a.mp4", 0.0, 3.0), ("/src/b.mp4", 0.0, 3.0)]
        plan = _build_multi_source_plan(
            clips=clips,
            has_audio=True,
            transition=_uniform_transition(n_clips=2),
        )
        assert "[outv]" in plan.filter_complex
        assert "[outa]" in plan.filter_complex


# ===========================================================================
# (4) RenderPlan.total_duration_seconds correction (Σprogram_dur − Σclamped_d)
# ===========================================================================


class TestTotalDurationCorrection:
    """total_duration_seconds and BGM duration are corrected by Σclamped_d (ADR-RT-3)."""

    def test_4a_total_duration_reduced_by_d(self) -> None:
        """n=2 clips (3s+3s), d=0.5s → total=3+3-0.5=5.5s."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        assert abs(plan.total_duration_seconds - 5.5) < _EPSILON

    def test_4b_total_duration_two_boundaries(self) -> None:
        """n=3 clips (3s each), d=0.5s × 2 → total=3+3+3-1.0=8.0s."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=3),
        )
        assert abs(plan.total_duration_seconds - 8.0) < _EPSILON

    def test_4c_no_transition_total_duration_unchanged(self) -> None:
        """No transition → total_duration is Σprogram_dur (backward compat)."""
        plan_no_tr = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=None,
        )
        assert abs(plan_no_tr.total_duration_seconds - 6.0) < _EPSILON

    def test_4d_bgm_duration_also_corrected(self) -> None:
        """BGM target duration (total_duration_for_bgm) is also corrected by Σd.

        Note: RenderPlan may expose this as total_duration_for_bgm or embed it
        in BGM-related processing. We verify that the output video duration
        (total_duration_seconds) is corrected; BGM correction is tested via
        plan-level attributes when available.
        """
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        # Primary assertion: total_duration_seconds is corrected
        assert abs(plan.total_duration_seconds - 5.5) < _EPSILON
        # Secondary: if total_duration_for_bgm attribute exists, it must match
        if hasattr(plan, "total_duration_for_bgm"):
            assert abs(plan.total_duration_for_bgm - 5.5) < _EPSILON  # type: ignore[attr-defined]


# ===========================================================================
# (5) Duration clamping (ADR-RT-8)
# ===========================================================================


class TestDurationClamping:
    """Transition duration clamped when adjacent clip is shorter (ADR-RT-8)."""

    def test_5a_clamp_occurs_when_clip_shorter_than_d(self) -> None:
        """Clip shorter than d: clamped_d = min(d, prog_dur_i, prog_dur_{i+1}).

        clip_0=1.0s, clip_1=3.0s, d=2.0s → clamped_d=min(2.0,1.0,3.0)=1.0s
        """
        plan = _build_single_source_plan(
            clip_durations=[1.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=2.0, n_clips=2),
        )
        # A clamping warning must be present
        clamped_warnings = [w for w in plan.warnings if "clamped" in w.lower()]
        assert len(clamped_warnings) == 1, (
            f"Expected 1 clamping warning, got {clamped_warnings}"
        )

    def test_5b_clamp_warning_text(self) -> None:
        """Clamping warning mentions 'boundary after clip {i}'."""
        plan = _build_single_source_plan(
            clip_durations=[1.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=2.0, n_clips=2),
        )
        warning_text = " ".join(plan.warnings)
        assert "boundary after clip 0" in warning_text

    def test_5c_one_warning_per_boundary(self) -> None:
        """2 clamped boundaries → 2 clamping warnings."""
        # clip_0=0.5s, clip_1=0.5s, clip_2=3.0s, d=2.0s
        # boundary 0: min(2.0, 0.5, 0.5)=0.5s → clamped
        # boundary 1: min(2.0, 0.5, 3.0)=0.5s → clamped
        plan = _build_single_source_plan(
            clip_durations=[0.5, 0.5, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=2.0, n_clips=3),
        )
        clamped_warnings = [w for w in plan.warnings if "clamped" in w.lower()]
        assert len(clamped_warnings) == 2

    def test_5d_no_clamp_when_clip_long_enough(self) -> None:
        """Clips longer than d: no clamping warning is added."""
        plan = _build_single_source_plan(
            clip_durations=[5.0, 5.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
        )
        clamped_warnings = [w for w in plan.warnings if "clamped" in w.lower()]
        assert len(clamped_warnings) == 0

    def test_5e_clamped_duration_used_in_offset(self) -> None:
        """After clamping, offset uses clamped_d (not requested d).

        clip_0=1.0s, clip_1=3.0s, d=2.0s → clamped_d=1.0s
        offset = prog_cum(0) - 0 - clamped_d = 1.0 - 0 - 1.0 = 0.0
        """
        plan = _build_single_source_plan(
            clip_durations=[1.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=2.0, n_clips=2),
        )
        # offset=0.000000 because clamped_d=1.0s equals prog_cum(0)=1.0s
        assert "offset=0.000000" in plan.filter_complex

    def test_5f_total_duration_uses_clamped_d(self) -> None:
        """total_duration uses clamped_d (not requested d).

        clip_0=1.0s, clip_1=3.0s, d=2.0s → clamped_d=1.0s
        total=1.0+3.0-1.0=3.0s
        """
        plan = _build_single_source_plan(
            clip_durations=[1.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=2.0, n_clips=2),
        )
        assert abs(plan.total_duration_seconds - 3.0) < _EPSILON


# ===========================================================================
# (6) time_scalar (LinearTimeWarp) integration (ADR-RT-4)
# ===========================================================================


class TestTimeScalarIntegration:
    """program_dur = source_dur / time_scalar is used in offset calculation (ADR-RT-4)."""

    def test_6a_time_scalar_2x_halves_program_dur(self) -> None:
        """time_scalar=2.0 (2× speed): program_dur = source_dur / 2.0.

        clip_0: source=4.0s, scalar=2.0 → program=2.0s
        clip_1: source=4.0s, scalar=1.0 → program=4.0s
        d=0.5s
        offset = prog_dur[0] - 0 - d = 2.0 - 0.5 = 1.5
        total = 2.0 + 4.0 - 0.5 = 5.5s
        """
        plan = _build_single_source_plan(
            clip_durations=[4.0, 4.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
            time_scalars=[2.0, 1.0],
        )
        assert "offset=1.500000" in plan.filter_complex
        assert abs(plan.total_duration_seconds - 5.5) < _EPSILON

    def test_6b_time_scalar_0_5x_doubles_program_dur(self) -> None:
        """time_scalar=0.5 (0.5× speed): program_dur = source_dur / 0.5 = 2× source.

        clip_0: source=2.0s, scalar=0.5 → program=4.0s
        clip_1: source=2.0s, scalar=1.0 → program=2.0s
        d=0.5s
        offset = 4.0 - 0 - 0.5 = 3.5
        total = 4.0 + 2.0 - 0.5 = 5.5s
        """
        plan = _build_single_source_plan(
            clip_durations=[2.0, 2.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
            time_scalars=[0.5, 1.0],
        )
        assert "offset=3.500000" in plan.filter_complex
        assert abs(plan.total_duration_seconds - 5.5) < _EPSILON

    def test_6c_identity_scalar_unchanged(self) -> None:
        """time_scalar=1.0 → program_dur unchanged (identity, ADR-SP-5)."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(duration_sec=0.5, n_clips=2),
            time_scalars=[1.0, 1.0],
        )
        # Same as the non-warp case
        assert "offset=2.500000" in plan.filter_complex
        assert abs(plan.total_duration_seconds - 5.5) < _EPSILON


# ===========================================================================
# (7) retime interference warning (ADR-RT-6)
# ===========================================================================


class TestRetimeInterferenceWarning:
    """transition + text/image overlay marker → warning (ADR-RT-6)."""

    def _make_timeline_with_text_marker(
        self,
        source: str = "/src/a.mp4",
        clip_duration: float = 3.0,
    ) -> otio.schema.Timeline:
        """Build a single-clip timeline with a text_overlay marker."""
        tl = _make_timeline_with_clips([_make_clip(source, 0.0, clip_duration)])
        marker = otio.schema.Marker()
        marker.marked_range = _tr(0.5, 1.0)
        marker.metadata["clipwright"] = {
            "kind": "text_overlay",
            "text": "hello",
            "start_sec": 0.5,
            "duration_sec": 1.0,
            "x": "100",
            "y": "100",
            "font_size": 24,
            "font_color": "white",
            "box": False,
            "box_color": "black@0.5",
            "fade_in_sec": 0.0,
            "fade_out_sec": 0.0,
        }
        tl.tracks[0][0].markers.append(marker)
        return tl

    def test_7a_single_source_retime_warning(self) -> None:
        """Single-source: transition + text_overlay marker → retime warning."""
        from clipwright_render.plan import (
            KeptRangeList,
            build_plan,
            resolve_kept_ranges,
        )

        tl = self._make_timeline_with_text_marker(
            clip_duration=3.0,
        )
        # Add a second clip manually so n_clips=2
        clip2 = _make_clip("/src/a.mp4", 10.0, 3.0)
        tl.tracks[0].append(clip2)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(has_video=True, audio_count=1)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=_uniform_transition(n_clips=2),  # type: ignore[call-arg]
        )
        # At least one warning must mention transition + overlay timing
        retime_warnings = [
            w
            for w in plan.warnings
            if "transition" in w.lower()
            and (
                "overlay" in w.lower() or "timing" in w.lower() or "drift" in w.lower()
            )
        ]
        assert len(retime_warnings) >= 1, (
            f"No retime interference warning found. Warnings: {plan.warnings}"
        )

    def test_7b_multi_source_retime_warning(self) -> None:
        """Multi-source: transition + overlay marker → retime warning also fires.

        Uses timeline-based text_overlay marker (same approach as test_7a) so that
        font_path resolution is delegated to build_plan/_collect_text_overlays.
        Multi-source path is forced by having two clips with different source URLs.
        """
        from clipwright_render.plan import (
            KeptRangeList,
            build_plan,
            resolve_kept_ranges,
        )

        # Build a two-clip timeline where clip1 uses /src/a.mp4 and clip2 uses
        # /src/b.mp4.  This forces the multi-source path in build_plan.
        tl = self._make_timeline_with_text_marker(
            source="/src/a.mp4",
            clip_duration=3.0,
        )
        # Add a second clip with a different source → multi-source
        clip2 = _make_clip("/src/b.mp4", 0.0, 3.0)
        tl.tracks[0].append(clip2)

        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(has_video=True, audio_count=1, width=1920, height=1080)
        probe_b = _make_probe(has_video=True, audio_count=1, width=1920, height=1080)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}

        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
            transition=_uniform_transition(n_clips=2),  # type: ignore[call-arg]
        )
        retime_warnings = [
            w
            for w in plan.warnings
            if "transition" in w.lower()
            and (
                "overlay" in w.lower() or "timing" in w.lower() or "drift" in w.lower()
            )
        ]
        assert len(retime_warnings) >= 1, (
            f"No retime interference warning found. Warnings: {plan.warnings}"
        )

    def test_7c_no_warning_without_overlay(self) -> None:
        """No overlay marker → no retime interference warning."""
        plan = _build_single_source_plan(
            clip_durations=[3.0, 3.0],
            has_audio=False,
            transition=_uniform_transition(n_clips=2),
        )
        retime_warnings = [
            w
            for w in plan.warnings
            if "transition" in w.lower()
            and (
                "overlay" in w.lower() or "timing" in w.lower() or "drift" in w.lower()
            )
        ]
        assert len(retime_warnings) == 0


# ===========================================================================
# (8) Backward compatibility: transition=None/empty → concat unchanged
# ===========================================================================


class TestBackwardCompatibility:
    """Transition None/empty → filter_complex is byte-identical to no-transition (ADR-RT-1)."""

    def test_8a_none_produces_concat(self) -> None:
        """transition=None → concat= appears (backward compat)."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 10.0, 2.0),
        ]
        probe = _make_probe(has_video=True, audio_count=0)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=None,  # type: ignore[call-arg]
        )
        assert "concat=n=2" in plan.filter_complex

    def test_8b_none_filter_complex_identical_to_baseline(self) -> None:
        """transition=None → filter_complex is byte-identical to calling
        build_plan without the transition argument at all."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 10.0, 2.0),
        ]
        probe = _make_probe(has_video=True, audio_count=0)

        plan_baseline = build_plan(ranges, probe, RenderOptions())
        plan_none = build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=None,  # type: ignore[call-arg]
        )
        assert plan_baseline.filter_complex == plan_none.filter_complex

    def test_8c_empty_transitions_list_produces_concat(self) -> None:
        """transition directive with empty 'transitions' list → concat= (compat)."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 10.0, 2.0),
        ]
        probe = _make_probe(has_video=True, audio_count=0)
        empty_directive: dict[str, Any] = {
            "tool": "clipwright_add_transition",
            "version": "0.1.0",
            "kind": "transition",
            "transitions": [],
        }
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=empty_directive,  # type: ignore[call-arg]
        )
        assert "concat=" in plan.filter_complex
        assert "xfade=" not in plan.filter_complex

    def test_8d_multi_source_none_produces_concat(self) -> None:
        """Multi-source: transition=None → concat= appears (backward compat)."""
        clips = [("/src/a.mp4", 0.0, 3.0), ("/src/b.mp4", 0.0, 2.0)]
        plan = _build_multi_source_plan(clips=clips, has_audio=False, transition=None)
        assert "concat=" in plan.filter_complex
        assert "xfade=" not in plan.filter_complex

    def test_8e_single_clip_no_transition_unchanged(self) -> None:
        """Single clip, transition=None → concat=n=1 (DC-AS-005 backward compat)."""
        from clipwright_render.plan import build_plan

        ranges = [_make_kept_range("/src/a.mp4", 0.0, 5.0)]
        probe = _make_probe(has_video=True, audio_count=0)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=None,  # type: ignore[call-arg]
        )
        assert "concat=n=1" in plan.filter_complex


# ===========================================================================
# (9) Validation errors: per_boundary gaps and out-of-range (ADR-RT-5/RT-9)
# ===========================================================================


class TestTransitionValidationErrors:
    """_validate_transition rejects invalid directives (ADR-RT-5/RT-9)."""

    def _call_build(
        self,
        n_clips: int,
        after_indices: list[int],
        duration_sec: float = 0.5,
    ) -> Any:
        """Helper: call build_plan with per-boundary transitions at given indices."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", float(i) * 10.0, 3.0) for i in range(n_clips)
        ]
        probe = _make_probe(has_video=True, audio_count=0)
        directive = _uniform_transition(
            after_indices=after_indices,
            duration_sec=duration_sec,
        )
        return build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=directive,  # type: ignore[call-arg]
        )

    def test_9a_gapped_per_boundary_raises_unsupported(self) -> None:
        """Gaps in per_boundary (not all internal boundaries) → UNSUPPORTED_OPERATION."""
        # n=3 clips: valid indices are {0,1}. Provide only {0} → gap at 1.
        with pytest.raises(ClipwrightError) as exc_info:
            self._call_build(n_clips=3, after_indices=[0])
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_9b_gapped_per_boundary_hint_mentions_uniform(self) -> None:
        """UNSUPPORTED_OPERATION hint mentions uniform or all boundaries."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._call_build(n_clips=3, after_indices=[1])  # skip boundary 0
        hint = exc_info.value.hint.lower()
        assert "uniform" in hint or "all" in hint or "boundaries" in hint

    def test_9c_out_of_range_index_raises_invalid_input(self) -> None:
        """after_clip_index >= n_clips-1 → INVALID_INPUT."""
        # n=2 clips: valid index is {0}. Index 1 is out of range.
        with pytest.raises(ClipwrightError) as exc_info:
            self._call_build(n_clips=2, after_indices=[1])
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_9d_negative_after_index_raises_invalid_input(self) -> None:
        """after_clip_index < 0 → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._call_build(n_clips=2, after_indices=[-1])
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_9e_n_clips_lt2_with_transition_raises_invalid_input(self) -> None:
        """n_clips < 2 with transition directive → INVALID_INPUT (ADR-RT-9)."""
        from clipwright_render.plan import build_plan

        # Only 1 segment, but transition directive specifies boundary 0
        ranges = [_make_kept_range("/src/a.mp4", 0.0, 3.0)]
        probe = _make_probe(has_video=True, audio_count=0)
        directive: dict[str, Any] = {
            "tool": "clipwright_add_transition",
            "version": "0.1.0",
            "kind": "transition",
            "transitions": [
                {"after_clip_index": 0, "type": "dissolve", "duration_sec": 0.5}
            ],
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe,
                RenderOptions(),
                transition=directive,  # type: ignore[call-arg]
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_9f_invalid_type_raises_invalid_input(self) -> None:
        """Type not in allowlist → INVALID_INPUT (reader-side defence)."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 10.0, 3.0),
        ]
        probe = _make_probe(has_video=True, audio_count=0)
        directive: dict[str, Any] = {
            "tool": "clipwright_add_transition",
            "version": "0.1.0",
            "kind": "transition",
            "transitions": [
                {"after_clip_index": 0, "type": "wipe", "duration_sec": 0.5}
            ],
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe,
                RenderOptions(),
                transition=directive,  # type: ignore[call-arg]
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_9g_duration_zero_raises_invalid_input(self) -> None:
        """duration_sec=0 → INVALID_INPUT (gt=0 constraint)."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 10.0, 3.0),
        ]
        probe = _make_probe(has_video=True, audio_count=0)
        directive: dict[str, Any] = {
            "tool": "clipwright_add_transition",
            "version": "0.1.0",
            "kind": "transition",
            "transitions": [
                {"after_clip_index": 0, "type": "dissolve", "duration_sec": 0.0}
            ],
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe,
                RenderOptions(),
                transition=directive,  # type: ignore[call-arg]
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_9h_duration_exceeds_5s_raises_invalid_input(self) -> None:
        """duration_sec > 5.0 → INVALID_INPUT (le=5.0 constraint)."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 10.0),
            _make_kept_range("/src/a.mp4", 20.0, 10.0),
        ]
        probe = _make_probe(has_video=True, audio_count=0)
        directive: dict[str, Any] = {
            "tool": "clipwright_add_transition",
            "version": "0.1.0",
            "kind": "transition",
            "transitions": [
                {"after_clip_index": 0, "type": "dissolve", "duration_sec": 5.1}
            ],
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe,
                RenderOptions(),
                transition=directive,  # type: ignore[call-arg]
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_9i_multi_source_gapped_per_boundary_raises_unsupported(self) -> None:
        """Multi-source path: gapped per_boundary → UNSUPPORTED_OPERATION."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/b.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 5.0, 3.0),
        ]
        probe_a = _make_probe(has_video=True, audio_count=0, width=1920, height=1080)
        probe_b = _make_probe(has_video=True, audio_count=0, width=1920, height=1080)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}

        # Gap: indices {0} only (should be {0, 1} for n=3)
        directive = _uniform_transition(after_indices=[0])
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe_a,
                RenderOptions(),
                source_probes=source_probes,
                transition=directive,  # type: ignore[call-arg]
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION
