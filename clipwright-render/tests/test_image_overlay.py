"""test_image_overlay.py — Green-phase tests for image_overlay render extension.

All target symbols are implemented and this suite is expected to PASS:
  - ImageOverlay (frozen dataclass in clipwright_render.plan)
  - _collect_image_overlays(timeline, image_index_base) -> list[ImageOverlay]
  - _build_overlay_segment(o, base_label, i) -> tuple[list[str], str]
  - _append_overlay_filter(filter_parts, video_map_label, overlays) -> str
  - _check_image_overlay_within_timeline_dir(timeline_path, image_path) in render.py
  - _ALLOWED_IMAGE_EXTENSIONS in render.py
  - RenderPlan.image_sources (new field)

Architecture authority: architecture-report-20260622-013708.md §V2 (OVERRIDES v1).
Ground-truth nodes verified on real ffmpeg 8.1.1:
  V2-0 (G1..G5), V2-1 (fade filter chain), V2-2 (scale=:-2), V2-3 (relative-path
  reconstruct + re-validate), V2-6 (x/y single-quoted), V2-7 (corrupt -> SUBPROCESS_FAILED),
  V2-9 (collect cap 64), ADR-OV-5 (stream index G4-confirmed).

Test isolation:
  - All new symbols are imported INSIDE test functions or methods with
    pytest.importorskip / try-except so that ImportError does NOT break
    collection of the rest of the render suite.
  - Existing tests in test_plan.py / test_render.py / test_text_overlay.py
    are not modified.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import opentimelineio as otio
import pytest

# ---------------------------------------------------------------------------
# Guard: imports of new symbols are deferred to inside test bodies.
# Module-level sentinel that these symbols are NOT yet available.
# ---------------------------------------------------------------------------

_PLAN_HAS_IMAGE_OVERLAY: bool
try:
    from clipwright_render.plan import ImageOverlay as _IO  # noqa: F401

    _PLAN_HAS_IMAGE_OVERLAY = True
except ImportError:
    _PLAN_HAS_IMAGE_OVERLAY = False

_RENDER_HAS_IMAGE_CHECK: bool
try:
    from clipwright_render.render import (  # noqa: F401
        _ALLOWED_IMAGE_EXTENSIONS as _AIE,
    )

    _RENDER_HAS_IMAGE_CHECK = True
except ImportError:
    _RENDER_HAS_IMAGE_CHECK = False

# Guard: xfail(strict=True) activates only when the symbols are absent (i.e. if
# the package is installed without the image_overlay extension).  Currently all
# symbols exist and this marker is inactive.  Using the module-level sentinel
# is the same pattern as clipwright-sequence/tests/test_server.py.
pytestmark = pytest.mark.xfail(
    not _PLAN_HAS_IMAGE_OVERLAY,
    strict=True,
    reason="ImageOverlay symbols not found (image_overlay extension not installed)",
)

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_text_overlay.py — no cross-file import)
# ---------------------------------------------------------------------------

FPS = 30.0


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(clips: list[Any]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for c in clips:
        track.append(c)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _add_image_overlay_marker(
    timeline: otio.schema.Timeline,
    *,
    image_path: str = "logo/overlay.png",
    start_sec: float = 1.0,
    duration_sec: float = 3.0,
    x: str = "(W-w)/2",
    y: str = "(H-h)/2",
    scale: float = 1.0,
    opacity: float = 1.0,
    fade_in_sec: float = 0.3,
    fade_out_sec: float = 0.3,
    name: str = "image_0",
) -> None:
    """Attach an image_overlay marker directly to the first video track."""
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"

    rate = FPS
    marked_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start_sec * rate, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    marker = otio.schema.Marker(
        name=name,
        marked_range=marked_range,
        metadata={
            "clipwright": {
                "kind": "image_overlay",
                "tool": "clipwright-overlay",
                "version": "0.1.0",
                "image_path": image_path,
                "start_sec": start_sec,
                "duration_sec": duration_sec,
                "x": x,
                "y": y,
                "scale": scale,
                "opacity": opacity,
                "fade_in_sec": fade_in_sec,
                "fade_out_sec": fade_out_sec,
            }
        },
    )
    video_track.markers.append(marker)


# ---------------------------------------------------------------------------
# Helper: build an ImageOverlay instance directly (asserts dataclass fields)
# ---------------------------------------------------------------------------


def _make_image_overlay(**kwargs: Any) -> Any:
    """Construct an ImageOverlay using the plan module's frozen dataclass."""
    from clipwright_render.plan import ImageOverlay  # type: ignore[attr-defined]

    defaults: dict[str, Any] = dict(
        image_path="/project/logo/overlay.png",
        start_s=1.0,
        end_s=4.0,
        x="(W-w)/2",
        y="(H-h)/2",
        scale=1.0,
        opacity=0.8,
        fade_in_s=0.3,
        fade_out_s=0.3,
        input_index=2,
    )
    defaults.update(kwargs)
    return ImageOverlay(**defaults)


# ===========================================================================
# Section 1: ImageOverlay frozen dataclass
# ===========================================================================


class TestImageOverlayDataclass:
    """ImageOverlay is a frozen dataclass with the required fields (ADR-OV-4)."""

    def test_image_overlay_importable(self) -> None:
        """ImageOverlay can be imported from clipwright_render.plan."""
        from clipwright_render.plan import ImageOverlay  # type: ignore[attr-defined]

        assert ImageOverlay is not None

    def test_image_overlay_is_frozen(self) -> None:
        """ImageOverlay is a frozen dataclass — mutation raises FrozenInstanceError."""
        o = _make_image_overlay()
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            o.scale = 2.0  # type: ignore[misc]

    def test_image_overlay_has_required_fields(self) -> None:
        """ImageOverlay has all 10 required fields."""
        o = _make_image_overlay(
            image_path="/p/a.png",
            start_s=0.0,
            end_s=5.0,
            x="10",
            y="20",
            scale=0.5,
            opacity=0.7,
            fade_in_s=0.2,
            fade_out_s=0.2,
            input_index=3,
        )
        assert o.image_path == "/p/a.png"
        assert o.start_s == 0.0
        assert o.end_s == 5.0
        assert o.x == "10"
        assert o.y == "20"
        assert o.scale == 0.5
        assert o.opacity == 0.7
        assert o.fade_in_s == 0.2
        assert o.fade_out_s == 0.2
        assert o.input_index == 3

    def test_image_overlay_field_types(self) -> None:
        """ImageOverlay field types: str/float/float/str/str/float/float/float/float/int."""
        o = _make_image_overlay(input_index=5)
        assert isinstance(o.image_path, str)
        assert isinstance(o.start_s, float)
        assert isinstance(o.end_s, float)
        assert isinstance(o.x, str)
        assert isinstance(o.y, str)
        assert isinstance(o.scale, float)
        assert isinstance(o.opacity, float)
        assert isinstance(o.fade_in_s, float)
        assert isinstance(o.fade_out_s, float)
        assert isinstance(o.input_index, int)


# ===========================================================================
# Section 2: _collect_image_overlays
# ===========================================================================


