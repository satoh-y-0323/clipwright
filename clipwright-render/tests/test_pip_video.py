"""test_pip_video.py — Red-phase tests for PiP (Picture-in-Picture) video
compositing filtergraph in clipwright-render.

Target symbols (NOT YET IMPLEMENTED — this suite is expected to be Red):
  - PipOverlay (frozen dataclass in clipwright_render.plan)
  - _collect_pip_overlays(timeline, pip_index_base, timeline_path=None)
      -> list[PipOverlay]
  - _build_pip_video_segment(o, base_label, i) -> tuple[list[str], str]
  - _append_pip_video_filter(filter_parts, video_map_label, pips) -> str
  - RenderPlan.pip_sources: list[str] (new field)
  - clipwright_render.render._build_ffmpeg_inputs extended with a pip_sources
    category (input_sources -> bgm -> image_sources -> pip_sources)

Architecture authority: architecture-report-20260709-093022.md
  §2.2 (module design), ADR-PIP-7 (input order / index base), ADR-PIP-8
  (video filtergraph design — trim/setpts + RELATIVE fade timing).

Key differentiator vs. image_overlay (ADR-PIP-8): because PiP video is
trimmed with `trim=start=...:duration=...,setpts=PTS-STARTPTS`, the input is
re-based to t=0. Fade timing (`fade=t=in:st=...` / `fade=t=out:st=...`) must
therefore use TRIMMED-RELATIVE time (0 / duration_sec-fade_out_sec), NOT the
absolute program time (start_s / end_s-fade_out_s) that image_overlay uses
(no trim/setpts there because `-loop 1` inputs already start at t=0 program
time). Naively copying the image_overlay code would silently produce a fade
that fires at the wrong moment (or never, for placements with start_s>0)
without raising any error — this is exactly the pitfall ADR-PIP-8 calls out
as a developer footgun, so the tests here assert on it explicitly.

Test isolation:
  - All new symbols are imported INSIDE test functions/methods so ImportError
    does not break collection of the rest of the render suite.
  - Module-level sentinel + `pytest.mark.xfail(strict=True)` mirrors the
    established Red-confirmation pattern in this codebase (see
    test_image_overlay.py and .claude/agent-memory/wt_tester/MEMORY.md
    "frames server テストパターン" — xfail is the accepted way to report a
    clean Red state without noisy collection errors).
  - This file does not test PiP AUDIO mixing (_append_pip_audio_pipe /
    ADR-PIP-9) — that is covered separately by test_pip_audio.py. PipOverlay
    instances constructed here only exercise video-relevant fields.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest

# ---------------------------------------------------------------------------
# Guard: imports of new symbols are deferred to inside test bodies.
# Module-level sentinel records whether the PiP video extension exists yet.
# ---------------------------------------------------------------------------

_PLAN_HAS_PIP_OVERLAY: bool
try:
    from clipwright_render.plan import PipOverlay as _PO  # noqa: F401

    _PLAN_HAS_PIP_OVERLAY = True
except ImportError:
    _PLAN_HAS_PIP_OVERLAY = False

pytestmark = pytest.mark.xfail(
    not _PLAN_HAS_PIP_OVERLAY,
    strict=True,
    reason=(
        "PipOverlay/_collect_pip_overlays/_build_pip_video_segment/"
        "_append_pip_video_filter not found in clipwright_render.plan"
        " (PiP video extension not implemented yet — expected Red state"
        " per architecture-report-20260709-093022.md ADR-PIP-7/ADR-PIP-8)."
    ),
)

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_image_overlay.py — no cross-file import)
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


def _add_pip_overlay_marker(
    timeline: otio.schema.Timeline,
    *,
    media_path: str = "clips/pip.mp4",
    start_sec: float = 2.0,
    duration_sec: float = 4.0,
    media_start_sec: float = 0.0,
    x: str = "(W-w)/2",
    y: str = "(H-h)/2",
    scale: float = 0.3,
    opacity: float = 1.0,
    fade_in_sec: float = 0.3,
    fade_out_sec: float = 0.3,
    mix_audio: bool = False,
    audio_volume: float = 1.0,
    name: str = "pip_0",
) -> None:
    """Attach a pip_overlay marker directly to the first video track.

    Metadata shape mirrors AddPipOptions (ADR-PIP-3). The ducking sub-object
    is included for realism (matches what clipwright-overlay would actually
    write) but is not exercised by any assertion in this video-only suite.
    """
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
                "kind": "pip_overlay",
                "tool": "clipwright-overlay",
                "version": "0.1.0",
                "media_path": media_path,
                "start_sec": start_sec,
                "duration_sec": duration_sec,
                "media_start_sec": media_start_sec,
                "x": x,
                "y": y,
                "scale": scale,
                "opacity": opacity,
                "fade_in_sec": fade_in_sec,
                "fade_out_sec": fade_out_sec,
                "mix_audio": mix_audio,
                "audio_volume": audio_volume,
                "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
            }
        },
    )
    video_track.markers.append(marker)


def _make_pip_overlay(**kwargs: Any) -> Any:
    """Construct a PipOverlay using the plan module's frozen dataclass.

    Field names follow the ImageOverlay "_s" seconds-suffix convention
    (start_s / end_s / fade_in_s / fade_out_s) plus the two fields PiP needs
    that image_overlay does not: media_start_s (source read offset) and
    duration_s (trim duration = placement duration, per ADR-PIP-8).
    end_s = start_s + duration_s (used only for overlay enable=between()).
    """
    from clipwright_render.plan import PipOverlay  # type: ignore[attr-defined]

    defaults: dict[str, Any] = dict(
        media_path="/project/clips/pip.mp4",
        media_start_s=0.0,
        duration_s=4.0,
        start_s=2.0,
        end_s=6.0,
        x="(W-w)/2",
        y="(H-h)/2",
        scale=0.3,
        opacity=1.0,
        fade_in_s=0.3,
        fade_out_s=0.3,
        input_index=2,
    )
    defaults.update(kwargs)
    return PipOverlay(**defaults)


# ===========================================================================
# Section 1: PipOverlay frozen dataclass
# ===========================================================================


class TestPipOverlayDataclass:
    """PipOverlay is a frozen dataclass with the required fields (ADR-PIP-8)."""

    def test_pip_overlay_importable(self) -> None:
        """PipOverlay can be imported from clipwright_render.plan."""
        from clipwright_render.plan import PipOverlay  # type: ignore[attr-defined]

        assert PipOverlay is not None

    def test_pip_overlay_is_frozen(self) -> None:
        """PipOverlay is a frozen dataclass — mutation raises FrozenInstanceError."""
        o = _make_pip_overlay()
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            o.scale = 0.5  # type: ignore[misc]

    def test_pip_overlay_has_required_fields(self) -> None:
        """PipOverlay exposes media_path/media_start_s/duration_s/start_s/end_s/
        x/y/scale/opacity/fade_in_s/fade_out_s/input_index."""
        o = _make_pip_overlay(
            media_path="/p/a.mp4",
            media_start_s=1.5,
            duration_s=3.0,
            start_s=1.0,
            end_s=4.0,
            x="10",
            y="20",
            scale=0.4,
            opacity=0.9,
            fade_in_s=0.2,
            fade_out_s=0.2,
            input_index=3,
        )
        assert o.media_path == "/p/a.mp4"
        assert o.media_start_s == 1.5
        assert o.duration_s == 3.0
        assert o.start_s == 1.0
        assert o.end_s == 4.0
        assert o.x == "10"
        assert o.y == "20"
        assert o.scale == 0.4
        assert o.opacity == 0.9
        assert o.fade_in_s == 0.2
        assert o.fade_out_s == 0.2
        assert o.input_index == 3

    def test_pip_overlay_field_types(self) -> None:
        """PipOverlay field types: str/float*7/str/str/int (media_path, timing
        floats, x, y, int)."""
        o = _make_pip_overlay(input_index=5)
        assert isinstance(o.media_path, str)
        assert isinstance(o.media_start_s, float)
        assert isinstance(o.duration_s, float)
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
# Section 2: _collect_pip_overlays
# ===========================================================================


class TestCollectPipOverlays:
    """_collect_pip_overlays reads kind=='pip_overlay' markers (ADR-PIP-7)."""

    def test_empty_timeline_returns_empty_list(self) -> None:
        """Timeline with no markers -> empty list."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_pip_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        result = _collect_pip_overlays(tl, pip_index_base=1)
        assert result == []

    def test_non_pip_overlay_kind_ignored(self) -> None:
        """Markers with kind!='pip_overlay' (e.g. image_overlay) are excluded."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_pip_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        track = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        track.markers.append(
            otio.schema.Marker(
                name="image_0",
                marked_range=_tr(1.0, 2.0),
                metadata={"clipwright": {"kind": "image_overlay"}},
            )
        )
        result = _collect_pip_overlays(tl, pip_index_base=1)
        assert result == []

    def test_single_marker_produces_one_pip_overlay(self) -> None:
        """One pip_overlay marker yields one PipOverlay object."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_pip_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_pip_overlay_marker(
            tl, media_path="clips/pip.mp4", start_sec=1.0, duration_sec=3.0
        )
        result = _collect_pip_overlays(tl, pip_index_base=2)
        assert len(result) == 1

    def test_input_index_uses_base_plus_collection_order(self) -> None:
        """input_index = pip_index_base + i (0-indexed collection order)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_pip_overlays,
        )

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_pip_overlay_marker(tl, media_path="clips/a.mp4", name="pip_0")
        _add_pip_overlay_marker(
            tl, media_path="clips/b.mp4", name="pip_1", start_sec=6.0
        )

        result = _collect_pip_overlays(tl, pip_index_base=3)
        assert len(result) == 2
        assert result[0].input_index == 3
        assert result[1].input_index == 4


# ===========================================================================
# Section 3: pip_index_base calculation (ADR-PIP-7)
#   pip_index_base = len(input_sources) + (1 if bgm else 0) + len(image_sources)
# ===========================================================================


class TestPipIndexBaseCalculation:
    """pip_index_base combines input_sources / bgm / image_sources counts.

    Parametrized over combinations of (bgm present/absent) x
    (image_overlay present/absent) x multiple source counts, matching the
    formula from ADR-PIP-7 and mirroring image_overlay's own
    image_index_base = len(input_sources) + (1 if bgm else 0) pattern
    extended with + len(image_sources).
    """

    @pytest.mark.parametrize(
        "n_input_sources,has_bgm,n_image_sources,expected_base",
        [
            (1, False, 0, 1),  # no bgm, no image overlays
            (1, True, 0, 2),  # bgm only
            (1, False, 2, 3),  # image overlays only
            (2, True, 1, 4),  # bgm + image overlays, 2 sources
            (3, True, 3, 7),  # bgm + image overlays, 3 sources
            (1, False, 1, 2),  # single image overlay, no bgm
        ],
    )
    def test_pip_index_base_formula_matches_input_index(
        self,
        n_input_sources: int,
        has_bgm: bool,
        n_image_sources: int,
        expected_base: int,
    ) -> None:
        """ADR-PIP-7 formula value is exactly what _collect_pip_overlays uses
        as the first PiP input_index."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_pip_overlays,
        )

        computed_base = n_input_sources + (1 if has_bgm else 0) + n_image_sources
        assert computed_base == expected_base

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_pip_overlay_marker(tl, media_path="clips/pip.mp4", name="pip_0")

        result = _collect_pip_overlays(tl, pip_index_base=computed_base)
        assert len(result) == 1
        assert result[0].input_index == expected_base

    def test_two_pips_index_base_offset_by_collection_order(self) -> None:
        """Multiple PiP markers stack sequentially from the computed base."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _collect_pip_overlays,
        )

        # 2 input sources + bgm + 1 image overlay -> base = 2 + 1 + 1 = 4
        base = 2 + 1 + 1
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_pip_overlay_marker(tl, media_path="clips/a.mp4", name="pip_0")
        _add_pip_overlay_marker(
            tl, media_path="clips/b.mp4", name="pip_1", start_sec=6.0
        )

        result = _collect_pip_overlays(tl, pip_index_base=base)
        assert len(result) == 2
        assert result[0].input_index == 4
        assert result[1].input_index == 5


# ===========================================================================
# Section 4: _build_pip_video_segment — filtergraph chain (ADR-PIP-8)
# ===========================================================================


class TestBuildPipVideoSegment:
    """_build_pip_video_segment emits the ADR-PIP-8 confirmed filter chain."""

    def _make_both_fades(self) -> Any:
        return _make_pip_overlay(
            media_path="/project/clips/pip.mp4",
            media_start_s=1.0,
            duration_s=4.0,
            start_s=2.0,
            end_s=6.0,
            x="(W-w)/2",
            y="(H-h)/2",
            scale=0.3,
            opacity=0.9,
            fade_in_s=0.5,
            fade_out_s=0.5,
            input_index=2,
        )

    def test_build_pip_video_segment_importable(self) -> None:
        """_build_pip_video_segment can be imported from clipwright_render.plan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        assert _build_pip_video_segment is not None

    def test_returns_tuple_list_str(self) -> None:
        """_build_pip_video_segment returns (list[str], str)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        result = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert isinstance(result, tuple)
        assert len(result) == 2
        segs, new_label = result
        assert isinstance(segs, list)
        assert isinstance(new_label, str)

    def test_two_segments_emitted_for_both_fades(self) -> None:
        """With both fades > 0, two filter segment strings are emitted."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert len(segs) == 2

    # --- Segment 1: trim + setpts + scale + colorchannelmixer chain ---

    def test_segment1_starts_with_stream_ref(self) -> None:
        """Segment 1 starts with [{input_index}:v] stream reference."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(input_index=3)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert segs[0].startswith("[3:v]")

    def test_segment1_contains_trim_start_and_duration(self) -> None:
        """Segment 1 contains trim=start={media_start_sec}:duration={duration_sec}."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(media_start_s=1.5, duration_s=4.0, input_index=2)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert "trim=start=1.5:duration=4" in seg1, (
            f"Expected trim=start=1.5:duration=4 in segment 1: {seg1!r}"
        )

    def test_segment1_contains_setpts_pts_startpts(self) -> None:
        """Segment 1 contains setpts=PTS-STARTPTS (re-bases trimmed input to t=0)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert "setpts=PTS-STARTPTS" in segs[0]

    def test_segment1_uses_scale_iw_times_scale_colon_minus_2(self) -> None:
        """Segment 1 uses scale=iw*{scale}:-2 (NOT :-1 — matches image_overlay convention)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(scale=0.4, input_index=2)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert "scale=iw*0.4:-2" in seg1, f"Expected scale=iw*0.4:-2: {seg1!r}"
        assert ":-1" not in seg1, f"Found :-1 in segment 1 (should be :-2): {seg1!r}"

    def test_segment1_contains_format_rgba(self) -> None:
        """Segment 1 contains format=rgba (alpha channel for opacity/fade)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert "format=rgba" in segs[0]

    def test_segment1_colorchannelmixer_aa_is_constant_opacity(self) -> None:
        """Segment 1 uses colorchannelmixer=aa={opacity} as a CONSTANT value."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(opacity=0.9, input_index=2)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert "colorchannelmixer=aa=0.9" in seg1, (
            f"Expected colorchannelmixer=aa=0.9 in segment 1: {seg1!r}"
        )
        assert "if(" not in seg1, f"aa= must be constant, not time-varying: {seg1!r}"

    def test_segment1_intermediate_label_is_pipv_i(self) -> None:
        """Segment 1 output label is [pipv{i}] (PiP-specific label, distinct
        from image_overlay's [ov{i}])."""
        import re

        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(input_index=2)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert re.search(r"\[pipv\d+\]$", seg1), (
            f"Segment 1 must end with [pipv{{i}}]: {seg1!r}"
        )

    # --- Segment 2: overlay composition ---

    def test_segment2_contains_overlay_filter(self) -> None:
        """Segment 2 contains 'overlay=' filter."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert "overlay=" in segs[1]

    def test_segment2_x_y_are_single_quoted(self) -> None:
        """Segment 2 x/y are SINGLE-QUOTED in the overlay filter (matches
        image_overlay V2-6 convention)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(x="(W-w)/2", y="(H-h)/2", input_index=2)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg2 = segs[1]
        assert "x='(W-w)/2'" in seg2
        assert "y='(H-h)/2'" in seg2

    def test_segment2_enable_between_with_start_and_end(self) -> None:
        """Segment 2 enable='between(t,{start_s},{end_s})' with placement times
        (NOT trimmed-relative — the enable gate operates on program time, unlike
        the fade st= values)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(start_s=2.0, end_s=6.0, input_index=2)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg2 = segs[1]
        assert "enable=" in seg2
        assert "between(t,2,6)" in seg2

    def test_segment2_uses_base_label(self) -> None:
        """Segment 2 starts with the provided base_label."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        segs, _ = _build_pip_video_segment(o, base_label="[outvimg0]", i=0)
        assert segs[1].startswith("[outvimg0]")

    def test_segment2_output_label_is_outvpip_i(self) -> None:
        """Segment 2 output label matches [outvpip{i}] pattern (distinct from
        image_overlay's [outvimg{i}])."""
        import re

        o = self._make_both_fades()
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        segs, new_label = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert re.search(r"\[outvpip\d+\]$", segs[1]), (
            f"Segment 2 must end with [outvpip{{i}}]: {segs[1]!r}"
        )
        assert re.match(r"^\[outvpip\d+\]$", new_label), (
            f"new_label must be [outvpip{{i}}]: {new_label!r}"
        )

    def test_new_label_matches_segment2_output(self) -> None:
        """new_label returned equals the [outvpip{i}] label at end of segment 2."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = self._make_both_fades()
        segs, new_label = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert segs[1].endswith(new_label)


# ===========================================================================
# Section 5: fade timing is TRIMMED-RELATIVE, not absolute (ADR-PIP-8)
# ===========================================================================


class TestPipFadeRelativeTiming:
    """PiP fade st= values are relative to the trimmed/re-based clip (t=0
    after setpts=PTS-STARTPTS), NOT the absolute program time used by
    image_overlay.

    This is the explicit developer-footgun called out in ADR-PIP-8: naively
    copying image_overlay's `fade=t=in:st={start_s}` /
    `fade=t=out:st={end_s-fade_out_s}` would use the wrong time base for PiP
    because the input stream itself has already been shifted to t=0 by trim+
    setpts. The correct PiP values are st=0 (fade-in) and
    st=duration_sec-fade_out_sec (fade-out).
    """

    def test_fade_in_st_is_relative_zero_not_absolute_start(self) -> None:
        """fade=t=in:st=0 regardless of start_s (placement start on the
        program timeline)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(
            start_s=10.0, end_s=14.0, duration_s=4.0, fade_in_s=0.5, fade_out_s=0.0
        )
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert "fade=t=in:st=0:" in seg1, (
            f"PiP fade-in st must be 0 (trimmed-relative), not absolute"
            f" start_s=10: {seg1!r}"
        )
        # The image_overlay-style (WRONG for PiP) absolute value must NOT appear.
        assert "st=10:" not in seg1, (
            f"fade-in st must not use absolute start_s (image_overlay-style"
            f" bug): {seg1!r}"
        )

    def test_fade_out_st_uses_duration_minus_fadeout_not_end_minus_fadeout(
        self,
    ) -> None:
        """fade=t=out:st={duration_sec-fade_out_sec}, NOT {end_s-fade_out_s}
        (the image_overlay formula)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(
            start_s=10.0, end_s=14.0, duration_s=4.0, fade_in_s=0.0, fade_out_s=1.0
        )
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        # Correct (PiP / trimmed-relative): duration_s - fade_out_s = 4 - 1 = 3
        assert "fade=t=out:st=3:" in seg1, (
            f"PiP fade-out st must be duration_s-fade_out_s=3"
            f" (trimmed-relative): {seg1!r}"
        )
        # Wrong (image_overlay-style, using absolute end_s): 14 - 1 = 13
        assert "st=13:" not in seg1, (
            f"fade-out st must not use absolute end_s-fade_out_s (image_overlay"
            f"-style bug): {seg1!r}"
        )

    def test_fade_in_st_zero_holds_across_varying_placement_start(self) -> None:
        """fade-in st=0 holds even as start_s varies (parametrized sanity check)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        for start_s in (0.0, 3.0, 25.5):
            o = _make_pip_overlay(
                start_s=start_s,
                end_s=start_s + 4.0,
                duration_s=4.0,
                fade_in_s=0.3,
                fade_out_s=0.0,
            )
            segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
            assert "fade=t=in:st=0:" in segs[0], (
                f"fade-in st must stay 0 regardless of start_s={start_s}: {segs[0]!r}"
            )


