"""test_reframe_integration.py — W3-Red tests for reframe integration.

Target: build_plan / _build_filter_complex reframe wiring + render.py passthrough.

These tests are written BEFORE the wiring exists (W3-Red phase).  All tests must
FAIL because:
  - build_plan() does not yet accept a ``reframe`` keyword argument.
  - _build_filter_complex() does not yet accept a ``reframe`` keyword argument.
  - render.py does not yet read clipwright_meta["reframe"] or forward it to
    build_plan().

Expected failure mode: TypeError ("unexpected keyword argument 'reframe'") for
build_plan / _build_filter_complex tests, and AssertionError for render.py tests
(reframe stage absent from filter_complex).

Architecture reference: architecture-report-20260621-004050.md §4/§5/§7/§8/§9
Plan reference: plan-report-20260621-004050.md W3-Red (test-render-plan)
Requirements: FR-3/FR-4/NFR-4, AC-07~13/16
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import set_clipwright_metadata
from clipwright.schemas import MediaInfo, StreamInfo

from clipwright_render.plan import KeptRange, ProbeInfo
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FPS = 30.0
_W = 1080  # target width  (9:16 vertical video)
_H = 1920  # target height

# D3 directive dict — both-sides contract (mirrors ReframeDirective key set)
_D3_DICT: dict[str, Any] = {
    "tool": "clipwright-reframe",
    "version": "0.1.0",
    "kind": "reframe",
    "target_w": _W,
    "target_h": _H,
    "mode": "pad",
    "anchor": "center",
    "pad_color": "black",
}

_D3_CROP: dict[str, Any] = {**_D3_DICT, "mode": "crop"}
_D3_BLUR: dict[str, Any] = {**_D3_DICT, "mode": "blur_pad"}


# ---------------------------------------------------------------------------
# Helpers: OTIO / ranges construction
# ---------------------------------------------------------------------------


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


def _make_single_source_timeline(
    source: str = "/fake/src.mp4",
    start: float = 0.0,
    duration: float = 5.0,
) -> otio.schema.Timeline:
    """Build a single-video-track Timeline with one clip."""
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    track.append(_make_clip(source, start, duration))
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _make_ranges(
    source: str = "/fake/src.mp4",
    start: float = 0.0,
    duration: float = 5.0,
) -> list[KeptRange]:
    """Build a minimal single-source KeptRange list (plain list, no _timeline)."""
    return [
        KeptRange(
            source=source,
            source_range=_tr(start, duration),
        )
    ]


def _make_probe(
    *,
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


def _make_media_info(
    path: str = "/fake/src.mp4",
    *,
    bit_rate: int | None = 8_000_000,
    has_video: bool = True,
    audio_streams: int = 1,
) -> MediaInfo:
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=None,
        streams=streams,
        bit_rate=bit_rate,
    )


def _write_timeline_with_reframe(
    path: Path,
    source: str,
    reframe_dict: dict[str, Any],
) -> None:
    """Write an OTIO timeline to disk with a reframe directive in metadata."""
    tl = _make_single_source_timeline(source)
    set_clipwright_metadata(tl, {"reframe": reframe_dict})
    otio.adapters.write_to_file(tl, str(path))


def _write_plain_timeline(path: Path, source: str) -> None:
    """Write an OTIO timeline without any reframe directive."""
    tl = _make_single_source_timeline(source)
    otio.adapters.write_to_file(tl, str(path))


# ---------------------------------------------------------------------------
# AC-07 / AC-08 / AC-09: build_plan with reframe produces reframe stage
# (architecture §4.2)
# ---------------------------------------------------------------------------


class TestBuildPlanReframeStageEmitted:
    """build_plan(..., reframe=D3_dict) emits the reframe filter stage.

    Verifies that the reframe stage appears in filter_complex (AC-07/08/09).
    These tests fail because build_plan does not yet accept 'reframe' kwarg (W3-Red).
    """

    def test_pad_mode_produces_reframe_filter_in_filter_complex(self) -> None:
        """reframe=D3(pad) -> filter_complex contains pad-based reframe segment.

        Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
        """
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)

        # Should contain setsar=1[outvrf] — the terminal reframe label
        assert "[outvrf]" in plan.filter_complex

    def test_crop_mode_produces_reframe_filter_in_filter_complex(self) -> None:
        """reframe=D3(crop) -> filter_complex contains crop-based reframe segment.

        Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
        """
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_CROP)

        assert "[outvrf]" in plan.filter_complex
        assert "crop" in plan.filter_complex

    def test_blur_pad_mode_produces_reframe_filter_in_filter_complex(self) -> None:
        """reframe=D3(blur_pad) -> filter_complex contains blur_pad reframe segments.

        Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
        """
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_BLUR)

        assert "[outvrf]" in plan.filter_complex
        assert "split=2" in plan.filter_complex
        assert "boxblur" in plan.filter_complex


# ---------------------------------------------------------------------------
# Insertion order: reframe before eq/subtitle/drawtext (architecture §4.2/D4)
# ---------------------------------------------------------------------------


class TestBuildPlanReframeInsertionOrder:
    """reframe stage appears before eq / subtitle / drawtext in filter_complex.

    AC requirement: reframe is inserted concat-after, eq-before (D4).
    Tests use index comparison on the filter_complex string.
    Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
    """

    def test_reframe_appears_before_eq_in_filter_complex(self) -> None:
        """reframe stage index < eq stage index in filter_complex (D4)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        # color_eq produces the eq filter
        color_dict: dict[str, Any] = {
            "tool": "clipwright-color",
            "version": "0.1.0",
            "eq": {"brightness": 0.1, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0},
        }
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT, color=color_dict)

        fc = plan.filter_complex
        reframe_pos = fc.find("[outvrf]")
        eq_pos = fc.find("eq=")
        assert reframe_pos != -1, "reframe terminal label [outvrf] not found"
        assert eq_pos != -1, "eq filter not found"
        assert reframe_pos < eq_pos, (
            f"reframe stage (pos={reframe_pos}) must appear before eq stage"
            f" (pos={eq_pos}) in filter_complex"
        )

    def test_reframe_appears_before_subtitles_in_filter_complex(
        self, tmp_path: Path
    ) -> None:
        """reframe stage index < subtitles stage index in filter_complex (D4)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        sub_path = tmp_path / "subs.vtt"
        sub_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n")
        opts = RenderOptions(subtitle=SubtitleOptions(path=str(sub_path)))

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)

        fc = plan.filter_complex
        reframe_pos = fc.find("[outvrf]")
        subtitle_pos = fc.find("subtitles=")
        assert reframe_pos != -1, "reframe terminal label [outvrf] not found"
        # subtitle filter is only present when libass is available; skip when absent
        if subtitle_pos == -1:
            pytest.skip("subtitles filter not present (libass unavailable)")
        assert reframe_pos < subtitle_pos, (
            f"reframe (pos={reframe_pos}) must appear before subtitle"
            f" (pos={subtitle_pos})"
        )


# ---------------------------------------------------------------------------
# AC-11: use_scale suppression (architecture §8/D5)
# ---------------------------------------------------------------------------


class TestBuildPlanUseScaleSuppression:
    """width/height + reframe -> scale stage suppressed; warning added (AC-11).

    Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
    """

    def test_scale_stage_not_emitted_when_reframe_and_size_specified(self) -> None:
        """width/height + reframe present -> scale= not in filter_complex (D5)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        # Specify width/height alongside reframe — scale should be suppressed
        opts = RenderOptions(width=1080, height=1920)

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)

        # scale= from the regular fit-scale stage must NOT appear; reframe uses its
        # own scale embedded inside the reframe segment ([outvrf] terminal label).
        # The scale inside the reframe filter contains 'force_original_aspect_ratio'
        # whereas the fit-scale stage uses 'outvscaled' as terminal label.
        assert "[outvscaled]" not in plan.filter_complex, (
            "fit-scale stage [outvscaled] must not appear when reframe is present"
        )

    def test_warning_emitted_when_width_height_and_reframe_both_present(self) -> None:
        """width/height + reframe -> warning about ignored resolution (AC-11)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions(width=1920, height=1080)

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)

        # Warning must mention width/height being ignored and the fixed resolution
        matching = [
            w
            for w in plan.warnings
            if "ignored" in w.lower() or "width" in w.lower() or "height" in w.lower()
        ]
        assert len(matching) >= 1, (
            f"Expected a width/height-ignored warning; got warnings: {plan.warnings}"
        )
        # Must mention target dimensions
        full_text = " ".join(plan.warnings)
        assert str(_W) in full_text or str(_H) in full_text, (
            f"Expected target dimensions {_W}x{_H} in warnings; got: {plan.warnings}"
        )

    def test_exactly_one_scale_warning_when_width_height_and_reframe(self) -> None:
        """Exactly 1 scale-suppression warning when width/height + reframe (AC-11)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions(width=1920, height=1080)

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)

        scale_warnings = [
            w
            for w in plan.warnings
            if "ignored" in w.lower()
            and ("width" in w.lower() or "height" in w.lower())
        ]
        assert len(scale_warnings) == 1, (
            f"Expected exactly 1 scale warning; got: {plan.warnings}"
        )