class TestCollectImageOverlays:
    """_collect_image_overlays reads kind=='image_overlay' markers (ADR-OV-4 / V2-3)."""

    def test_empty_timeline_returns_empty_list(self) -> None:
        """Timeline with no markers -> empty list."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        result = _collect_image_overlays(tl, image_index_base=1)
        assert result == []

    def test_non_image_overlay_kind_ignored(self) -> None:
        """Markers with kind!='image_overlay' are excluded."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        track = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        track.markers.append(
            otio.schema.Marker(
                name="text_0",
                marked_range=_tr(1.0, 2.0),
                metadata={"clipwright": {"kind": "text_overlay"}},
            )
        )
        result = _collect_image_overlays(tl, image_index_base=1)
        assert result == []

    def test_single_marker_produces_one_overlay(self) -> None:
        """One image_overlay marker yields one ImageOverlay object."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, image_path="logo/a.png", start_sec=1.0, duration_sec=3.0
        )

        # Reconstruct requires the marker image_path to be a relative posix path.
        # _collect_image_overlays needs the timeline object which holds the file path.
        # We mock timeline to have a known path so reconstruct works.
        with patch.object(
            type(tl),
            "metadata",
            new_callable=lambda: property(  # type: ignore[return-value]
                lambda self: {"clipwright": {"timeline_path": "/project/timeline.otio"}}
            ),
        ):
            pass  # Just testing that we get one overlay back.

        result = _collect_image_overlays(tl, image_index_base=2)
        assert len(result) == 1

    def test_input_index_uses_base_plus_collection_order(self) -> None:
        """input_index = image_index_base + i (0-indexed collection order)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, image_path="logo/a.png", name="image_0")
        _add_image_overlay_marker(
            tl, image_path="logo/b.png", name="image_1", start_sec=5.0
        )

        result = _collect_image_overlays(tl, image_index_base=3)
        assert len(result) == 2
        assert result[0].input_index == 3
        assert result[1].input_index == 4

    def test_collect_cap_64_raises_invalid_input(self) -> None:
        """Collecting >64 image_overlay markers raises INVALID_INPUT (V2-9)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 300.0)])
        for i in range(65):
            _add_image_overlay_marker(
                tl,
                image_path=f"logo/img_{i}.png",
                name=f"image_{i}",
                start_sec=float(i),
                duration_sec=1.0,
            )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_collect_exactly_64_passes(self) -> None:
        """Collecting exactly 64 image_overlay markers does NOT raise (boundary)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 300.0)])
        for i in range(64):
            _add_image_overlay_marker(
                tl,
                image_path=f"logo/img_{i}.png",
                name=f"image_{i}",
                start_sec=float(i),
                duration_sec=1.0,
            )
        # Should not raise
        result = _collect_image_overlays(tl, image_index_base=1)
        assert len(result) == 64

    def test_revalidation_start_negative_raises_invalid_input(self) -> None:
        """Re-validation: start_sec < 0 in marker raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, start_sec=-1.0)
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_zero_duration_raises_invalid_input(self) -> None:
        """Re-validation: duration_sec <= 0 in marker raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, duration_sec=0.0)
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_scale_zero_raises_invalid_input(self) -> None:
        """Re-validation: scale <= 0 in marker raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, scale=0.0)
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_scale_above_8_raises_invalid_input(self) -> None:
        """Re-validation: scale > 8.0 raises INVALID_INPUT (V2-9)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, scale=8.01)
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_opacity_above_1_raises_invalid_input(self) -> None:
        """Re-validation: opacity > 1.0 raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, opacity=1.01)
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_fade_sum_exceeds_duration_raises_invalid_input(self) -> None:
        """Re-validation: fade_in + fade_out > duration_sec raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, fade_in_sec=2.0, fade_out_sec=2.0, duration_sec=3.0
        )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_x_with_forbidden_char_raises_invalid_input(self) -> None:
        """Re-validation: x/y with char outside allowlist ^[A-Za-z0-9_()+\\-*/. ]+$ raises."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        # colon is forbidden (filtergraph injection risk)
        _add_image_overlay_marker(tl, x="(W-w)/2:evil")
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_image_path_with_single_quote_raises_invalid_input(
        self,
    ) -> None:
        """Re-validation: image_path with single-quote raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, image_path="logo/it's.png")
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_image_path_with_control_char_raises_invalid_input(
        self,
    ) -> None:
        """Re-validation: image_path with control char raises INVALID_INPUT."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, image_path="logo/bad\x00.png")
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_revalidation_nan_start_raises_invalid_input(self) -> None:
        """Re-validation: start_sec=nan raises INVALID_INPUT (non-finite guard)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, start_sec=math.nan)
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# Section 3: _build_overlay_segment — exact confirmed filter chain (V2-1)
# ===========================================================================