# ===========================================================================
# Section 6: fade omission when zero (mirrors image_overlay convention)
# ===========================================================================


class TestPipFadeOmittedWhenZero:
    """_build_pip_video_segment OMITS fade stages when the corresponding
    duration is 0 (avoids passing d=0 to ffmpeg's fade filter — same
    convention as image_overlay's V2-1 degenerate-d=0 avoidance)."""

    def test_fade_in_omitted_when_zero(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(fade_in_s=0.0, fade_out_s=0.3)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert "fade=t=in" not in segs[0], (
            f"fade=t=in should be OMITTED when fade_in_s==0: {segs[0]!r}"
        )

    def test_fade_out_omitted_when_zero(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(fade_in_s=0.3, fade_out_s=0.0)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        assert "fade=t=out" not in segs[0], (
            f"fade=t=out should be OMITTED when fade_out_s==0: {segs[0]!r}"
        )

    def test_both_fades_zero_no_fade_stages(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_pip_video_segment,
        )

        o = _make_pip_overlay(fade_in_s=0.0, fade_out_s=0.0)
        segs, _ = _build_pip_video_segment(o, base_label="[outv]", i=0)
        seg1 = segs[0]
        assert "fade=t=in" not in seg1
        assert "fade=t=out" not in seg1
        # Chain still ends at colorchannelmixer=aa={opacity}[pipv{i}]
        assert "colorchannelmixer=aa=" in seg1


# ===========================================================================
# Section 7: _append_pip_video_filter — sequential chaining (ADR-PIP-7/8)
# ===========================================================================


class TestAppendPipVideoFilter:
    """_append_pip_video_filter chains PiP overlays sequentially:
    [outv] -> [outvpip0] -> [outvpip1] -> ...; no-op for empty pips list
    (backward compatible / byte-identical output)."""

    def test_empty_pips_returns_video_map_label_unchanged(self) -> None:
        """Empty pips -> video_map_label unchanged and filter_parts untouched
        (backward compat: byte-identical to a render with no PiP)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_video_filter,
        )

        filter_parts: list[str] = []
        result = _append_pip_video_filter(filter_parts, "[outv]", [])
        assert result == "[outv]"
        assert filter_parts == []

    def test_empty_pips_preserves_preexisting_filter_parts_byte_identical(
        self,
    ) -> None:
        """When filter_parts already has entries (e.g. from image_overlay),
        an empty pips list must not mutate or append to them."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_video_filter,
        )

        existing = [
            "[0:v]scale=iw*0.5:-2[ov0]",
            "[outv][ov0]overlay=x='0':y='0'[outvimg0]",
        ]
        filter_parts = list(existing)
        result = _append_pip_video_filter(filter_parts, "[outvimg0]", [])
        assert result == "[outvimg0]"
        assert filter_parts == existing

    def test_single_pip_produces_two_filter_parts(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_video_filter,
        )

        filter_parts: list[str] = []
        pips = [_make_pip_overlay(input_index=2)]
        _append_pip_video_filter(filter_parts, "[outv]", pips)
        assert len(filter_parts) == 2

    def test_two_pips_produce_four_filter_parts(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_video_filter,
        )

        filter_parts: list[str] = []
        pips = [
            _make_pip_overlay(input_index=2),
            _make_pip_overlay(
                media_path="/project/clips/b.mp4",
                input_index=3,
                start_s=8.0,
                end_s=12.0,
            ),
        ]
        _append_pip_video_filter(filter_parts, "[outv]", pips)
        assert len(filter_parts) == 4

    def test_two_pips_chain_labels_outvpip0_then_outvpip1(self) -> None:
        """[outv] -> [outvpip0] -> [outvpip1] sequential chaining."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_video_filter,
        )

        filter_parts: list[str] = []
        pips = [
            _make_pip_overlay(input_index=2),
            _make_pip_overlay(
                media_path="/project/clips/b.mp4",
                input_index=3,
                start_s=8.0,
                end_s=12.0,
            ),
        ]
        _append_pip_video_filter(filter_parts, "[outv]", pips)
        # Segment 2 of the second PiP (filter_parts[3]) must reference the
        # first PiP's output label [outvpip0] as its base.
        seg2_of_2nd = filter_parts[3]
        assert "[outvpip0]" in seg2_of_2nd, (
            f"Second PiP segment2 must chain from [outvpip0]: {seg2_of_2nd!r}"
        )

    def test_two_pips_final_label_is_outvpip1(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_video_filter,
        )

        filter_parts: list[str] = []
        pips = [
            _make_pip_overlay(input_index=2),
            _make_pip_overlay(
                media_path="/project/clips/b.mp4",
                input_index=3,
                start_s=8.0,
                end_s=12.0,
            ),
        ]
        final_label = _append_pip_video_filter(filter_parts, "[outv]", pips)
        assert final_label == "[outvpip1]"


# ===========================================================================
# Section 8: render.py -i construction with pip_sources (ADR-PIP-7)
# ===========================================================================


class TestBuildFfmpegInputsPipOrder:
    """Pip inputs appended AFTER image_sources; NO -loop 1 on pip inputs
    (unlike image_sources — PiP is real video, so ffmpeg reads its own
    timestamps rather than needing to be looped/re-timed)."""

    def _make_mock_plan(
        self,
        input_sources: list[str],
        bgm_source: str | None,
        image_sources: list[str],
        pip_sources: list[str],
    ) -> Any:
        """Create a mock RenderPlan with the given sources, including the
        not-yet-existing pip_sources field (expected to raise TypeError until
        RenderPlan.pip_sources is implemented — that failure is the Red
        signal for this section)."""
        from clipwright_render.plan import RenderPlan

        return RenderPlan(
            filter_complex="[0:v]copy[outv]",
            ffmpeg_args=["-map", "[outv]", "-map", "0:a?"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=input_sources,
            bgm_source=bgm_source,
            image_sources=image_sources,
            pip_sources=pip_sources,  # type: ignore[call-arg]
        )

    def test_pip_inputs_appended_after_image_sources(self) -> None:
        """Pip -i flags appear after image_sources -i flags."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source=None,
            image_sources=["/project/logo/a.png"],
            pip_sources=["/project/clips/pip.mp4"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        img_pos = inputs.index("/project/logo/a.png")
        pip_pos = inputs.index("/project/clips/pip.mp4")
        assert img_pos < pip_pos, (
            f"pip_sources must appear after image_sources: {inputs!r}"
        )

    def test_full_order_sources_bgm_image_pip(self) -> None:
        """Full order: input_sources -> bgm -> image_sources -> pip_sources."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source="/project/bgm.mp3",
            image_sources=["/project/logo/a.png"],
            pip_sources=["/project/clips/pip.mp4"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        src_pos = inputs.index("/src/a.mp4")
        bgm_pos = inputs.index("/project/bgm.mp3")
        img_pos = inputs.index("/project/logo/a.png")
        pip_pos = inputs.index("/project/clips/pip.mp4")

        assert src_pos < bgm_pos < img_pos < pip_pos, (
            f"Order must be sources < bgm < images < pip, got positions "
            f"src={src_pos} bgm={bgm_pos} img={img_pos} pip={pip_pos} in"
            f" {inputs!r}"
        )

    def test_no_loop_1_on_pip_inputs(self) -> None:
        """Pip inputs do NOT have -loop 1 (unlike image_sources — real video,
        not a still image)."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source=None,
            image_sources=[],
            pip_sources=["/project/clips/pip.mp4"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        pip_pos = inputs.index("/project/clips/pip.mp4")
        assert inputs[pip_pos - 1] == "-i", (
            f"Pip source must be preceded directly by -i, not -loop: {inputs!r}"
        )
        if pip_pos >= 2:
            assert inputs[pip_pos - 2] != "-loop", (
                f"-loop must not precede pip input: {inputs!r}"
            )
        if pip_pos >= 3:
            assert inputs[pip_pos - 3] != "-loop", (
                f"-loop 1 must not precede pip input: {inputs!r}"
            )

    def test_image_sources_still_have_loop_1_when_pip_present(self) -> None:
        """image_sources retain -loop 1 even when pip_sources is also present
        (regression guard for the extension not disturbing existing behaviour)."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source=None,
            image_sources=["/project/logo/a.png"],
            pip_sources=["/project/clips/pip.mp4"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        img_pos = inputs.index("/project/logo/a.png")
        assert inputs[img_pos - 1] == "-i"
        assert inputs[img_pos - 2] == "1"
        assert inputs[img_pos - 3] == "-loop", (
            f"image_sources must still have -loop 1 when pip_sources present:"
            f" {inputs!r}"
        )

    def test_bgm_still_has_stream_loop_when_pip_present(self) -> None:
        """BGM retains -stream_loop -1 even when pip_sources is present."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source="/project/bgm.mp3",
            image_sources=[],
            pip_sources=["/project/clips/pip.mp4"],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

        bgm_pos = inputs.index("/project/bgm.mp3")
        assert inputs[bgm_pos - 1] == "-i"
        assert inputs[bgm_pos - 2] == "-1"
        assert inputs[bgm_pos - 3] == "-stream_loop", (
            f"BGM must still have -stream_loop when pip_sources present: {inputs!r}"
        )

    def test_empty_pip_sources_backward_compatible(self) -> None:
        """pip_sources=[] does not add any -i flags beyond the existing
        sources/bgm/image_sources (backward compatibility)."""
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = self._make_mock_plan(
            input_sources=["/src/a.mp4"],
            bgm_source=None,
            image_sources=["/project/logo/a.png"],
            pip_sources=[],
        )
        inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)
        assert inputs.count("-i") == 2  # one for source, one for image


# ===========================================================================
# Section 9: patch-based sanity check (ensures no accidental import-time
# side effects on clipwright_render.render when pip_sources is present)
# ===========================================================================


class TestPipDoesNotBreakExistingRenderPath:
    """Presence of an empty pip_sources field must not alter the ffmpeg
    invocation for a plan with no PiP overlays (backward-compat guard)."""

    def test_render_plan_run_not_called_by_input_builder(self) -> None:
        """_build_ffmpeg_inputs is a pure function; it must not invoke run()."""
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import (  # type: ignore[attr-defined]
            _build_ffmpeg_inputs,
        )

        plan = RenderPlan(
            filter_complex="[0:v]copy[outv]",
            ffmpeg_args=["-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=["/src/a.mp4"],
            pip_sources=[],  # type: ignore[call-arg]
        )
        with patch("clipwright_render.render.run") as mock_run:
            _build_ffmpeg_inputs(plan, hw_decode_value=None)
            mock_run.assert_not_called()
