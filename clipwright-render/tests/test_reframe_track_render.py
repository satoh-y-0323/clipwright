"""test_reframe_track_render.py — Tests for mode='track' render path.

Design context (DC-AS-007 / ADR-T10 confusion guard):
    AC-06 backward compat (existing mode='crop' = legacy scale-first crop) and
    the track-center fallback (crop-from-source) INTENTIONALLY produce different
    pixel output.  They are distinct code paths.  Tests in this file NEVER assert
    that mode='crop' output equals track-center fallback output.
    The two paths must NOT be conflated (ADR-T10).

Architecture reference: architecture-report-20260625-164805.md §3/§5/§9
Plan reference: wave2-C task assignment (impl_render).
N_max decision: 80 (spike result, confirmed by parent; overrides arch-report 120).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright_render.plan import (  # type: ignore[attr-defined]
    _RenderCentreKeyframe,
    _RenderReframe,
    _append_reframe_filter,
    _build_track_crop_expr,
    _validate_reframe,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TW = 1080  # target width (9:16 portrait)
_TH = 1920  # target height
_TW_LS = 1920  # landscape target width
_TH_LS = 1080  # landscape target height

# Source dimensions: 1920×1080 landscape source → portrait target
_SRC_W = 1920
_SRC_H = 1080

# Source dimensions: 1080×1920 portrait source → portrait target (same orientation)
_SRC_W_PORT = 1080
_SRC_H_PORT = 1920

# Aspect tolerance ε = 2 / min(tw, th) — DC-AS-002 / AC-04b
_ASPECT_EPS_PORTRAIT = 2.0 / min(_TW, _TH)
_ASPECT_EPS_LANDSCAPE = 2.0 / min(_TW_LS, _TH_LS)

# N_max confirmed by spike (overrides arch-report default of 120)
_N_MAX = 80

# Standard video input label in filter pipeline
_IN_LABEL = "[outv]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track_reframe(
    tw: int = _TW,
    th: int = _TH,
    track: list[dict[str, float]] | None = None,
    anchor: str = "center",
    pad_color: str = "black",
) -> _RenderReframe:
    """Build a _RenderReframe with mode='track' and the given keyframe list."""
    kwargs: dict[str, Any] = {
        "target_w": tw,
        "target_h": th,
        "mode": "track",
        "anchor": anchor,
        "pad_color": pad_color,
    }
    if track is not None:
        kwargs["track"] = track
    return _RenderReframe(**kwargs)  # type: ignore[call-arg]


def _run_track_filter(
    reframe: _RenderReframe,
    src_w: int = _SRC_W,
    src_h: int = _SRC_H,
    label: str = _IN_LABEL,
) -> tuple[list[str], str]:
    """Call _append_reframe_filter for track mode and return (parts, terminal_label)."""
    parts: list[str] = []
    result_label = _append_reframe_filter(
        parts, label, reframe, src_w=src_w, src_h=src_h
    )
    return parts, result_label


def _cw_ch_from_filter(seg: str) -> tuple[int, int]:
    """Extract cw and ch from a 'crop=cw:ch:...' filter segment."""
    m = re.search(r"crop=(\d+):(\d+):", seg)
    assert m is not None, f"crop=cw:ch not found in: {seg!r}"
    return int(m.group(1)), int(m.group(2))


def _make_track_directive(
    tw: int = _TW,
    th: int = _TH,
    keyframes: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Build a raw D3 directive dict with mode='track'."""
    return {
        "tool": "clipwright-reframe",
        "version": "0.3.0",
        "kind": "reframe",
        "target_w": tw,
        "target_h": th,
        "mode": "track",
        "anchor": "center",
        "pad_color": "black",
        "track": keyframes
        if keyframes is not None
        else [{"t_s": 0.0, "cx": 0.5, "cy": 0.5}],
    }


# ---------------------------------------------------------------------------
# AC-04 — track フィルタ: crop-from-source 基本構造
# ---------------------------------------------------------------------------


