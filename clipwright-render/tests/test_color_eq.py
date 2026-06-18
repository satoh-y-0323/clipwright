"""test_color_eq.py — Tests for color → eq filter extension in clipwright-render.

Target functions (all implemented; all tests pass):
  - build_plan(..., color=color_dict) — `color` argument (FR-6)
  - _validate_color_eq(color: dict) -> _RenderEqParams | None
  - _append_eq_filter(filter_parts, video_map_label, eq) -> str
  - _build_filter_complex(..., color_eq=...) — updated signature
  - _build_multi_source_filter_complex(..., color_eq=...) — updated signature

Requirements: architecture-report-20260618-201024.md §6 + §7 render apply side
FR-6: Apply the eq color-correction filter (scale-after, subtitle-before) when
      a color directive is present in the timeline's clipwright metadata.
"""

from __future__ import annotations

import math
import re
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import (
    ProbeInfo,
    build_plan,
    resolve_kept_ranges,
)
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_plan.py / test_text_overlay.py — no import to
# keep test-file isolation per project convention)
# ---------------------------------------------------------------------------

FPS = 30.0

_COLOR_DICT: dict[str, Any] = {
    "tool": "clipwright-color",
    "version": "0.1.0",
    "kind": "color",
    "target_luma": 128.0,
    "measured": {
        "yavg": 96.4,
        "ymin": 12.0,
        "ymax": 230.0,
        "sampled_frames": 12,
    },
    "eq": {
        "brightness": 0.2,
        "contrast": 1.0,
        "saturation": 1.0,
        "gamma": 1.0,
    },
}


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