# ---------------------------------------------------------------------------
# AC-16: subtitle frame_h = target_h when reframe present (architecture §4/AC-16)
# ---------------------------------------------------------------------------


class TestBuildPlanSubtitleFrameH:
    """When reframe is present, subtitle counter-scale uses target_h (AC-16).

    frame_h must equal reframe.target_h so that _build_force_style applies the
    correct counter-scale for libass's frame_h upscale.

    Validation strategy: compare the FontSize injected into force_style= with what
    _counter_scale(font_size, target_h) would produce vs. _counter_scale(font_size,
    probe_height). When frame_h == target_h, the injected value matches target_h-based
    counter-scaling (not probe_height-based).

    Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
    """

    def test_subtitle_force_style_fontsize_uses_target_h(self, tmp_path: Path) -> None:
        """FontSize in force_style= reflects counter-scale by target_h, not probe_h.

        With probe_h=1080 and target_h=1920:
          - frame_h=target_h=1920: FontSize = round(font_size * 288 / 1920)
          - frame_h=probe_h=1080:  FontSize = round(font_size * 288 / 1080) (wrong)
        The test verifies the filter uses the target_h-based value.
        """
        from clipwright_render.plan import build_plan, _counter_scale

        ranges = _make_ranges()
        probe = _make_probe(height=1080)  # probe height differs from target_h (1920)
        font_size = 48
        sub_path = tmp_path / "subs.vtt"
        sub_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n")
        opts = RenderOptions(
            subtitle=SubtitleOptions(path=str(sub_path), font_size=font_size)
        )

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)

        fc = plan.filter_complex
        if "subtitles=" not in fc:
            pytest.skip("subtitles filter not present (libass unavailable)")

        expected_fs_target = _counter_scale(font_size, _H)  # target_h = 1920
        wrong_fs_probe = _counter_scale(font_size, 1080)  # probe height

        # Must use target_h-based counter-scale, not probe-height-based
        assert f"FontSize={expected_fs_target}" in fc, (
            f"Expected FontSize={expected_fs_target} (target_h={_H}) in filter_complex."
            f" Got filter_complex={fc!r}"
        )
        # Must NOT use probe-height-based counter-scale (guard)
        if expected_fs_target != wrong_fs_probe:
            assert f"FontSize={wrong_fs_probe}" not in fc, (
                f"FontSize={wrong_fs_probe} (probe_h=1080) must NOT appear when"
                f" reframe target_h={_H} is present."
            )