class TestTrackFilterBasicStructure:
    """AC-04: mode='track' with a non-empty track produces crop-from-source filter.

    The generated filter must match:
        {in_label}crop={cw}:{ch}:'{x_expr}':'{y_expr}',scale={tw}:{th},setsar=1[outvrf]

    Architecture §3.4 (crop-from-source single path, DC-AS-001).
    """

    def test_track_mode_returns_outvrf_label(self) -> None:
        """mode='track' → terminal label is '[outvrf]'."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        _parts, label = _run_track_filter(reframe)
        assert label == "[outvrf]"

    def test_track_mode_single_segment(self) -> None:
        """mode='track' → exactly one segment appended (single-segment rule §3.1)."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        assert len(parts) == 1

    def test_track_segment_no_semicolon(self) -> None:
        """track segment must not contain ';' (individual-segment rule §3.1)."""
        reframe = _make_track_reframe(
            track=[
                {"t_s": 0.0, "cx": 0.3, "cy": 0.4},
                {"t_s": 1.0, "cx": 0.7, "cy": 0.6},
            ]
        )
        parts, _ = _run_track_filter(reframe)
        assert ";" not in parts[0]

    def test_track_segment_starts_with_input_label(self) -> None:
        """track segment starts with the input video_map_label."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        assert parts[0].startswith(_IN_LABEL)

    def test_track_segment_ends_with_outvrf(self) -> None:
        """track segment ends with '[outvrf]'."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        assert parts[0].endswith("[outvrf]")

    def test_track_segment_contains_crop_filter(self) -> None:
        """track segment contains crop=cw:ch:'x_expr':'y_expr' (§3.4)."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        # crop= must be present with single-quoted x/y expressions
        assert "crop=" in parts[0]

    def test_track_segment_contains_scale(self) -> None:
        """track segment contains scale={tw}:{th} after crop (crop-from-source)."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        assert f"scale={_TW}:{_TH}" in parts[0]

    def test_track_segment_contains_setsar_1(self) -> None:
        """track segment ends chain with setsar=1 (§3.4)."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        assert "setsar=1" in parts[0]

    def test_track_segment_no_iw_in_crop_args(self) -> None:
        """x/y in crop must be numeric (no 'iw' / 'ih' tokens) — DC-AS-001/§3.2."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe)
        seg = parts[0]
        # A simpler check: 'iw' and 'ih' must not appear in the segment at all.
        assert "iw" not in seg, f"'iw' found in track filter segment: {seg!r}"
        assert "ih" not in seg, f"'ih' found in track filter segment: {seg!r}"

    def test_track_n2_segment_contains_min_max(self) -> None:
        """N>=2 track → x_expr/y_expr contains 'min(max(' piecewise ramp (§3.3)."""
        reframe = _make_track_reframe(
            track=[
                {"t_s": 0.0, "cx": 0.3, "cy": 0.4},
                {"t_s": 2.0, "cx": 0.7, "cy": 0.6},
            ]
        )
        parts, _ = _run_track_filter(reframe)
        seg = parts[0]
        assert "min(max(" in seg, f"'min(max(' not found in: {seg!r}"

    def test_track_crop_origin_clamped_portrait_source(self) -> None:
        """Pixel origins x0,y0 must lie in [0, src_w-cw] / [0, src_h-ch] (§3.2).

        For a single-keyframe (N=1) track, x_expr is a constant integer.
        We extract cw,ch from the segment and verify the constant is in range.
        """
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W, src_h=_SRC_H)
        seg = parts[0]
        cw, ch = _cw_ch_from_filter(seg)
        # N=1 → x_expr is str(x0), y_expr is str(y0).
        # Extract the constants from crop=cw:ch:'x0':'y0'
        m = re.search(r"crop=\d+:\d+:'(\d+)':'(\d+)'", seg)
        assert m is not None, f"crop with constant origins not found in: {seg!r}"
        x0 = int(m.group(1))
        y0 = int(m.group(2))
        assert 0 <= x0 <= _SRC_W - cw, f"x0={x0} out of [0, {_SRC_W - cw}]"
        assert 0 <= y0 <= _SRC_H - ch, f"y0={y0} out of [0, {_SRC_H - ch}]"