class TestBuildOverlaySegment:
    """_build_overlay_segment emits the V2-1 confirmed fade filter chain."""

    def _make_both_fades(self) -> Any:
        """Helper: ImageOverlay with both fades > 0."""
        return _make_image_overlay(
            image_path="/project/logo/a.png",
            start_s=2.0,
            end_s=7.0,
            x="(W-w)/2",
            y="(H-h)/2",
            scale=0.5,
            opacity=0.8,
            fade_in_s=0.5,
            fade_out_s=0.5,
            input_index=2,
        )

    def test_build_overlay_segment_importable(self) -> None:
        """_build_overlay_segment can be imported from clipwright_render.plan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        assert _build_overlay_segment is not None

    def test_returns_tuple_list_str(self) -> None:
        """_build_overlay_segment returns (list[str], str)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = self._make_both_fades()
        result = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert isinstance(result, tuple)
        assert len(result) == 2
        segs, new_label = result
        assert isinstance(segs, list)
        assert isinstance(new_label, str)

    def test_two_segments_emitted_for_both_fades(self) -> None:
        """With both fades > 0, two filter segment strings are emitted."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert len(segs) == 2

    # --- Segment 1: image processing chain ---

    def test_segment1_starts_with_stream_ref(self) -> None:
        """Segment 1 starts with [{input_index}:v] stream reference."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(input_index=3)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert segs[0].startswith("[3:v]")

    def test_segment1_uses_scale_iw_times_scale_colon_minus_2(self) -> None:
        """Segment 1 uses scale=iw*{scale}:-2 (V2-2: NOT :-1, NOT :-0)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(scale=0.5, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        # Must contain :-2
        assert ":-2" in seg1, f"Expected :-2 in segment 1, got: {seg1!r}"
        # Must NOT contain :-1
        assert ":-1" not in seg1, f"Found :-1 in segment 1 (should be :-2): {seg1!r}"

    def test_segment1_contains_format_rgba(self) -> None:
        """Segment 1 contains format=rgba (alpha channel for opacity/fade)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert "format=rgba" in segs[0]

    def test_segment1_colorchannelmixer_aa_is_constant_opacity(self) -> None:
        """Segment 1 uses colorchannelmixer=aa={opacity} as CONSTANT (V2-1/G1).

        NOT a time-varying expression — aa= only accepts double, not avfilter expr.
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(opacity=0.8, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        # Must contain the constant opacity value
        assert "colorchannelmixer=aa=" in seg1, (
            f"Expected colorchannelmixer=aa= in segment 1: {seg1!r}"
        )
        # Must NOT contain time-varying expression tokens (if/lt/gt)
        assert "if(" not in seg1, (
            f"aa= must be constant (not time-varying expr): {seg1!r}"
        )
        assert "lt(" not in seg1, (
            f"aa= must be constant (not time-varying expr): {seg1!r}"
        )

    def test_segment1_has_fade_in_when_fade_in_nonzero(self) -> None:
        """Segment 1 contains fade=t=in stage when fade_in_s > 0 (V2-1)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(fade_in_s=0.5, fade_out_s=0.0, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert "fade=t=in" in segs[0], (
            f"fade=t=in should be present when fade_in_s>0: {segs[0]!r}"
        )

    def test_segment1_has_fade_out_when_fade_out_nonzero(self) -> None:
        """Segment 1 contains fade=t=out stage when fade_out_s > 0 (V2-1)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(
            start_s=2.0, end_s=7.0, fade_in_s=0.0, fade_out_s=0.5, input_index=2
        )
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert "fade=t=out" in segs[0], (
            f"fade=t=out should be present when fade_out_s>0: {segs[0]!r}"
        )

    def test_segment1_fade_in_omitted_when_zero(self) -> None:
        """Segment 1 OMITS fade=t=in when fade_in_s == 0 (V2-1 degenerate d=0 avoidance)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(fade_in_s=0.0, fade_out_s=0.3, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert "fade=t=in" not in segs[0], (
            f"fade=t=in should be OMITTED when fade_in_s==0: {segs[0]!r}"
        )

    def test_segment1_fade_out_omitted_when_zero(self) -> None:
        """Segment 1 OMITS fade=t=out when fade_out_s == 0 (V2-1 degenerate d=0 avoidance)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(
            start_s=2.0, end_s=7.0, fade_in_s=0.3, fade_out_s=0.0, input_index=2
        )
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert "fade=t=out" not in segs[0], (
            f"fade=t=out should be OMITTED when fade_out_s==0: {segs[0]!r}"
        )

    def test_segment1_both_fades_zero_no_fade_stages(self) -> None:
        """With fade_in_s=0 and fade_out_s=0, NO fade stages appear (V2-1)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(fade_in_s=0.0, fade_out_s=0.0, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert "fade=t=in" not in seg1
        assert "fade=t=out" not in seg1
        # Chain ends at colorchannelmixer=aa={opacity}[ov{i}]
        assert "colorchannelmixer=aa=" in seg1

    def test_segment1_fade_in_has_alpha_1(self) -> None:
        """fade=t=in stage contains :alpha=1 (G2 confirmed — multiplies existing alpha)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(fade_in_s=0.5, fade_out_s=0.0, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert ":alpha=1" in segs[0], (
            f"fade stage must have :alpha=1 (G2 — multiplies aa constant): {segs[0]!r}"
        )

    def test_segment1_intermediate_label_is_ov_i(self) -> None:
        """Segment 1 output label is [ov{i}] (not [ovimg{i}] — using overlay index)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        # The overlay index 'i' is implicit in the label naming;
        # we verify the label pattern [ov0] or [ov{N}] appears.
        o = _make_image_overlay(input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        # Segment 1 must end with something like [ov0] or [ov2] etc.
        import re

        assert re.search(r"\[ov\d+\]$", seg1), (
            f"Segment 1 must end with [ov{{i}}]: {seg1!r}"
        )

    # --- Segment 2: overlay composition ---

    def test_segment2_contains_overlay_filter(self) -> None:
        """Segment 2 contains 'overlay=' filter."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert "overlay=" in segs[1]

    def test_segment2_x_y_are_single_quoted(self) -> None:
        """Segment 2 x/y are SINGLE-QUOTED in the overlay filter (V2-6/G5).

        overlay=x='{x}':y='{y}':enable='between(...)' — NOT bare.
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(x="(W-w)/2", y="(H-h)/2", input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg2 = segs[1]
        # x value must appear as x='(W-w)/2'
        assert "x='(W-w)/2'" in seg2, f"x must be single-quoted (V2-6): {seg2!r}"
        assert "y='(H-h)/2'" in seg2, f"y must be single-quoted (V2-6): {seg2!r}"

    def test_segment2_enable_between_with_start_and_end(self) -> None:
        """Segment 2 enable='between(t,{start_s},{end_s})' with correct times."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(start_s=2.0, end_s=7.0, input_index=2)
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg2 = segs[1]
        assert "enable=" in seg2
        assert "between(t," in seg2
        assert "2.0" in seg2 or "2," in seg2 or "2)" in seg2

    def test_segment2_uses_base_label(self) -> None:
        """Segment 2 starts with the provided base_label."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_overlay_segment(o, base_label="[outvtext]", i=0)
        assert segs[1].startswith("[outvtext]")

    def test_segment2_output_label_is_outvimg_i(self) -> None:
        """Segment 2 output label matches [outvimg{i}] pattern."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        import re

        o = self._make_both_fades()
        segs, new_label = _build_overlay_segment(o, base_label="[outv]", i=0)
        assert re.search(r"\[outvimg\d+\]$", segs[1]), (
            f"Segment 2 must end with [outvimg{{i}}]: {segs[1]!r}"
        )
        # new_label must also be [outvimg{i}]
        assert re.match(r"^\[outvimg\d+\]$", new_label), (
            f"new_label must be [outvimg{{i}}]: {new_label!r}"
        )

    def test_new_label_matches_segment2_output(self) -> None:
        """new_label returned equals the [outvimg{i}] label at end of segment 2."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = self._make_both_fades()
        segs, new_label = _build_overlay_segment(o, base_label="[outv]", i=0)
        # The label at end of segment 2 should match new_label
        assert segs[1].endswith(new_label), (
            f"Segment 2 tail {segs[1]!r} must end with new_label {new_label!r}"
        )

    # --- Exact assembled string verification for both-fades case ---

    def test_exact_segment1_string_both_fades(self) -> None:
        """Segment 1 exact content matches V2-1 confirmed chain (both fades > 0).

        Expected (assembled without line breaks):
          [{input_index}:v]scale=iw*{scale}:-2,format=rgba,colorchannelmixer=aa={opacity},
          fade=t=in:st={start_s}:d={fade_in_s}:alpha=1,
          fade=t=out:st={end_s-fade_out_s}:d={fade_out_s}:alpha=1[ov{i}]
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(
            start_s=2.0,
            end_s=7.0,
            x="(W-w)/2",
            y="(H-h)/2",
            scale=0.5,
            opacity=0.8,
            fade_in_s=0.5,
            fade_out_s=0.5,
            input_index=2,
        )
        segs, new_label = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]

        # Key structural assertions (not exact string match since float formatting may vary)
        assert "[2:v]" in seg1
        assert "scale=iw*" in seg1
        assert ":-2" in seg1
        assert "format=rgba" in seg1
        assert "colorchannelmixer=aa=0.8" in seg1
        assert "fade=t=in" in seg1
        assert ":st=2" in seg1 or "st=2.0" in seg1
        assert ":d=0.5" in seg1
        assert ":alpha=1" in seg1
        assert "fade=t=out" in seg1
        # fade_out start = end_s - fade_out_s = 7.0 - 0.5 = 6.5
        assert "st=6.5" in seg1 or "st=6" in seg1

    def test_exact_segment2_string_both_fades(self) -> None:
        """Segment 2 exact content matches V2-1 confirmed overlay composition.

        Expected:
          {base_label}[ov{i}]overlay=x='{x}':y='{y}':enable='between(t,{start_s},{end_s})'[outvimg{i}]
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_overlay_segment,
        )

        o = _make_image_overlay(
            start_s=2.0,
            end_s=7.0,
            x="(W-w)/2",
            y="(H-h)/2",
            scale=0.5,
            opacity=0.8,
            fade_in_s=0.5,
            fade_out_s=0.5,
            input_index=2,
        )
        segs, _ = _build_overlay_segment(o, base_label="[outv]", i=0)
        seg2 = segs[1]

        assert "[outv]" in seg2  # base_label
        assert "overlay=" in seg2
        assert "x='(W-w)/2'" in seg2
        assert "y='(H-h)/2'" in seg2
        assert "enable=" in seg2
        assert "between(t," in seg2


# ===========================================================================
# Section 4: _append_overlay_filter
# ===========================================================================


class TestAppendOverlayFilter:
    """_append_overlay_filter stacks overlays sequentially (ADR-OV-4 / V2-1)."""

    def test_empty_overlays_returns_video_map_label_unchanged(self) -> None:
        """empty overlays -> video_map_label unchanged (backward compat)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_overlay_filter,
        )

        filter_parts: list[str] = []
        result = _append_overlay_filter(filter_parts, "[outv]", [])
        assert result == "[outv]"
        assert filter_parts == []

    def test_single_overlay_produces_two_filter_parts(self) -> None:
        """One overlay produces 2 filter_parts entries."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_overlay_filter,
        )

        filter_parts: list[str] = []
        overlays = [_make_image_overlay(input_index=2)]
        _append_overlay_filter(filter_parts, "[outv]", overlays)
        assert len(filter_parts) == 2

    def test_two_overlays_produce_four_filter_parts(self) -> None:
        """Two overlays produce 4 filter_parts entries (2 per overlay)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_overlay_filter,
        )

        filter_parts: list[str] = []
        overlays = [
            _make_image_overlay(input_index=2),
            _make_image_overlay(
                image_path="/project/logo/b.png", input_index=3, start_s=5.0, end_s=8.0
            ),
        ]
        _append_overlay_filter(filter_parts, "[outv]", overlays)
        assert len(filter_parts) == 4

    def test_two_overlays_final_label_is_outvimg1(self) -> None:
        """Two overlays: final video_map_label is [outvimg1] (1-indexed end)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_overlay_filter,
        )

        filter_parts: list[str] = []
        overlays = [
            _make_image_overlay(input_index=2),
            _make_image_overlay(
                image_path="/project/logo/b.png", input_index=3, start_s=5.0, end_s=8.0
            ),
        ]
        final_label = _append_overlay_filter(filter_parts, "[outv]", overlays)
        assert final_label == "[outvimg1]"

    def test_overlays_stack_sequentially(self) -> None:
        """Second overlay uses first overlay's output label as its base_label."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_overlay_filter,
        )

        filter_parts: list[str] = []
        overlays = [
            _make_image_overlay(input_index=2),
            _make_image_overlay(
                image_path="/project/logo/b.png", input_index=3, start_s=5.0, end_s=8.0
            ),
        ]
        _append_overlay_filter(filter_parts, "[outv]", overlays)
        # The 3rd filter_part (segment 1 of 2nd overlay) should be independent;
        # the 4th filter_part (segment 2 of 2nd overlay) should reference [outvimg0].
        seg2_of_2nd = filter_parts[3]
        assert "[outvimg0]" in seg2_of_2nd, (
            f"Second overlay segment2 must use [outvimg0] as base: {seg2_of_2nd!r}"
        )


# ===========================================================================
# Section 5: RenderPlan.image_sources field (ADR-OV-5)
# ===========================================================================


class TestRenderPlanImageSources:
    """RenderPlan has image_sources: list[str] with default_factory=list (ADR-OV-5)."""

    def test_render_plan_has_image_sources_field(self) -> None:
        """RenderPlan has image_sources attribute."""
        from clipwright_render.plan import RenderPlan

        # Build a minimal RenderPlan (all required fields)
        plan = RenderPlan(
            filter_complex="[0:v]copy[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
        )
        assert hasattr(plan, "image_sources"), (
            "RenderPlan must have image_sources field (ADR-OV-5)"
        )

    def test_render_plan_image_sources_default_is_empty_list(self) -> None:
        """RenderPlan.image_sources defaults to [] (backward compatible)."""
        from clipwright_render.plan import RenderPlan

        plan = RenderPlan(
            filter_complex="[0:v]copy[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
        )
        assert plan.image_sources == []

    def test_render_plan_image_sources_accepts_list_of_str(self) -> None:
        """RenderPlan.image_sources can hold a list of strings."""
        from clipwright_render.plan import RenderPlan

        plan = RenderPlan(
            filter_complex="[0:v]copy[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            image_sources=["/project/logo/a.png", "/project/logo/b.png"],
        )
        assert plan.image_sources == ["/project/logo/a.png", "/project/logo/b.png"]


# ===========================================================================
# Section 6: Stream index calculation (ADR-OV-5 / G4-confirmed)
# ===========================================================================


class TestStreamIndexCalculation:
    """image_index_base = len(input_sources) + (1 if bgm else 0) (ADR-OV-5/G4)."""

    def test_bgm_absent_index_base_is_len_input_sources(self) -> None:
        """BGM absent: image i index = len(input_sources) + i."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, image_path="logo/a.png", name="image_0")

        # With 1 input_source, image_index_base = 1 (no bgm)
        result = _collect_image_overlays(tl, image_index_base=1)
        assert len(result) == 1
        assert result[0].input_index == 1  # 1 + 0

    def test_bgm_present_index_base_is_len_plus_one(self) -> None:
        """BGM present: image i index = len(input_sources) + 1 + i."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(tl, image_path="logo/a.png", name="image_0")

        # With 1 input_source + 1 bgm, image_index_base = 2
        result = _collect_image_overlays(tl, image_index_base=2)
        assert len(result) == 1
        assert result[0].input_index == 2  # 1 + 1 + 0

    def test_two_sources_bgm_absent_index(self) -> None:
        """2 input sources, no BGM: image 0 index = 2, image 1 index = 3."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline(
            [
                _make_clip("/src/a.mp4", 0.0, 5.0),
                _make_clip("/src/b.mp4", 0.0, 5.0),
            ]
        )
        _add_image_overlay_marker(
            tl, image_path="logo/a.png", name="image_0", start_sec=0.0
        )
        _add_image_overlay_marker(
            tl, image_path="logo/b.png", name="image_1", start_sec=2.0
        )

        # 2 unique sources, no bgm → image_index_base = 2
        result = _collect_image_overlays(tl, image_index_base=2)
        assert len(result) == 2
        assert result[0].input_index == 2
        assert result[1].input_index == 3

    def test_two_sources_bgm_present_index(self) -> None:
        """2 input sources + BGM: image 0 index = 3 (2 sources + 1 bgm + 0)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline(
            [
                _make_clip("/src/a.mp4", 0.0, 5.0),
                _make_clip("/src/b.mp4", 0.0, 5.0),
            ]
        )
        _add_image_overlay_marker(
            tl, image_path="logo/a.png", name="image_0", start_sec=0.0
        )

        # 2 unique sources + bgm → image_index_base = 3
        result = _collect_image_overlays(tl, image_index_base=3)
        assert len(result) == 1
        assert result[0].input_index == 3


# ===========================================================================
# Section 7: render.py -i construction with image_sources
# ===========================================================================


class TestRenderInputConstruction:
    """Image inputs appended AFTER bgm; NO -stream_loop on images (ADR-OV-5)."""

    def _make_mock_plan(
        self,
        input_sources: list[str],
        bgm_source: str | None,
        image_sources: list[str],
    ) -> Any:
        """Create a mock RenderPlan with the given sources."""
        from clipwright_render.plan import RenderPlan

        return RenderPlan(
            filter_complex="[0:v]copy[outv]",
            ffmpeg_args=["-map", "[outv]", "-map", "0:a?"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=input_sources,
            bgm_source=bgm_source,
            image_sources=image_sources,
        )

    def test_image_inputs_appended_after_regular_sources(self) -> None:
        """Image -i flags appear after all input_sources (no bgm)."""
        from clipwright_render.render import _build_ffmpeg_inputs  # type: ignore[attr-defined]

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source=None,
            image_sources=["/project/logo/a.png"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        # Positions: ["-i", "/src/a.mp4", "-i", "/project/logo/a.png"]
        assert "-i" in inputs
        src_idx = inputs.index("-i")
        # The image must appear AFTER the source
        remaining = inputs[src_idx + 2 :]
        assert "/project/logo/a.png" in remaining, (
            f"Image must appear after sources in inputs: {inputs!r}"
        )

    def test_image_inputs_appended_after_bgm(self) -> None:
        """Image -i flags appear AFTER bgm (sources -> bgm -> images) (ADR-OV-5)."""
        from clipwright_render.render import _build_ffmpeg_inputs  # type: ignore[attr-defined]

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source="/project/bgm.mp3",
            image_sources=["/project/logo/a.png"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        # Expected order: ["-i", src, "-stream_loop", "-1", "-i", bgm, "-i", img]
        assert "/src/a.mp4" in inputs
        assert "/project/bgm.mp3" in inputs
        assert "/project/logo/a.png" in inputs

        src_pos = inputs.index("/src/a.mp4")
        bgm_pos = inputs.index("/project/bgm.mp3")
        img_pos = inputs.index("/project/logo/a.png")

        assert src_pos < bgm_pos < img_pos, (
            f"Order must be sources < bgm < images, got positions "
            f"src={src_pos} bgm={bgm_pos} img={img_pos} in {inputs!r}"
        )

    def test_no_stream_loop_on_image_inputs(self) -> None:
        """Image inputs do NOT have -stream_loop flag (unlike bgm)."""
        from clipwright_render.render import _build_ffmpeg_inputs  # type: ignore[attr-defined]

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source=None,
            image_sources=["/project/logo/a.png"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        # Find index of image path, check that -stream_loop does NOT immediately precede it
        img_pos = inputs.index("/project/logo/a.png")
        # At least 1 slot before must be "-i", not "-stream_loop"
        assert inputs[img_pos - 1] == "-i", (
            f"Image must be preceded by -i, not -stream_loop: {inputs!r}"
        )
        if img_pos >= 2:
            assert inputs[img_pos - 2] != "-stream_loop", (
                f"-stream_loop must not precede image: {inputs!r}"
            )

    def test_bgm_still_has_stream_loop(self) -> None:
        """BGM retains -stream_loop -1 even when image_sources present."""
        from clipwright_render.render import _build_ffmpeg_inputs  # type: ignore[attr-defined]

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source="/project/bgm.mp3",
            image_sources=["/project/logo/a.png"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        bgm_pos = inputs.index("/project/bgm.mp3")
        assert inputs[bgm_pos - 1] == "-i"
        assert inputs[bgm_pos - 2] == "-1"
        assert inputs[bgm_pos - 3] == "-stream_loop", (
            f"BGM must still have -stream_loop: {inputs!r}"
        )


# ===========================================================================
# Section 8: render.py co-location re-validation helpers
# ===========================================================================


class TestRenderCoLocationValidation:
    """render.py has _ALLOWED_IMAGE_EXTENSIONS; shared check_media_ref handles ref validation."""

    def test_allowed_image_extensions_importable(self) -> None:
        """_ALLOWED_IMAGE_EXTENSIONS is importable from clipwright_render.render."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _ALLOWED_IMAGE_EXTENSIONS,
        )

        assert _ALLOWED_IMAGE_EXTENSIONS is not None

    def test_allowed_image_extensions_contains_expected(self) -> None:
        """_ALLOWED_IMAGE_EXTENSIONS includes .png, .jpg, .jpeg, .webp."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _ALLOWED_IMAGE_EXTENSIONS,
        )

        assert ".png" in _ALLOWED_IMAGE_EXTENSIONS
        assert ".jpg" in _ALLOWED_IMAGE_EXTENSIONS
        assert ".jpeg" in _ALLOWED_IMAGE_EXTENSIONS
        assert ".webp" in _ALLOWED_IMAGE_EXTENSIONS

    def test_check_media_ref_importable_from_pathpolicy(self) -> None:
        """check_media_ref is importable from clipwright.pathpolicy (ADR-PP-1)."""
        from clipwright.pathpolicy import check_media_ref

        assert check_media_ref is not None

    def test_absolute_image_outside_timeline_dir_passes(self, tmp_path: Path) -> None:
        """Absolute image path outside timeline dir -> no error (ADR-PP-1 escape hatch).

        Under ADR-PP-1, check_media_ref allows absolute refs to existing real files
        regardless of their location relative to the timeline directory.
        """
        from clipwright.pathpolicy import check_media_ref

        timeline_dir = tmp_path / "project"
        timeline_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_image = outside_dir / "logo.png"
        outside_image.touch()

        # Must NOT raise: absolute ref to existing real file is always allowed
        check_media_ref(str(outside_image), timeline_dir, "image")

    def test_absolute_image_inside_timeline_dir_passes(self, tmp_path: Path) -> None:
        """Absolute image path inside timeline parent dir -> no error."""
        from clipwright.pathpolicy import check_media_ref

        timeline_dir = tmp_path / "project"
        timeline_dir.mkdir()
        image_path = timeline_dir / "logo.png"
        image_path.touch()

        # Must NOT raise
        check_media_ref(str(image_path), timeline_dir, "image")

    def test_absolute_image_in_subdir_passes(self, tmp_path: Path) -> None:
        """Absolute image in recursive subdir of timeline parent -> allowed."""
        from clipwright.pathpolicy import check_media_ref

        timeline_dir = tmp_path / "project"
        sub_dir = timeline_dir / "assets" / "logo"
        sub_dir.mkdir(parents=True)
        image_path = sub_dir / "logo.png"
        image_path.touch()

        # Must NOT raise
        check_media_ref(str(image_path), timeline_dir, "image")

    def test_nonexistent_absolute_image_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """Non-existent absolute image path -> PATH_NOT_ALLOWED (ADR-PP-1)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright.pathpolicy import check_media_ref

        timeline_dir = tmp_path / "project"
        timeline_dir.mkdir()
        # File does not exist
        missing_image = str(tmp_path / "outside" / "logo.png")

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref(missing_image, timeline_dir, "image")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_relative_traversal_outside_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """Relative image path traversing outside timeline dir -> PATH_NOT_ALLOWED (CWE-22)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright.pathpolicy import check_media_ref

        timeline_dir = tmp_path / "project"
        timeline_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "logo.png").touch()

        # Relative path that traverses above timeline_dir -> PATH_NOT_ALLOWED (CWE-22)
        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref("../outside/logo.png", timeline_dir, "image")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_round_trip_different_dir_same_relative_position(
        self, tmp_path: Path
    ) -> None:
        """V2-3 round-trip: moving timeline+image together preserves relative path -> OK.

        Simulate: annotate dir and render dir differ, but image stays co-located
        (relative position preserved). check_media_ref should NOT raise
        (DC-GP-001/DC-AS-003).
        """
        from clipwright.pathpolicy import check_media_ref

        # "Moved" to dir B: both timeline and image move together
        dir_b = tmp_path / "proj_b"
        (dir_b / "logo").mkdir(parents=True)
        image_b = dir_b / "logo" / "overlay.png"
        image_b.touch()

        # Must NOT raise: image is inside dir_b's tree (absolute path, exists)
        check_media_ref(str(image_b), dir_b, "image")

    def test_relative_image_within_timeline_dir_passes(self, tmp_path: Path) -> None:
        """Relative image path within timeline dir -> allowed (no CWE-22 violation)."""
        from clipwright.pathpolicy import check_media_ref

        timeline_dir = tmp_path / "project"
        (timeline_dir / "logo").mkdir(parents=True)
        (timeline_dir / "logo" / "overlay.png").touch()

        # Relative path within the boundary -> no error
        check_media_ref("logo/overlay.png", timeline_dir, "image")


# ===========================================================================
# Section 9: V2-7 corrupt image -> SUBPROCESS_FAILED (mock-only)
# ===========================================================================


class TestCorruptImageSubprocessFailed:
    """Corrupt/unsupported image -> SUBPROCESS_FAILED with safe error message (V2-7)."""

    def test_subprocess_failed_error_code(self, tmp_path: Path) -> None:
        """ffmpeg failure with corrupt image -> ErrorCode.SUBPROCESS_FAILED."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_plan  # type: ignore[attr-defined]

        # Build a minimal plan with an image source
        plan = RenderPlan(
            filter_complex="[0:v][1:v]overlay=x='0':y='0'[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=["/project/a.mp4"],
            image_sources=["/project/logo/corrupt.png"],
        )

        # Mock the ffmpeg subprocess to fail (simulating corrupt image decode error)
        with patch("clipwright_render.render.run") as mock_run:
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="corrupt.png",
                hint=(
                    "The overlay image may be corrupt or an unsupported format;"
                    " provide a valid .png/.jpg/.jpeg/.webp."
                ),
            )
            with pytest.raises(ClipwrightError) as exc_info:
                render_plan(plan, output=str(tmp_path / "out.mp4"))
        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_corrupt_image_message_masked_by_s2_redaction(self, tmp_path: Path) -> None:
        """Corrupt-image SUBPROCESS_FAILED message is masked by S2 redaction.

        ADR-SR-1 (architecture-report-20260717-163916.md S2.5 Consequences):
        render_plan's own ffmpeg run() call is subprocess seam S2. Once
        _sanitize_subprocess_error is wired in, any SUBPROCESS_FAILED/TIMEOUT
        raised from that run() call has its message replaced with
        safe_subprocess_message(exc) — code/hint stay unchanged. This is the
        ONLY existing test in this batch whose expectation changed (the prior
        version asserted the mocked message kept the image basename; that
        basename now flows through S2 masking and must NOT appear).

        Regression guard: verifies that S2 masking in render_plan correctly
        replaces the mocked SUBPROCESS_FAILED message with safe_subprocess_message,
        ensuring basename ("corrupt.png") and parent path are both redacted.
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright.process import safe_subprocess_message
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_plan  # type: ignore[attr-defined]

        plan = RenderPlan(
            filter_complex="[0:v][1:v]overlay=x='0':y='0'[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=["/project/a.mp4"],
            image_sources=["/project/logo/corrupt.png"],
        )

        raw_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message="corrupt.png",
            hint=(
                "The overlay image may be corrupt or an unsupported format;"
                " provide a valid .png/.jpg/.jpeg/.webp."
            ),
        )
        with patch("clipwright_render.render.run") as mock_run:
            mock_run.side_effect = raw_exc
            with pytest.raises(ClipwrightError) as exc_info:
                render_plan(plan, output=str(tmp_path / "out.mp4"))
        err = exc_info.value
        # Message must equal the shared safe_subprocess_message contract (ADR-SR-1).
        assert err.message == safe_subprocess_message(raw_exc), (
            "S2 seam must replace message with safe_subprocess_message"
            f" (ADR-SR-1); got: {err.message!r}"
        )
        # Neither the image basename NOR the parent directory path may leak (CWE-209).
        assert "corrupt.png" not in err.message
        assert "/project/logo" not in err.message

    def test_corrupt_image_hint_mentions_valid_formats(self, tmp_path: Path) -> None:
        """Corrupt image error hint mentions .png/.jpg/.jpeg/.webp (V2-7)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_plan  # type: ignore[attr-defined]

        plan = RenderPlan(
            filter_complex="[0:v][1:v]overlay=x='0':y='0'[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=["/project/a.mp4"],
            image_sources=["/project/logo/corrupt.png"],
        )

        with patch("clipwright_render.render.run") as mock_run:
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="corrupt.png",
                hint=(
                    "The overlay image may be corrupt or an unsupported format;"
                    " provide a valid .png/.jpg/.jpeg/.webp."
                ),
            )
            with pytest.raises(ClipwrightError) as exc_info:
                render_plan(plan, output=str(tmp_path / "out.mp4"))
        err = exc_info.value
        assert ".png" in err.hint or "png" in err.hint.lower()
        assert ".jpg" in err.hint or "jpg" in err.hint.lower()
        assert ".webp" in err.hint or "webp" in err.hint.lower()