def _make_timeline(clips: list[Any]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for c in clips:
        track.append(c)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


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


def _single_source_plan(
    color: dict[str, Any] | None = None,
    options: RenderOptions | None = None,
) -> Any:
    """Build a single-source RenderPlan with an optional color directive."""
    tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
    ranges = resolve_kept_ranges(tl)
    probe = _make_probe()
    return build_plan(  # type: ignore[call-arg]
        ranges,
        probe,
        options or RenderOptions(),
        color=color,
    )


def _multi_source_plan(
    color: dict[str, Any] | None = None,
    options: RenderOptions | None = None,
) -> Any:
    """Build a multi-source RenderPlan with two different sources."""
    clips = [
        _make_clip("/src/a.mp4", 0.0, 3.0),
        _make_clip("/src/b.mp4", 0.0, 2.0),
    ]
    tl = _make_timeline(clips)
    ranges = resolve_kept_ranges(tl)
    source_probes = {
        "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
    }
    probe_info = source_probes["/src/a.mp4"]
    return build_plan(  # type: ignore[call-arg]
        ranges,
        probe_info,
        options or RenderOptions(),
        color=color,
        source_probes=source_probes,
    )


# ===========================================================================
# Aspect EQ-1: eq present — single-source path
# ===========================================================================


class TestEqPresentSingleSource:
    """build_plan(..., color=...) injects eq filter in single-source path (FR-6)."""

    def test_eq_substring_in_filter_complex(self) -> None:
        """filter_complex contains the eq= filter string with correct parameter values."""
        plan = _single_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        assert "eq=brightness=0.2:contrast=1:saturation=1:gamma=1" in fc

    def test_outveq_label_present(self) -> None:
        """[outveq] label is present in filter_complex."""
        plan = _single_source_plan(color=_COLOR_DICT)
        assert "[outveq]" in plan.filter_complex

    def test_outveq_appears_after_outv_or_outvscaled(self) -> None:
        """[outveq] label appears after [outv] or [outvscaled] in the joined filter_complex."""
        plan = _single_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        # [outveq] must come after [outv] (or [outvscaled] if scale stage is present)
        outv_pos = fc.rfind("[outv]")
        outvscaled_pos = fc.rfind("[outvscaled]")
        scale_label_pos = max(outv_pos, outvscaled_pos)
        outveq_pos = fc.find("[outveq]")
        assert outveq_pos != -1, "[outveq] not found in filter_complex"
        assert scale_label_pos < outveq_pos, (
            f"[outveq] (pos={outveq_pos}) must appear after "
            f"[outv]/[outvscaled] (pos={scale_label_pos})"
        )

    def test_outveq_appears_before_subtitles_and_drawtext(self) -> None:
        """[outveq] label appears before any subtitles= or drawtext= stage."""
        plan = _single_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        outveq_pos = fc.find("[outveq]")
        assert outveq_pos != -1

        # If subtitles= or drawtext= stages exist they must come after [outveq]
        sub_pos = fc.find("subtitles=")
        drawtext_pos = fc.find("drawtext=")
        if sub_pos != -1:
            assert outveq_pos < sub_pos, (
                f"[outveq] (pos={outveq_pos}) must appear before subtitles= (pos={sub_pos})"
            )
        if drawtext_pos != -1:
            assert outveq_pos < drawtext_pos, (
                f"[outveq] (pos={outveq_pos}) must appear before drawtext= (pos={drawtext_pos})"
            )


# ===========================================================================
# Aspect EQ-2: eq present — multi-source path
# ===========================================================================


class TestEqPresentMultiSource:
    """build_plan(..., color=...) injects eq filter in multi-source path (FR-6)."""

    def test_eq_substring_in_filter_complex(self) -> None:
        """filter_complex contains the eq= filter string with correct parameter values (multi-source)."""
        plan = _multi_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        assert "eq=brightness=0.2:contrast=1:saturation=1:gamma=1" in fc

    def test_outveq_label_present(self) -> None:
        """[outveq] label is present in filter_complex (multi-source)."""
        plan = _multi_source_plan(color=_COLOR_DICT)
        assert "[outveq]" in plan.filter_complex

    def test_outveq_appears_after_outv(self) -> None:
        """[outveq] label appears after [outv] in the multi-source filter_complex."""
        plan = _multi_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        outv_pos = fc.rfind("[outv]")
        outveq_pos = fc.find("[outveq]")
        assert outveq_pos != -1, "[outveq] not found in filter_complex"
        assert outv_pos < outveq_pos, (
            f"[outveq] (pos={outveq_pos}) must appear after [outv] (pos={outv_pos})"
        )

    def test_outveq_appears_before_subtitle_or_drawtext(self) -> None:
        """[outveq] label appears before any subtitles= or drawtext= stage (multi-source)."""
        plan = _multi_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        outveq_pos = fc.find("[outveq]")
        assert outveq_pos != -1

        sub_pos = fc.find("subtitles=")
        drawtext_pos = fc.find("drawtext=")
        if sub_pos != -1:
            assert outveq_pos < sub_pos
        if drawtext_pos != -1:
            assert outveq_pos < drawtext_pos


# ===========================================================================
# Aspect EQ-3: ordering with subtitle + scale
# ===========================================================================


class TestEqOrderingWithSubtitleAndScale:
    """scale → [outvscaled] → eq → [outveq] → subtitle → [outvsub] ordering (ADR-CO-4)."""

    def _plan_with_scale_and_subtitle(self, color: dict[str, Any] | None) -> Any:
        """Build a plan with explicit width/height (forces scale) and subtitle."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".vtt", mode="w", delete=False) as f:
            f.write("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello\n")
            sub_path = f.name

        options = RenderOptions(
            width=1280,
            height=720,
            subtitle=SubtitleOptions(path=sub_path),
        )
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(width=1920, height=1080)
        return build_plan(  # type: ignore[call-arg]
            ranges,
            probe,
            options,
            color=color,
        )

    def test_scale_then_eq_then_subtitle_ordering(self) -> None:
        """filter_complex order: [outvscaled] before [outveq] before [outvsub]."""
        plan = self._plan_with_scale_and_subtitle(color=_COLOR_DICT)
        fc = plan.filter_complex
        outvscaled_pos = fc.find("[outvscaled]")
        outveq_pos = fc.find("[outveq]")
        outvsub_pos = fc.find("[outvsub]")

        assert outvscaled_pos != -1, "[outvscaled] not found (scale stage missing)"
        assert outveq_pos != -1, "[outveq] not found (eq stage missing)"
        assert outvsub_pos != -1, "[outvsub] not found (subtitle stage missing)"

        assert outvscaled_pos < outveq_pos, (
            f"[outvscaled] (pos={outvscaled_pos}) must appear before [outveq] (pos={outveq_pos})"
        )
        assert outveq_pos < outvsub_pos, (
            f"[outveq] (pos={outveq_pos}) must appear before [outvsub] (pos={outvsub_pos})"
        )

    def test_eq_filter_string_present_with_subtitle(self) -> None:
        """eq= string is present even when subtitle option is active."""
        plan = self._plan_with_scale_and_subtitle(color=_COLOR_DICT)
        assert (
            "eq=brightness=0.2:contrast=1:saturation=1:gamma=1" in plan.filter_complex
        )


# ===========================================================================
# Aspect EQ-4: backward compatibility
# ===========================================================================


class TestEqBackwardCompat:
    """color=None and missing color key → no eq stage; filter_complex identical to baseline (FR-6)."""

    def _baseline_plan(self) -> Any:
        """Build a reference plan without color argument (existing behavior)."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe()
        return build_plan(ranges, probe, RenderOptions())

    def test_color_none_no_eq_substring(self) -> None:
        """color=None → filter_complex does not contain 'eq=' substring."""
        plan = _single_source_plan(color=None)
        assert "eq=" not in plan.filter_complex

    def test_color_none_no_outveq_label(self) -> None:
        """color=None → filter_complex does not contain '[outveq]' label."""
        plan = _single_source_plan(color=None)
        assert "[outveq]" not in plan.filter_complex

    def test_color_none_identical_to_baseline(self) -> None:
        """color=None → filter_complex byte-identical to the no-color baseline."""
        baseline = self._baseline_plan()
        with_none = _single_source_plan(color=None)
        assert baseline.filter_complex == with_none.filter_complex

    def test_color_missing_eq_key_no_eq_substring(self) -> None:
        """color dict without 'eq' key → no eq stage injected."""
        color_no_eq: dict[str, Any] = {
            "tool": "clipwright-color",
            "version": "0.1.0",
            "kind": "color",
            "target_luma": 128.0,
        }
        plan = _single_source_plan(color=color_no_eq)
        assert "eq=" not in plan.filter_complex

    def test_color_eq_none_no_eq_substring(self) -> None:
        """color={"eq": None} → no eq stage injected."""
        color_eq_none: dict[str, Any] = {
            "tool": "clipwright-color",
            "version": "0.1.0",
            "kind": "color",
            "target_luma": 128.0,
            "eq": None,
        }
        plan = _single_source_plan(color=color_eq_none)
        assert "eq=" not in plan.filter_complex

    def test_color_empty_dict_no_eq_substring(self) -> None:
        """color={} → no eq stage injected (eq key absent)."""
        plan = _single_source_plan(color={})
        assert "eq=" not in plan.filter_complex

    def test_multi_source_color_none_no_eq_substring(self) -> None:
        """Multi-source path: color=None → filter_complex does not contain 'eq='."""
        plan = _multi_source_plan(color=None)
        assert "eq=" not in plan.filter_complex

    def test_multi_source_color_none_no_outveq_label(self) -> None:
        """Multi-source path: color=None → filter_complex does not contain '[outveq]'."""
        plan = _multi_source_plan(color=None)
        assert "[outveq]" not in plan.filter_complex


# ===========================================================================
# Aspect EQ-5: _validate_color_eq validation
# ===========================================================================


class TestValidateColorEq:
    """_validate_color_eq rejects out-of-range/inf/nan/extra keys; accepts None/empty eq (FR-6, CWE-20)."""

    def _validate(self, color: dict[str, Any]) -> Any:
        """Call _validate_color_eq directly (internal function)."""
        from clipwright_render.plan import _validate_color_eq  # type: ignore[attr-defined]

        return _validate_color_eq(color)

    # --- range rejections ---

    def test_brightness_out_of_range_raises_invalid_input(self) -> None:
        """brightness=5.0 (out of [-1, 1]) → INVALID_INPUT."""
        color = {
            "eq": {"brightness": 5.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_brightness_negative_out_of_range_raises_invalid_input(self) -> None:
        """brightness=-2.0 (out of [-1, 1]) → INVALID_INPUT."""
        color = {
            "eq": {"brightness": -2.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_contrast_out_of_range_raises_invalid_input(self) -> None:
        """contrast=3.0 (out of [0, 2]) → INVALID_INPUT."""
        color = {
            "eq": {"brightness": 0.0, "contrast": 3.0, "saturation": 1.0, "gamma": 1.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_saturation_out_of_range_raises_invalid_input(self) -> None:
        """saturation=2.5 (out of [0, 2]) → INVALID_INPUT."""
        color = {
            "eq": {"brightness": 0.0, "contrast": 1.0, "saturation": 2.5, "gamma": 1.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_gamma_out_of_range_high_raises_invalid_input(self) -> None:
        """gamma=11.0 (out of [0.1, 10]) → INVALID_INPUT."""
        color = {
            "eq": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 11.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_gamma_out_of_range_low_raises_invalid_input(self) -> None:
        """gamma=0.0 (below 0.1) → INVALID_INPUT."""
        color = {
            "eq": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 0.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- inf / nan rejections ---

    def test_brightness_inf_raises_invalid_input(self) -> None:
        """brightness=inf → INVALID_INPUT (CWE-20)."""
        color = {
            "eq": {
                "brightness": math.inf,
                "contrast": 1.0,
                "saturation": 1.0,
                "gamma": 1.0,
            }
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_brightness_nan_raises_invalid_input(self) -> None:
        """brightness=nan → INVALID_INPUT (CWE-20)."""
        color = {
            "eq": {
                "brightness": math.nan,
                "contrast": 1.0,
                "saturation": 1.0,
                "gamma": 1.0,
            }
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_contrast_inf_raises_invalid_input(self) -> None:
        """contrast=-inf → INVALID_INPUT (CWE-20)."""
        color = {
            "eq": {
                "brightness": 0.0,
                "contrast": -math.inf,
                "saturation": 1.0,
                "gamma": 1.0,
            }
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- extra key rejections ---

    def test_extra_key_raises_invalid_input(self) -> None:
        """Extra key in eq dict → INVALID_INPUT (extra: forbid)."""
        color = {
            "eq": {
                "brightness": 0.2,
                "contrast": 1.0,
                "saturation": 1.0,
                "gamma": 1.0,
                "unknown_param": 42,
            }
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- None / empty → return None (no eq) ---

    def test_eq_none_returns_none(self) -> None:
        """color={"eq": None} → _validate_color_eq returns None (no eq stage)."""
        result = self._validate({"eq": None})
        assert result is None

    def test_empty_dict_returns_none(self) -> None:
        """color={} (no eq key) → _validate_color_eq returns None."""
        result = self._validate({})
        assert result is None

    def test_color_without_eq_key_returns_none(self) -> None:
        """color dict lacking 'eq' key → _validate_color_eq returns None."""
        result = self._validate({"tool": "clipwright-color", "version": "0.1.0"})
        assert result is None

    # --- valid input returns _RenderEqParams (not None) ---

    def test_valid_eq_returns_non_none(self) -> None:
        """Valid eq dict → _validate_color_eq returns a non-None _RenderEqParams object."""
        color = {
            "eq": {"brightness": 0.2, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        result = self._validate(color)
        assert result is not None

    def test_valid_eq_fields_accessible(self) -> None:
        """Valid eq dict → returned object has correct field values."""
        color = {
            "eq": {"brightness": 0.2, "contrast": 1.5, "saturation": 0.8, "gamma": 2.0}
        }
        result = self._validate(color)
        assert result is not None
        assert result.brightness == pytest.approx(0.2)
        assert result.contrast == pytest.approx(1.5)
        assert result.saturation == pytest.approx(0.8)
        assert result.gamma == pytest.approx(2.0)

    def test_valid_boundary_brightness_minus_one(self) -> None:
        """brightness=-1.0 (lower boundary) is accepted."""
        color = {
            "eq": {"brightness": -1.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        result = self._validate(color)
        assert result is not None

    def test_valid_boundary_brightness_plus_one(self) -> None:
        """brightness=1.0 (upper boundary) is accepted."""
        color = {
            "eq": {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        result = self._validate(color)
        assert result is not None

    def test_valid_boundary_gamma_min(self) -> None:
        """gamma=0.1 (lower boundary) is accepted."""
        color = {
            "eq": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 0.1}
        }
        result = self._validate(color)
        assert result is not None

    def test_valid_boundary_gamma_max(self) -> None:
        """gamma=10.0 (upper boundary) is accepted."""
        color = {
            "eq": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 10.0}
        }
        result = self._validate(color)
        assert result is not None

    # --- CWE-209: error message must not expose input values ---

    def test_error_message_does_not_contain_input_value(self) -> None:
        """INVALID_INPUT message must not expose input values (CWE-209).

        Also verifies that from None cuts the exception chain (__cause__ is None).
        """
        color = {
            "eq": {"brightness": 5.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "5.0" not in exc_info.value.message
        assert exc_info.value.__cause__ is None


# ===========================================================================
# Aspect EQ-6: eq filter numeric format lock (SR-INJ-002)
# ===========================================================================


class TestEqFilterFormatLock:
    """eq= filter values in filter_complex must be numeric-only (SR-INJ-002).

    Ensures :g formatting does not allow filtergraph special characters
    (`:`, `[`, `]`, `,` etc.) to be injected into the filter chain.
    """

    def test_eq_filter_format_is_numeric_only(self) -> None:
        """eq= filter values must be numeric (no filtergraph special chars injected)."""
        plan = _single_source_plan(color=_COLOR_DICT)
        fc = plan.filter_complex
        # Extract the eq=...[outveq] block
        m = re.search(r"eq=([^\[]+)\[outveq\]", fc)
        assert m is not None, (
            f"eq=...[outveq] block not found in filter_complex: {fc!r}"
        )
        eq_part = m.group(1)
        # Each param=value pair must have a numeric-only value
        for param in eq_part.split(":"):
            key, sep, val = param.partition("=")
            assert sep == "=", f"Unexpected param format (no '='): {param!r}"
            assert re.fullmatch(r"-?[\d.]+(?:e[+-]?\d+)?", val), (
                f"Non-numeric value in eq filter: {param!r}"
            )