# ---------------------------------------------------------------------------
# AC-04b — アスペクト: cw:ch が tw:th に一致（歪みなし DC-AS-002）
# ---------------------------------------------------------------------------


class TestTrackFilterAspect:
    """AC-04b: cw/ch satisfies |cw/ch - tw/th| <= ε = 2/min(tw,th).

    Portrait and landscape sources both tested.
    """

    def test_portrait_source_landscape_target_aspect(self) -> None:
        """Landscape target (1920:1080) from landscape source: |cw/ch - tw/th| <= ε."""
        reframe = _make_track_reframe(
            tw=_TW_LS, th=_TH_LS, track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]
        )
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W, src_h=_SRC_H)
        cw, ch = _cw_ch_from_filter(parts[0])
        ratio_cw_ch = cw / ch
        ratio_tw_th = _TW_LS / _TH_LS
        eps = _ASPECT_EPS_LANDSCAPE
        assert abs(ratio_cw_ch - ratio_tw_th) <= eps, (
            f"|cw/ch - tw/th| = {abs(ratio_cw_ch - ratio_tw_th):.6f} > ε={eps:.6f} "
            f"(cw={cw}, ch={ch})"
        )

    def test_landscape_source_portrait_target_aspect(self) -> None:
        """Portrait target (1080:1920) from landscape source: |cw/ch - tw/th| <= ε."""
        reframe = _make_track_reframe(
            tw=_TW, th=_TH, track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]
        )
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W, src_h=_SRC_H)
        cw, ch = _cw_ch_from_filter(parts[0])
        ratio_cw_ch = cw / ch
        ratio_tw_th = _TW / _TH
        eps = _ASPECT_EPS_PORTRAIT
        assert abs(ratio_cw_ch - ratio_tw_th) <= eps, (
            f"|cw/ch - tw/th| = {abs(ratio_cw_ch - ratio_tw_th):.6f} > ε={eps:.6f} "
            f"(cw={cw}, ch={ch})"
        )

    def test_portrait_source_portrait_target_aspect(self) -> None:
        """Portrait target (1080:1920) from portrait source (1080:1920): ε-check."""
        reframe = _make_track_reframe(
            tw=_TW, th=_TH, track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]
        )
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W_PORT, src_h=_SRC_H_PORT)
        cw, ch = _cw_ch_from_filter(parts[0])
        ratio_cw_ch = cw / ch
        ratio_tw_th = _TW / _TH
        eps = _ASPECT_EPS_PORTRAIT
        assert abs(ratio_cw_ch - ratio_tw_th) <= eps, (
            f"|cw/ch - tw/th| = {abs(ratio_cw_ch - ratio_tw_th):.6f} > ε={eps:.6f} "
            f"(cw={cw}, ch={ch})"
        )

    def test_cw_ch_are_even(self) -> None:
        """cw and ch must both be even numbers (yuv420p constraint, DC-AS-002)."""
        reframe = _make_track_reframe(
            tw=_TW, th=_TH, track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]
        )
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W, src_h=_SRC_H)
        cw, ch = _cw_ch_from_filter(parts[0])
        assert cw % 2 == 0, f"cw={cw} is not even"
        assert ch % 2 == 0, f"ch={ch} is not even"


# ---------------------------------------------------------------------------
# AC-04c — ゼロ除算: フォーマット後分母 dt が非ゼロ（DC-AM-005）
# ---------------------------------------------------------------------------