# ---------------------------------------------------------------------------
# AC-13: crop warning (architecture §8/AC-13)
# ---------------------------------------------------------------------------


class TestBuildPlanCropWarning:
    """mode='crop' emits an information-loss warning (AC-13).

    Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
    """

    def test_crop_mode_emits_information_loss_warning(self) -> None:
        """mode='crop' -> 1 warning about cropped content (AC-13)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_CROP)

        crop_warnings = [w for w in plan.warnings if "crop" in w.lower()]
        assert len(crop_warnings) >= 1, (
            f"Expected a crop-information-loss warning; got warnings: {plan.warnings}"
        )

    def test_crop_warning_mentions_discarded(self) -> None:
        """crop warning must mention 'crop' and 'discard' (AC-13)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_CROP)

        crop_warnings = [w for w in plan.warnings if "crop" in w.lower()]
        assert len(crop_warnings) >= 1, f"Expected crop warning; got: {plan.warnings}"
        # Must mention that content is discarded
        assert any(
            "discard" in w.lower() or "cropped" in w.lower() for w in crop_warnings
        ), f"crop warning must mention discarding; got: {crop_warnings}"

    def test_pad_mode_does_not_emit_crop_warning(self) -> None:
        """mode='pad' (no crop) -> no crop-information-loss warning (AC-13 negative)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)  # mode=pad

        crop_warnings = [
            w for w in plan.warnings if "crop" in w.lower() and "discard" in w.lower()
        ]
        assert len(crop_warnings) == 0, (
            f"mode=pad must not emit crop warning; got: {plan.warnings}"
        )


# ---------------------------------------------------------------------------
# AC-12: multi-source + reframe -> UNSUPPORTED_OPERATION (architecture §5/D6)
# ---------------------------------------------------------------------------


class TestBuildPlanMultiSourceUnsupported:
    """Multiple input_sources + reframe directive -> UNSUPPORTED_OPERATION (AC-12).

    Validation order: _validate_reframe (INVALID_INPUT on bad directive) runs before
    multi-source check (UNSUPPORTED_OPERATION), so invalid directive wins even when
    multiple sources are present (§5.2).

    Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
    """

    def _make_two_source_ranges(self) -> list[KeptRange]:
        """Build ranges with two distinct source URLs."""
        return [
            KeptRange(source="/fake/a.mp4", source_range=_tr(0.0, 3.0)),
            KeptRange(source="/fake/b.mp4", source_range=_tr(0.0, 2.0)),
        ]

    def test_multi_source_with_reframe_raises_unsupported_operation(self) -> None:
        """2 sources + valid reframe -> UNSUPPORTED_OPERATION with hint (AC-12)."""
        from clipwright_render.plan import build_plan

        ranges = self._make_two_source_ranges()
        probe = _make_probe()
        source_probes = {
            "/fake/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/fake/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        opts = RenderOptions(fps=30.0, width=1920, height=1080)

        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe,
                opts,
                reframe=_D3_DICT,
                source_probes=source_probes,
            )

        err = exc_info.value
        assert err.code == ErrorCode.UNSUPPORTED_OPERATION
        # hint must mention single-source and suggest trimming/rendering first
        hint_lower = (err.hint or "").lower()
        assert "single" in hint_lower or "single-source" in hint_lower, (
            f"hint must mention single-source; got hint={err.hint!r}"
        )

    def test_multi_source_invalid_directive_raises_invalid_input_first(self) -> None:
        """Invalid reframe directive with multi-source -> INVALID_INPUT (before UNSUPPORTED).

        Validation order: _validate_reframe runs first regardless of source count (§5.2).
        An odd target_w must raise INVALID_INPUT even when multi-source.
        Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
        """
        from clipwright_render.plan import build_plan

        ranges = self._make_two_source_ranges()
        probe = _make_probe()
        source_probes = {
            "/fake/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/fake/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        opts = RenderOptions(fps=30.0, width=1920, height=1080)
        bad_directive = {**_D3_DICT, "target_w": 1081}  # odd: invalid

        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                probe,
                opts,
                reframe=bad_directive,
                source_probes=source_probes,
            )

        # Must be INVALID_INPUT (validation before multi-source check)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT, (
            f"Expected INVALID_INPUT for bad directive with multi-source;"
            f" got {exc_info.value.code}"
        )

    def test_single_source_with_reframe_does_not_raise(self) -> None:
        """Single source + valid reframe -> no error (AC-12 negative case).

        Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
        """
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        # Should not raise any exception
        plan = build_plan(ranges, probe, opts, reframe=_D3_DICT)
        assert plan is not None


# ---------------------------------------------------------------------------
# AC-10: backward compatibility — reframe=None preserves existing output (NFR-4)
# ---------------------------------------------------------------------------


class TestBuildPlanBackwardCompatibility:
    """reframe=None (or absent) -> filter_complex and ffmpeg_args unchanged (AC-10).

    Compares the output of build_plan with no reframe against build_plan with
    reframe=None, then verifies that [outvrf] does NOT appear and [outv] remains
    the terminal video label.

    Note: The absence of 'reframe' keyword argument in the current build_plan is
    the Red-phase failure condition; once wired (W3-Green), these tests confirm
    backward compatibility by asserting byte-equivalent output.
    Expected Red: TypeError — 'reframe' is not a valid keyword for build_plan.
    """

    def test_reframe_none_yields_same_filter_complex_as_no_reframe_arg(self) -> None:
        """build_plan(reframe=None) == build_plan() for filter_complex (AC-10)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan_no_reframe = build_plan(ranges, probe, opts)
        plan_reframe_none = build_plan(ranges, probe, opts, reframe=None)

        assert plan_no_reframe.filter_complex == plan_reframe_none.filter_complex, (
            "filter_complex must be identical when reframe=None vs. reframe absent"
        )

    def test_reframe_none_yields_same_ffmpeg_args_as_no_reframe_arg(self) -> None:
        """build_plan(reframe=None) == build_plan() for ffmpeg_args (AC-10)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan_no_reframe = build_plan(ranges, probe, opts)
        plan_reframe_none = build_plan(ranges, probe, opts, reframe=None)

        assert plan_no_reframe.ffmpeg_args == plan_reframe_none.ffmpeg_args, (
            "ffmpeg_args must be identical when reframe=None vs. reframe absent"
        )

    def test_no_reframe_directive_no_outvrf_label(self) -> None:
        """Without reframe, [outvrf] must NOT appear in filter_complex (AC-10)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions()

        plan = build_plan(ranges, probe, opts)

        assert "[outvrf]" not in plan.filter_complex, (
            "[outvrf] must not appear in filter_complex when no reframe directive"
        )

    def test_reframe_none_no_scale_suppression_warning(self) -> None:
        """reframe=None + width/height -> normal scale stage, no ignored-warning (AC-10)."""
        from clipwright_render.plan import build_plan

        ranges = _make_ranges()
        probe = _make_probe()
        opts = RenderOptions(width=1280, height=720)

        plan = build_plan(ranges, probe, opts, reframe=None)

        # Regular scale stage must be present
        assert "[outvscaled]" in plan.filter_complex, (
            "fit-scale [outvscaled] must appear when width/height given without reframe"
        )
        # No scale-suppression warning
        scale_suppress_warnings = [w for w in plan.warnings if "ignored" in w.lower()]
        assert len(scale_suppress_warnings) == 0, (
            f"No ignored-warning expected when reframe=None; got: {plan.warnings}"
        )