# ===========================================================================
# Section 10: _verify_image_magic direct unit tests (SR-M-1 / CWE-209)
# ===========================================================================


class TestVerifyImageMagic:
    """Direct unit tests for _verify_image_magic (SR-AS-002 / CWE-209 guard)."""

    def test_valid_png_header_passes(self, tmp_path: Path) -> None:
        """A file with a valid PNG magic header does not raise."""
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "logo.png"
        # Real PNG magic: \x89PNG\r\n\x1a\n (8 bytes) + 4 padding bytes
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)
        _verify_image_magic(str(img))  # must not raise

    def test_valid_jpeg_header_passes(self, tmp_path: Path) -> None:
        """A file with a valid JPEG magic header does not raise."""
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "logo.jpg"
        # Real JPEG magic: \xff\xd8\xff (3 bytes) + padding
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 9)
        _verify_image_magic(str(img))  # must not raise

    def test_valid_webp_header_passes(self, tmp_path: Path) -> None:
        """A file with a valid WebP magic header does not raise."""
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "logo.webp"
        # Real WebP magic: RIFF (4 bytes) + file-size (4 bytes) + WEBP (4 bytes)
        img.write_bytes(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP")
        _verify_image_magic(str(img))  # must not raise

    def test_garbage_bytes_raises_subprocess_failed(self, tmp_path: Path) -> None:
        """Garbage bytes -> ClipwrightError(SUBPROCESS_FAILED)."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "logo.png"
        img.write_bytes(b"GARBAGE_NOT_AN_IMAGE_\x00\x01\x02")
        with pytest.raises(ClipwrightError) as exc_info:
            _verify_image_magic(str(img))
        err = exc_info.value
        assert err.code == ErrorCode.SUBPROCESS_FAILED

    def test_garbage_bytes_message_contains_basename(self, tmp_path: Path) -> None:
        """Corrupt image error message contains the basename (not the full path)."""
        from clipwright.errors import ClipwrightError
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "secret_logo.png"
        img.write_bytes(b"GARBAGE_NOT_AN_IMAGE_\x00\x01\x02")
        with pytest.raises(ClipwrightError) as exc_info:
            _verify_image_magic(str(img))
        err = exc_info.value
        # basename must appear in the message
        assert "secret_logo.png" in err.message

    def test_garbage_bytes_message_excludes_full_path(self, tmp_path: Path) -> None:
        """Corrupt image error message must NOT contain the parent directory path (CWE-209)."""
        from clipwright.errors import ClipwrightError
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "secret_logo.png"
        img.write_bytes(b"GARBAGE_NOT_AN_IMAGE_\x00\x01\x02")
        with pytest.raises(ClipwrightError) as exc_info:
            _verify_image_magic(str(img))
        err = exc_info.value
        # parent directory path must NOT appear in the message
        assert str(tmp_path) not in err.message

    def test_garbage_bytes_hint_mentions_replacement(self, tmp_path: Path) -> None:
        """Corrupt image error hint provides actionable guidance."""
        from clipwright.errors import ClipwrightError
        from clipwright_render.render import _verify_image_magic  # type: ignore[attr-defined]

        img = tmp_path / "logo.png"
        img.write_bytes(b"GARBAGE_NOT_AN_IMAGE_\x00\x01\x02")
        with pytest.raises(ClipwrightError) as exc_info:
            _verify_image_magic(str(img))
        err = exc_info.value
        assert err.hint is not None and len(err.hint) > 0


# ===========================================================================
# Section 11: FR-2 image overlay output collision check (ADR-SR-1 / ADR-B8
# parity — architecture-report-20260717-163916.md S5.1)
# ===========================================================================


def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
    """Stub for process.run — always succeeds without calling ffmpeg.

    Mirrors test_pathpolicy_render.py's _fake_run (no cross-file import).
    """
    from subprocess import CompletedProcess

    return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _fake_resolve_tool(name: str, env_var: str | None = None) -> str:
    """Mirrors test_pathpolicy_render.py's _fake_resolve_tool (no cross-file import)."""
    return f"/usr/bin/{name}"


def _make_render_media_info(path: str) -> Any:
    """Minimal MediaInfo for a single-source render_timeline pipeline.

    Mirrors test_pathpolicy_render.py's _make_media_info (no cross-file
    import) — no fps_rate needed for a single-source plan.
    """
    from clipwright.schemas import MediaInfo, StreamInfo

    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=None,
        streams=[
            StreamInfo(
                index=0, codec_type="video", codec_name="h264", width=1920, height=1080
            ),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
        bit_rate=8_000_000,
    )


class TestImageOverlayOutputCollision:
    """FR-2 (Red): the image overlay loop must reject image_path == output_path
    with PATH_NOT_ALLOWED, mirroring the BGM precedent (render.py:753-757 /
    ADR-B8) via check_output_not_source(output_path, [img]) placed at the very
    top of the loop body (architecture-report-20260717-163916.md S5.1 / S8).

    The image boundary/existence/extension checks fire in the "6b. Execute"
    branch of _render_inner (render.py:1238-), so dry_run=False is required
    (same constraint documented by test_pathpolicy_render.py's PP-1d).

    Currently render.py:1238's image loop has NO check_output_not_source call,
    so the pathological image_path==output input falls through to the
    pre-existing existence check (FILE_NOT_FOUND) or extension check
    (INVALID_INPUT) instead of PATH_NOT_ALLOWED — Red until the collision
    check is added as the loop's first statement.
    """

    def test_image_path_equals_output_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """image_path == output (output not yet created) -> PATH_NOT_ALLOWED.

        Regression guard: verifies that check_output_not_source at the loop head
        catches the collision before the existence check, preventing FILE_NOT_FOUND
        and returning PATH_NOT_ALLOWED instead.
        """
        from clipwright.errors import ErrorCode
        from clipwright_render.render import render_timeline
        from clipwright_render.schemas import RenderOptions

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        src = str(project_dir / "clip.mp4")
        Path(src).touch()

        tl = _make_timeline([_make_clip(src, 0.0, 5.0)])
        # image_path (relative to timeline dir) reconstructs to the exact same
        # absolute path as output (V2-3 reconstruction) — the pathological
        # image_path == output_path collision.
        _add_image_overlay_marker(
            tl, image_path="out.mp4", start_sec=1.0, duration_sec=3.0
        )
        tl_path = project_dir / "tl.otio"
        otio.adapters.write_to_file(tl, str(tl_path))
        output = str(project_dir / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_render_media_info(src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
                dry_run=False,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED, (
            "image_path == output must be rejected as PATH_NOT_ALLOWED"
            f" (FR-2 / ADR-B8 parity); got: {result['error']}"
        )

    def test_image_path_equals_output_overwrite_variant_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """image_path == output, output pre-exists (overwrite=True) -> PATH_NOT_ALLOWED.

        With the output file already on disk, the existence check would pass
        and the (disjoint) extension check fires instead (INVALID_INPUT,
        since .mp4 is not in _ALLOWED_IMAGE_EXTENSIONS) — demonstrating why the
        collision check must sit ahead of BOTH pre-existing checks.

        Regression guard: verifies that check_output_not_source catches the
        collision even when the output file exists and would otherwise trigger
        the extension check, ensuring PATH_NOT_ALLOWED is returned consistently.
        """
        from clipwright.errors import ErrorCode
        from clipwright_render.render import render_timeline
        from clipwright_render.schemas import RenderOptions

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        src = str(project_dir / "clip.mp4")
        Path(src).touch()

        tl = _make_timeline([_make_clip(src, 0.0, 5.0)])
        _add_image_overlay_marker(
            tl, image_path="out.mp4", start_sec=1.0, duration_sec=3.0
        )
        tl_path = project_dir / "tl.otio"
        otio.adapters.write_to_file(tl, str(tl_path))
        output = str(project_dir / "out.mp4")
        # Pre-create the output file (overwrite scenario).
        Path(output).touch()

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_render_media_info(src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
                dry_run=False,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED, (
            "image_path == output must be rejected as PATH_NOT_ALLOWED even"
            f" when output pre-exists; got: {result['error']}"
        )

    def test_image_path_not_equal_output_still_passes(self, tmp_path: Path) -> None:
        """image_path != output (no collision) -> ok=True (green regression guard).

        FR-2 only changes the pathological image_path==output case; a normal,
        non-colliding image overlay must keep rendering successfully.
        """
        from clipwright_render.render import render_timeline
        from clipwright_render.schemas import RenderOptions

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        src = str(project_dir / "clip.mp4")
        Path(src).touch()
        image_file = project_dir / "logo.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)

        tl = _make_timeline([_make_clip(src, 0.0, 5.0)])
        _add_image_overlay_marker(
            tl, image_path="logo.png", start_sec=1.0, duration_sec=3.0
        )
        tl_path = project_dir / "tl.otio"
        otio.adapters.write_to_file(tl, str(tl_path))
        output = str(project_dir / "out.mp4")
        Path(output).touch()

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_render_media_info(src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
                dry_run=False,
            )

        assert result["ok"] is True, (
            f"Non-colliding image overlay must still succeed: {result.get('error')}"
        )


# ===========================================================================
# Section 12: FR-3 image fade fail-closed validation (ADR-SR-1 / PiP parity —
# architecture-report-20260717-163916.md S5.2 / S8)
# ===========================================================================


class TestImageFadeFailClosed:
    """FR-3 (Red): fade_in_sec/fade_out_sec must be validated isfinite -> range
    -> sum, matching the PiP fade validation (plan.py:997-1073), instead of
    silently dropping NaN/negative/inf fades.

    Currently _marker_to_image_overlay (plan.py) only checks
    fade_in_sec + fade_out_sec > duration_sec + 1e-9 (plan.py:567); there is no
    isfinite or per-field range guard. NaN and a finite negative fade slip
    through this single sum comparison silently (NaN comparisons are always
    False; a negative offset can shrink the sum below the threshold). This is
    Red until the isfinite (plan.py:532) and range (plan.py:566) checks are
    added. NOTE: a bare +inf value already trips the existing sum check today
    (inf > duration is True) via a DIFFERENT code path than the one FR-3
    introduces — those two parametrize cases are pre-existing green, not Red;
    see test-report for the itemised breakdown.
    """

    @pytest.mark.parametrize("fade_field", ["fade_in_sec", "fade_out_sec"])
    @pytest.mark.parametrize(
        "bad_value",
        [math.nan, -1.0, -math.inf, math.inf],
        ids=["nan", "neg1", "neginf", "posinf"],
    )
    def test_fail_closed_on_non_finite_or_negative_fade(
        self, fade_field: str, bad_value: float
    ) -> None:
        """fade_in/out_sec in {NaN, -1.0, -inf, +inf} -> INVALID_INPUT + hint.

        Currently Red for {nan, -1.0, -inf}: these values are silently
        accepted today (no isfinite/range guard exists yet), so
        _collect_image_overlays does NOT raise. The +inf sub-cases are
        already green today via the pre-existing sum-exceeds check (a
        different code path than the one FR-3 adds).
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        kwargs: dict[str, float] = {"fade_in_sec": 0.1, "fade_out_sec": 0.1}
        kwargs[fade_field] = bad_value
        _add_image_overlay_marker(tl, duration_sec=3.0, **kwargs)

        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.hint is not None and len(err.hint) > 0

    def test_fade_in_isfinite_message_and_hint_exact(self) -> None:
        """isfinite guard on fade_in_sec: exact message/hint (architecture §5.2 (a)).

        Regression guard: verifies that NaN fade_in_sec is fail-closed with
        INVALID_INPUT and the exact expected message/hint, preventing silent-drop
        (prior behavior of ignoring NaN fade values).
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=math.nan, fade_out_sec=0.1
        )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.message == (
            "The timeline contains an invalid image overlay: fade_in_sec is not finite."
        )
        assert err.hint == "Re-annotate with a finite fade_in_sec value."

    def test_fade_out_isfinite_message_and_hint_exact(self) -> None:
        """isfinite guard on fade_out_sec: exact message/hint (architecture §5.2 (a)).

        Regression guard: verifies that NaN fade_out_sec is fail-closed with
        INVALID_INPUT and the exact expected message/hint, preventing silent-drop.
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=0.1, fade_out_sec=math.nan
        )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.message == (
            "The timeline contains an invalid image overlay:"
            " fade_out_sec is not finite."
        )
        assert err.hint == "Re-annotate with a finite fade_out_sec value."

    def test_fade_in_range_message_and_hint_exact(self) -> None:
        """range guard on fade_in_sec: exact message/hint (architecture §5.2 (b)).

        Regression guard: verifies that negative fade_in_sec is fail-closed with
        INVALID_INPUT and the exact expected message/hint, even when sum with
        fade_out_sec does not exceed duration (range check precedes sum check).
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=-1.0, fade_out_sec=0.1
        )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.message == (
            "The timeline contains an invalid image overlay:"
            " fade_in_sec must be in [0, duration_sec]."
        )
        assert err.hint == (
            "Re-annotate with a fade_in_sec in the range [0, duration_sec]."
        )

    def test_fade_out_range_message_and_hint_exact(self) -> None:
        """range guard on fade_out_sec: exact message/hint (architecture §5.2 (b)).

        Regression guard: verifies that negative fade_out_sec is fail-closed with
        INVALID_INPUT and the exact expected message/hint, even when sum with
        fade_in_sec does not exceed duration (range check precedes sum check).
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=0.1, fade_out_sec=-1.0
        )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.message == (
            "The timeline contains an invalid image overlay:"
            " fade_out_sec must be in [0, duration_sec]."
        )
        assert err.hint == (
            "Re-annotate with a fade_out_sec in the range [0, duration_sec]."
        )

    def test_fade_in_equals_zero_boundary_still_green(self) -> None:
        """fade_in_sec == 0.0 remains accepted (boundary; architecture S8).

        Green today and must remain green after FR-3 (0 <= 0.0 <= duration_sec).
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=0.0, fade_out_sec=0.5
        )
        result = _collect_image_overlays(tl, image_index_base=1)
        assert len(result) == 1

    def test_fade_in_equals_duration_boundary_still_green(self) -> None:
        """fade_in_sec == duration_sec remains accepted (boundary; architecture S8).

        Green today and must remain green after FR-3
        (0 <= duration_sec <= duration_sec).
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=3.0, fade_out_sec=0.0
        )
        result = _collect_image_overlays(tl, image_index_base=1)
        assert len(result) == 1

    def test_fade_out_equals_zero_boundary_still_green(self) -> None:
        """fade_out_sec == 0.0 remains accepted (boundary; architecture S8)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=0.5, fade_out_sec=0.0
        )
        result = _collect_image_overlays(tl, image_index_base=1)
        assert len(result) == 1

    def test_fade_out_equals_duration_boundary_still_green(self) -> None:
        """fade_out_sec == duration_sec remains accepted (boundary; architecture S8)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, duration_sec=3.0, fade_in_sec=0.0, fade_out_sec=3.0
        )
        result = _collect_image_overlays(tl, image_index_base=1)
        assert len(result) == 1

    def test_fade_sum_exceeds_duration_message_unchanged(self) -> None:
        """Existing combined fade-sum check keeps its original wording (plan.py:567).

        Architecture S2.5 Consequences: FR-3 only adds isfinite/range checks
        BEFORE this existing check; its message/hint text is unchanged. Green
        both before and after FR-3 (regression guard, not Red).
        """
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_image_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_image_overlay_marker(
            tl, fade_in_sec=2.0, fade_out_sec=2.0, duration_sec=3.0
        )
        with pytest.raises(ClipwrightError) as exc_info:
            _collect_image_overlays(tl, image_index_base=1)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.message == (
            "The timeline contains an invalid image overlay:"
            " fade_in_sec + fade_out_sec exceeds duration_sec."
        )
        assert err.hint == (
            "Re-annotate so that fade_in_sec + fade_out_sec <= duration_sec."
        )