class TestTrackFilterNoDivisionByZero:
    """AC-04c: dt in each ramp term must not be '0', '0.0', or all-zero fixed-point.

    track_cli guarantees min interval >= 1/fps, but render asserts format-after.
    This test checks the formatted string directly.
    """

    # Patterns that represent zero (:.4f format → "0.0000" for dt=0)
    _ZERO_DT_PATTERN = re.compile(r"/(?:0\.0+|0+)(?:[^0-9.]|$)")

    def test_n2_no_zero_denominator(self) -> None:
        """Two keyframes at t=0 and t=2 → dt=2.0 → denominator is not zero."""
        reframe = _make_track_reframe(
            track=[
                {"t_s": 0.0, "cx": 0.3, "cy": 0.4},
                {"t_s": 2.0, "cx": 0.7, "cy": 0.6},
            ]
        )
        parts, _ = _run_track_filter(reframe)
        seg = parts[0]
        # Denominator in ramp terms: /dt_s — must not be zero
        assert not self._ZERO_DT_PATTERN.search(seg), (
            f"Zero denominator pattern found in: {seg!r}"
        )

    def test_n3_no_zero_denominator(self) -> None:
        """Three keyframes with varying dt → all denominators non-zero."""
        reframe = _make_track_reframe(
            track=[
                {"t_s": 0.0, "cx": 0.2, "cy": 0.3},
                {"t_s": 1.0, "cx": 0.5, "cy": 0.5},
                {"t_s": 3.0, "cx": 0.8, "cy": 0.7},
            ]
        )
        parts, _ = _run_track_filter(reframe)
        seg = parts[0]
        assert not self._ZERO_DT_PATTERN.search(seg), (
            f"Zero denominator pattern found in: {seg!r}"
        )


# ---------------------------------------------------------------------------
# 退化ケース (N=0/1/2, 全 dx==0)
# ---------------------------------------------------------------------------


class TestTrackFilterDegenerate:
    """Degenerate cases: N=0/1/2 and all-dx-zero (§3.3 §5 §9)."""

    def test_n1_x_expr_is_constant(self) -> None:
        """N=1 track → x_expr and y_expr are constant integers (no ramp terms).

        Architecture §3.3: 'N == 1: x_expr = str(x[0])'
        """
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W, src_h=_SRC_H)
        seg = parts[0]
        # N=1: no 'min(max(' in crop x/y (it's a constant expression)
        assert "min(max(" not in seg, (
            f"N=1 should produce constant x/y, but found min(max(: {seg!r}"
        )

    def test_n2_has_ramp_terms(self) -> None:
        """N=2 track → exactly one ramp term (one dx entry) in x_expr."""
        reframe = _make_track_reframe(
            track=[
                {"t_s": 0.0, "cx": 0.2, "cy": 0.3},
                {"t_s": 2.0, "cx": 0.8, "cy": 0.7},
            ]
        )
        parts, _ = _run_track_filter(reframe)
        seg = parts[0]
        assert "min(max(" in seg, f"N=2 should produce ramp term: {seg!r}"

    def test_all_dx_zero_produces_constant_expr(self) -> None:
        """All keyframes at the same position → constant x/y (dx==0 items omitted).

        Architecture §3.3: 'if dx == 0: continue' — all-zero dx ⇒ no ramp terms.
        """
        reframe = _make_track_reframe(
            track=[
                {"t_s": 0.0, "cx": 0.5, "cy": 0.5},
                {"t_s": 1.0, "cx": 0.5, "cy": 0.5},
                {"t_s": 2.0, "cx": 0.5, "cy": 0.5},
            ]
        )
        parts, _ = _run_track_filter(reframe)
        seg = parts[0]
        assert "min(max(" not in seg, (
            f"All-dx-zero should produce constant x/y, but found ramp: {seg!r}"
        )


# ---------------------------------------------------------------------------
# AC-05 — 空 track フォールバック (track=[] or None)
# ---------------------------------------------------------------------------