# ---------------------------------------------------------------------------
# render.py passthrough: clipwright_meta["reframe"] -> build_plan(reframe=...)
# (architecture §7.2 / render.py L818/L884)
# ---------------------------------------------------------------------------


class TestRenderPyReframePassthrough:
    """render.py reads reframe from OTIO metadata and passes it to build_plan.

    When an OTIO file has metadata["clipwright"]["reframe"] = D3 directive,
    the resulting build_plan call must include reframe=... so that the reframe
    stage appears in the returned filter_complex.

    Expected Red: AssertionError — render.py does not yet read raw_reframe or
    forward it to build_plan, so [outvrf] is absent from filter_complex.
    """

    def test_render_dry_run_with_reframe_otio_contains_outvrf(
        self, tmp_path: Path
    ) -> None:
        """dry_run on a reframe-annotated OTIO -> filter_complex contains [outvrf].

        Expected Red: [outvrf] absent (render.py wiring not yet done).
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "src.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl_reframe.otio"
        _write_timeline_with_reframe(tl_path, source, _D3_DICT)
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"Expected ok=True; got {result}"
        fc: str = result["data"]["filter_complex"]
        assert "[outvrf]" in fc, (
            f"Expected [outvrf] in filter_complex for reframe OTIO; got fc={fc!r}"
        )

    def test_render_dry_run_with_reframe_otio_contains_pad_filter(
        self, tmp_path: Path
    ) -> None:
        """dry_run on reframe(pad) OTIO -> filter_complex contains 'pad=' stage.

        Expected Red: pad not present (render.py wiring not yet done).
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "src.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl_pad.otio"
        _write_timeline_with_reframe(tl_path, source, _D3_DICT)  # mode=pad
        output = str(tmp_path / "out_pad.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        fc = result["data"]["filter_complex"]
        # pad= from reframe (not from scale/contain) must be present
        assert "pad=" in fc or "pad=" in fc, (
            f"Expected pad= in filter_complex for reframe(pad) OTIO; got fc={fc!r}"
        )

    def test_render_dry_run_without_reframe_otio_no_outvrf(
        self, tmp_path: Path
    ) -> None:
        """dry_run on plain OTIO (no reframe) -> filter_complex has no [outvrf].

        This confirms backward compatibility at the render.py level (AC-10).
        Expected: PASS even in Red phase (plain OTIO is unaffected).
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "src.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl_plain.otio"
        _write_plain_timeline(tl_path, source)
        output = str(tmp_path / "out_plain.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        fc = result["data"]["filter_complex"]
        assert "[outvrf]" not in fc, (
            f"[outvrf] must not appear in filter_complex for plain OTIO; got fc={fc!r}"
        )

    def test_render_dry_run_crop_otio_contains_crop_warning(
        self, tmp_path: Path
    ) -> None:
        """dry_run on reframe(crop) OTIO -> warnings contain crop-discard info (AC-13).

        Expected Red: no crop warning (render.py wiring not yet done).
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "src.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl_crop.otio"
        _write_timeline_with_reframe(tl_path, source, _D3_CROP)
        output = str(tmp_path / "out_crop.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        warnings: list[str] = result["warnings"]
        crop_warnings = [w for w in warnings if "crop" in w.lower()]
        assert len(crop_warnings) >= 1, (
            f"Expected crop-discard warning from render(crop); got warnings={warnings}"
        )