class TestTrackEmptyFallback:
    """AC-05: track=[] or track=None with mode='track' → static centre crop fallback.

    Fallback: _append_reframe_filter synthesises track=[{t_s:0,cx:0.5,cy:0.5}]
    internally.  Output is ok:true; warning indicates fallback (architecture §5).
    The fallback path is crop-from-source (NOT scale-first mode='crop').

    Design note (DC-AS-007 / ADR-T10):
        The static-centre fallback (crop-from-source) and legacy mode='crop'
        (scale-first) are SEPARATE paths that produce different pixel output.
        They must not be conflated.  This class tests the crop-from-source path.
    """

    def test_empty_track_list_produces_filter(self) -> None:
        """track=[] → filter is still produced (ok, not INVALID_INPUT)."""
        reframe = _make_track_reframe(track=[])
        parts, label = _run_track_filter(reframe)
        assert len(parts) == 1
        assert label == "[outvrf]"

    def test_empty_track_filter_contains_crop(self) -> None:
        """track=[] → crop filter in segment (crop-from-source centre fallback)."""
        reframe = _make_track_reframe(track=[])
        parts, _ = _run_track_filter(reframe)
        assert "crop=" in parts[0]

    def test_empty_track_filter_no_min_max_ramp(self) -> None:
        """track=[] fallback → N=1 constant (no ramp terms)."""
        reframe = _make_track_reframe(track=[])
        parts, _ = _run_track_filter(reframe)
        assert "min(max(" not in parts[0]

    def test_none_track_produces_filter(self) -> None:
        """track=None → filter is produced (centre fallback)."""
        reframe = _make_track_reframe(track=None)
        parts, label = _run_track_filter(reframe)
        assert len(parts) == 1
        assert label == "[outvrf]"

    def test_empty_track_crop_origin_is_centre(self) -> None:
        """track=[] → constant x0,y0 correspond to centre of source (cx=cy=0.5).

        centre means: x0 = (src_w - cw) / 2 rounded to int, similarly y0.
        """
        reframe = _make_track_reframe(track=[])
        parts, _ = _run_track_filter(reframe, src_w=_SRC_W, src_h=_SRC_H)
        seg = parts[0]
        cw, ch = _cw_ch_from_filter(seg)
        m = re.search(r"crop=\d+:\d+:'(\d+)':'(\d+)'", seg)
        assert m is not None, f"constant crop origin not found: {seg!r}"
        x0 = int(m.group(1))
        y0 = int(m.group(2))
        expected_x = round((_SRC_W - cw) / 2)
        expected_y = round((_SRC_H - ch) / 2)
        # Allow ±1 for rounding
        assert abs(x0 - expected_x) <= 1, (
            f"x0={x0} deviates from centre {expected_x} by more than 1"
        )
        assert abs(y0 - expected_y) <= 1, (
            f"y0={y0} deviates from centre {expected_y} by more than 1"
        )


# ---------------------------------------------------------------------------
# multi-source + track ガード（DC-AS-008 確定裁定）
# ---------------------------------------------------------------------------


class TestMultiSourceTrackGuard:
    """DC-AS-008: multi-source timeline + track → delegate to existing per-clip
    scale-first cover crop path; do NOT raise INVALID_INPUT; emit warning.

    Design context:
        The parent has ruled (DC-AS-008) that multi-source + track falls back
        silently to the existing per-clip cover crop path (not crop-from-source)
        with a warning.  INVALID_INPUT must NOT be raised.  This differs from
        the arch-report §3.5 wording ('static centre track fallback'), because
        the per-clip path already normalises each clip to target dimensions using
        cover crop — a first-source crop-from-source constant centre would be
        incorrect for the 2nd+ clips.

    Note on test structure:
        build_plan is the entry point for multi-source detection.  We drive it
        via a multi-source RenderPlan / ranges setup.  The key assertions are:
        1. No INVALID_INPUT / UNSUPPORTED_OPERATION raised.
        2. Warning emitted mentioning multi-source track limitation.
        3. Output filter_complex does NOT contain the crop-from-source ramp
           expression (uses existing per-clip cover crop instead).
    """

    def _make_multi_source_ranges(self) -> list[Any]:
        """Build a two-source KeptRange list for multi-source detection."""
        import opentimelineio as otio
        from clipwright_render.plan import KeptRange

        def _rt(s: float, r: float = 30.0) -> otio.opentime.RationalTime:
            return otio.opentime.RationalTime(s * r, r)

        def _tr(start: float, dur: float) -> otio.opentime.TimeRange:
            return otio.opentime.TimeRange(_rt(start), _rt(dur))

        return [
            KeptRange(source="/fake/src_a.mp4", source_range=_tr(0.0, 3.0)),
            KeptRange(source="/fake/src_b.mp4", source_range=_tr(0.0, 3.0)),
        ]

    def _make_probe(self, width: int = 1920, height: int = 1080) -> Any:
        from clipwright_render.plan import ProbeInfo

        return ProbeInfo(
            has_video=True,
            audio_count=1,
            bit_rate=8_000_000,
            width=width,
            height=height,
            fps=30.0,
        )

    def test_multi_source_track_does_not_raise(self) -> None:
        """multi-source + track directive → build_plan does not raise (DC-AS-008)."""
        from clipwright_render.plan import build_plan
        from clipwright_render.schemas import RenderOptions

        ranges = self._make_multi_source_ranges()
        probe = self._make_probe()
        source_probes = {
            "/fake/src_a.mp4": probe,
            "/fake/src_b.mp4": probe,
        }
        reframe_dict = _make_track_directive()

        # Must not raise ClipwrightError (INVALID_INPUT or UNSUPPORTED_OPERATION)
        plan = build_plan(
            ranges=ranges,
            options=RenderOptions(),
            probe_info=probe,
            source_probes=source_probes,
            reframe=reframe_dict,
        )
        assert plan is not None

    def test_multi_source_track_emits_warning(self) -> None:
        """multi-source + track → RenderPlan.warnings contains multi-source message."""
        from clipwright_render.plan import build_plan
        from clipwright_render.schemas import RenderOptions

        ranges = self._make_multi_source_ranges()
        probe = self._make_probe()
        source_probes = {
            "/fake/src_a.mp4": probe,
            "/fake/src_b.mp4": probe,
        }
        reframe_dict = _make_track_directive()

        plan = build_plan(
            ranges=ranges,
            options=RenderOptions(),
            probe_info=probe,
            source_probes=source_probes,
            reframe=reframe_dict,
        )
        # Warning must mention multi-source and track limitation
        warnings_text = " ".join(plan.warnings).lower()
        assert "multi" in warnings_text or "track" in warnings_text, (
            f"Expected multi-source track warning, got: {plan.warnings}"
        )

    def test_multi_source_track_no_crop_from_source_ramp(self) -> None:
        """multi-source + track → filter_complex uses per-clip cover crop, not ramp.

        The per-clip cover crop path does NOT produce 'min(max(' x/y expressions
        inside a crop filter.  If the ramp expression appears, the implementation
        incorrectly applied crop-from-source on multi-source.
        """
        from clipwright_render.plan import build_plan
        from clipwright_render.schemas import RenderOptions

        ranges = self._make_multi_source_ranges()
        probe = self._make_probe()
        source_probes = {
            "/fake/src_a.mp4": probe,
            "/fake/src_b.mp4": probe,
        }
        reframe_dict = _make_track_directive()

        plan = build_plan(
            ranges=ranges,
            options=RenderOptions(),
            probe_info=probe,
            source_probes=source_probes,
            reframe=reframe_dict,
        )
        # Per-clip cover crop does NOT produce time-varying crop-from-source ramps.
        # We check that '[outvrf]' (track path terminal) is absent — per-clip path
        # uses different labels.
        assert "[outvrf]" not in plan.filter_complex, (
            f"crop-from-source terminal '[outvrf]' must not appear in multi-source "
            f"filter_complex, got: {plan.filter_complex!r}"
        )


# ---------------------------------------------------------------------------
# probe 失敗フォールバック (src_w/src_h None)
# ---------------------------------------------------------------------------


class TestProbeFailureFallback:
    """When src_w or src_h is None (probe failure), fall back to centre crop + warning.

    Architecture §3.5 / §5.
    """

    def test_src_w_none_produces_filter(self) -> None:
        """src_w=None → fallback centre crop (not error)."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        # Probe failure: src_w is None
        parts: list[str] = []
        label = _append_reframe_filter(
            parts, _IN_LABEL, reframe, src_w=None, src_h=_SRC_H
        )
        assert len(parts) == 1
        assert label == "[outvrf]"
        assert "crop=" in parts[0]

    def test_src_h_none_produces_filter(self) -> None:
        """src_h=None → fallback centre crop (not error)."""
        reframe = _make_track_reframe(track=[{"t_s": 0.0, "cx": 0.5, "cy": 0.5}])
        parts: list[str] = []
        label = _append_reframe_filter(
            parts, _IN_LABEL, reframe, src_w=_SRC_W, src_h=None
        )
        assert len(parts) == 1
        assert label == "[outvrf]"
        assert "crop=" in parts[0]


# ---------------------------------------------------------------------------
# N_max 防御 (_validate_reframe で len(track) > 80 → INVALID_INPUT)
# DC-AM-003: render は間引かない
# ---------------------------------------------------------------------------


class TestNMaxGuard:
    """AC-09 (N_max=80, confirmed by spike, overrides arch-report 120).

    _validate_reframe must:
    - len(track) > 80 → raise INVALID_INPUT (no decimation, DC-AM-003).
    - len(track) == 80 → pass through (boundary must be inclusive).

    Note: track_cli guarantees <=80 in normal use; this is a render-side
    defence guard for malformed or externally crafted directives.
    """

    def _make_n_keyframes(self, n: int) -> list[dict[str, float]]:
        """Build n keyframes with strictly increasing t_s (monotonic guarantee)."""
        return [{"t_s": float(i), "cx": 0.5, "cy": 0.5} for i in range(n)]

    def test_n_max_80_passes(self) -> None:
        """len(track) == 80 → _validate_reframe returns _RenderReframe (boundary)."""
        raw = _make_track_directive(keyframes=self._make_n_keyframes(80))
        result = _validate_reframe(raw)
        assert result is not None
        assert result.track is not None
        assert len(result.track) == 80

    def test_n_81_raises_invalid_input(self) -> None:
        """len(track) == 81 → _validate_reframe raises INVALID_INPUT (DC-AM-003)."""
        raw = _make_track_directive(keyframes=self._make_n_keyframes(81))
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(raw)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_n_81_no_decimation(self) -> None:
        """len(track) == 81 → error raised, NOT silently truncated to 80.

        render must NOT decimate (DC-AM-003).  If _validate_reframe returns
        without raising, the test fails (decimation would produce result with
        track !=None of length <=80, but we demand the exception).
        """
        raw = _make_track_directive(keyframes=self._make_n_keyframes(81))
        raised = False
        try:
            _validate_reframe(raw)
        except ClipwrightError as e:
            assert e.code == ErrorCode.INVALID_INPUT
            raised = True
        assert raised, "INVALID_INPUT must be raised for len(track)=81 (no decimation)"

    def test_n_100_raises_invalid_input(self) -> None:
        """len(track) == 100 → INVALID_INPUT (well above N_max)."""
        raw = _make_track_directive(keyframes=self._make_n_keyframes(100))
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(raw)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_n_1_passes(self) -> None:
        """len(track) == 1 → _validate_reframe returns _RenderReframe (well below N_max)."""
        raw = _make_track_directive(keyframes=self._make_n_keyframes(1))
        result = _validate_reframe(raw)
        assert result is not None
        assert result.track is not None
        assert len(result.track) == 1


# ---------------------------------------------------------------------------
# AC-06 後方互換ガード（DC-AS-007 混同防止コメント必須）
# ---------------------------------------------------------------------------


class TestBackwardCompatNonTrackModes:
    """AC-06 backward compat: existing mode='crop'/'pad'/'blur_pad' are unchanged.

    Design note (DC-AS-007 / ADR-T10):
        The static-centre track fallback (crop-from-source single path, §3.4)
        and the legacy mode='crop' (scale-first cover crop, §2.2/§3.4 existing)
        produce DIFFERENT pixel output by design.  This is intentional (ADR-T10).
        Tests here verify that adding mode='track' support does NOT alter the
        output of the three legacy modes.  The pixel difference between the two
        paths is tested in AC-12 (integration), not here.
    """

    @pytest.mark.parametrize("mode", ["crop", "pad", "blur_pad"])
    def test_legacy_mode_still_works_without_src_dimensions(self, mode: str) -> None:
        """Legacy mode={mode} _append_reframe_filter call without src_w/src_h succeeds.

        Legacy callers do not pass src_w/src_h; the function must remain backward
        compatible (optional parameters with defaults or **kwargs).
        """
        reframe = _RenderReframe(  # type: ignore[call-arg]
            target_w=_TW,
            target_h=_TH,
            mode=mode,
            anchor="center",
            pad_color="black",
        )
        parts: list[str] = []
        # Legacy call: no src_w/src_h kwargs
        label = _append_reframe_filter(parts, _IN_LABEL, reframe)
        assert label == "[outvrf]"
        assert len(parts) >= 1
        # Mode-crop and mode-pad produce exactly 1 segment; blur_pad produces 4
        if mode in ("crop", "pad"):
            assert len(parts) == 1
        else:
            assert len(parts) == 4

    def test_mode_track_absent_from_legacy_crop_segment(self) -> None:
        """mode='crop' segment must NOT contain 'min(max(' x/y ramp (§2.2 formula).

        This regression guard ensures that adding the track branch does not
        accidentally inject ramp expressions into the legacy crop path.
        The legacy crop uses iw/ih expressions and min(max( for clamping origin,
        but NOT for the ramp-style time-varying x(t) expression used in track mode.
        We check that the crop origin contains '(iw-' (legacy formula anchor).
        """
        reframe = _RenderReframe(  # type: ignore[call-arg]
            target_w=_TW,
            target_h=_TH,
            mode="crop",
            anchor="center",
            pad_color="black",
        )
        parts: list[str] = []
        _append_reframe_filter(parts, _IN_LABEL, reframe)
        seg = parts[0]
        # Legacy crop formula uses '(iw-W)' for origin calculation
        assert "(iw-" in seg or "iw-" in seg, (
            f"Legacy crop must use iw-based origin, got: {seg!r}"
        )


# ---------------------------------------------------------------------------
# SR-H-1 — zero-dt defence: ClipwrightError(INTERNAL) not AssertionError
# ---------------------------------------------------------------------------


class TestZeroDtDefence:
    """SR-H-1: _build_track_crop_expr raises ClipwrightError(INTERNAL) for zero dt.

    The check 'if dt_s in (...)' replaces a bare assert so that python -O
    (which strips asserts) cannot bypass the zero-division guard.
    """

    def _make_kf(
        self, t_s: float, cx: float = 0.5, cy: float = 0.5
    ) -> _RenderCentreKeyframe:
        return _RenderCentreKeyframe(t_s=t_s, cx=cx, cy=cy)

    def test_zero_dt_raises_clipwright_error(self) -> None:
        """Duplicate t_s values produce dt=0 → ClipwrightError(INTERNAL) is raised.

        This verifies the if-raise path introduced by SR-H-1 (replacing the
        assert that python -O would have silently removed).
        """
        kfs = [self._make_kf(1.0, 0.3, 0.4), self._make_kf(1.0, 0.7, 0.6)]
        with pytest.raises(ClipwrightError) as exc_info:
            _build_track_crop_expr(_SRC_W, _SRC_H, 608, 1080, kfs)
        assert exc_info.value.code == ErrorCode.INTERNAL

    def test_zero_dt_not_assertion_error(self) -> None:
        """Ensure the zero-dt guard raises ClipwrightError, not AssertionError.

        With the old assert-based guard, python -O would bypass the check and
        let ffmpeg receive a zero-denominator expression.  The if-raise path
        is never bypassed regardless of optimisation flags.
        """
        kfs = [self._make_kf(2.0, 0.2, 0.3), self._make_kf(2.0, 0.8, 0.7)]
        raised_type: type | None = None
        try:
            _build_track_crop_expr(_SRC_W, _SRC_H, 608, 1080, kfs)
        except ClipwrightError:
            raised_type = ClipwrightError
        except AssertionError:
            raised_type = AssertionError
        assert raised_type is ClipwrightError, (
            f"Expected ClipwrightError; got {raised_type}"
        )
