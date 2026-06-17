"""test_text_overlay.py — Red tests for text_overlay → drawtext extension (WP-2).

Target functions (all new — not yet implemented):
  - _escape_drawtext_text(s: str) -> str
  - TextOverlay (frozen dataclass)
  - _build_alpha_expr(o: TextOverlay) -> str
  - _build_drawtext_segment(o: TextOverlay) -> str
  - _append_drawtext_filter(filter_parts, video_map_label, overlays) -> str
  - _marker_to_text_overlay(marker, resolved_font_path) -> TextOverlay
  - build_plan(..., text_overlays=...) — extended signature

Existing APIs under test (already implemented):
  - get_markers(timeline, kind="text_overlay")  — from clipwright.otio_utils

Test isolation:
  - Does NOT modify test_plan.py or any other existing test file.
  - Imports only from clipwright_render.plan and clipwright.otio_utils.
  - Font resolution uses unittest.mock so CI does not require real fonts.

Architecture references: architecture-report-20260617-230606.md §4.1–4.6/§9.2
Requirements references: requirements-report-20260617-230230.md AC-2-1 – AC-2-9
OQ-4: multiple overlays are comma-joined into a single [outvtext] label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import (
    KeptRange,
    ProbeInfo,
    build_plan,
    resolve_kept_ranges,
)
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_plan.py helpers — no import to keep isolation)
# ---------------------------------------------------------------------------

FPS = 30.0


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


def _add_text_overlay_marker(
    timeline: otio.schema.Timeline,
    text: str = "Hello",
    start_sec: float = 1.0,
    duration_sec: float = 3.0,
    x: str = "(w-tw)/2",
    y: str = "h-th-40",
    font_size: int = 48,
    font_color: str = "white",
    box: bool = False,
    box_color: str = "black@0.5",
    fade_in_sec: float = 0.3,
    fade_out_sec: float = 0.3,
    font_path: str | None = "/fake/font.ttf",
) -> None:
    """Attach a text_overlay marker directly to the first video track."""
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
        name="text_0",
        marked_range=marked_range,
        metadata={
            "clipwright": {
                "kind": "text_overlay",
                "tool": "clipwright-text",
                "version": "0.1.0",
                "text": text,
                "start_sec": start_sec,
                "duration_sec": duration_sec,
                "x": x,
                "y": y,
                "font_size": font_size,
                "font_color": font_color,
                "box": box,
                "box_color": box_color,
                "fade_in_sec": fade_in_sec,
                "fade_out_sec": fade_out_sec,
                "font_path": font_path,
            }
        },
    )
    video_track.markers.append(marker)


# ---------------------------------------------------------------------------
# AC-2-1: get_markers(kind="text_overlay") collects only text_overlay markers
# ---------------------------------------------------------------------------


class TestGetMarkersTextOverlay:
    """Verify get_markers(kind='text_overlay') filters correctly (AC-2-1)."""

    def test_text_overlay_marker_collected(self) -> None:
        """text_overlay marker on V1 track is returned by get_markers."""
        from clipwright.otio_utils import get_markers

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl)

        markers = get_markers(tl, kind="text_overlay")
        assert len(markers) == 1
        assert markers[0].metadata["clipwright"]["kind"] == "text_overlay"

    def test_other_kind_excluded(self) -> None:
        """Markers with kind != 'text_overlay' are excluded (AC-2-1)."""
        from clipwright.otio_utils import get_markers

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])

        # Add a marker of a different kind
        video_track = next(
            t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video
        )
        other_marker = otio.schema.Marker(
            name="scene_0",
            marked_range=_tr(2.0, 1.0),
            metadata={"clipwright": {"kind": "scene_boundary"}},
        )
        video_track.markers.append(other_marker)
        _add_text_overlay_marker(tl)

        markers = get_markers(tl, kind="text_overlay")
        assert len(markers) == 1
        assert markers[0].metadata["clipwright"]["kind"] == "text_overlay"

    def test_no_text_overlay_returns_empty(self) -> None:
        """Timeline with no text_overlay markers returns empty list."""
        from clipwright.otio_utils import get_markers

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        markers = get_markers(tl, kind="text_overlay")
        assert markers == []

    def test_multiple_text_overlays_all_collected(self) -> None:
        """Multiple text_overlay markers are all collected."""
        from clipwright.otio_utils import get_markers

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, text="First", start_sec=1.0)
        _add_text_overlay_marker(tl, text="Second", start_sec=5.0)

        markers = get_markers(tl, kind="text_overlay")
        assert len(markers) == 2


# ---------------------------------------------------------------------------
# AC-2-2: _escape_drawtext_text escapes backslash then single-quote
# ---------------------------------------------------------------------------


class TestEscapeDrawtextText:
    """Verify _escape_drawtext_text escape order (AC-2-2, architecture §4.4)."""

    def test_backslash_escaped(self) -> None:
        """Backslash is replaced with double-backslash."""
        from clipwright_render.plan import _escape_drawtext_text  # type: ignore[attr-defined]

        result = _escape_drawtext_text("a\\b")
        assert result == "a\\\\b"

    def test_single_quote_escaped(self) -> None:
        """Single quote is replaced with backslash-quote."""
        from clipwright_render.plan import _escape_drawtext_text  # type: ignore[attr-defined]

        result = _escape_drawtext_text("it's")
        assert result == "it\\'s"

    def test_backslash_then_quote_order(self) -> None:
        """Input 'a\\b'c': backslash escaped first, then quote (order matters)."""
        from clipwright_render.plan import _escape_drawtext_text  # type: ignore[attr-defined]

        # 'a\\b'c' contains one backslash and one single-quote
        result = _escape_drawtext_text("a\\b'c")
        # backslash → \\, quote → \' (order: backslash first)
        assert result == "a\\\\b\\'c"

    def test_no_special_chars_unchanged(self) -> None:
        """String without backslash or single-quote is returned unchanged."""
        from clipwright_render.plan import _escape_drawtext_text  # type: ignore[attr-defined]

        result = _escape_drawtext_text("Hello World")
        assert result == "Hello World"

    def test_empty_string(self) -> None:
        """Empty string returns empty string."""
        from clipwright_render.plan import _escape_drawtext_text  # type: ignore[attr-defined]

        result = _escape_drawtext_text("")
        assert result == ""


# ---------------------------------------------------------------------------
# AC-2-8/AC-2-9: Font resolution helpers
# ---------------------------------------------------------------------------


def _make_text_overlay_dataclass(**kwargs: Any) -> Any:
    """Construct a TextOverlay using the plan module's dataclass."""
    from clipwright_render.plan import TextOverlay  # type: ignore[attr-defined]

    defaults = dict(
        text="Hello",
        start_s=1.0,
        end_s=4.0,
        x="(w-tw)/2",
        y="h-th-40",
        font_size=48,
        font_color="white",
        box=False,
        box_color="black@0.5",
        fade_in_s=0.3,
        fade_out_s=0.3,
        font_path="/fake/font.ttf",
    )
    defaults.update(kwargs)
    return TextOverlay(**defaults)


# ---------------------------------------------------------------------------
# AC-2-3: Single-source _build_filter_complex has drawtext segment
# ---------------------------------------------------------------------------


class TestSingleSourceDrawtext:
    """filter_complex contains drawtext segment in single-source path (AC-2-3)."""

    def _build_single_with_overlay(
        self,
        font_path: str = "/fake/font.ttf",
        **overlay_kwargs: Any,
    ) -> str:
        """Helper: build filter_complex with one text_overlay via build_plan."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, font_path=font_path, **overlay_kwargs)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(width=1920, height=1080, fps=30.0, audio_count=0)

        # Mock font file to exist so CI passes without real fonts
        from clipwright_render.plan import TextOverlay  # type: ignore[attr-defined]

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())
        return plan.filter_complex

    def test_drawtext_keyword_present(self) -> None:
        """filter_complex contains 'drawtext' (AC-2-3)."""
        fc = self._build_single_with_overlay()
        assert "drawtext" in fc

    def test_drawtext_text_field(self) -> None:
        """drawtext segment contains text= field."""
        fc = self._build_single_with_overlay(text="Hello")
        assert "text=" in fc

    def test_drawtext_fontfile_field(self) -> None:
        """drawtext segment contains fontfile= field."""
        fc = self._build_single_with_overlay()
        assert "fontfile=" in fc

    def test_drawtext_fontsize_field(self) -> None:
        """drawtext segment contains fontsize= field."""
        fc = self._build_single_with_overlay(font_size=48)
        assert "fontsize=48" in fc

    def test_drawtext_fontcolor_field(self) -> None:
        """drawtext segment contains fontcolor= field."""
        fc = self._build_single_with_overlay(font_color="white")
        assert "fontcolor=white" in fc

    def test_drawtext_x_field(self) -> None:
        """drawtext segment contains x= field."""
        fc = self._build_single_with_overlay()
        assert "x=" in fc

    def test_drawtext_y_field(self) -> None:
        """drawtext segment contains y= field."""
        fc = self._build_single_with_overlay()
        assert "y=" in fc

    def test_drawtext_enable_between(self) -> None:
        """drawtext segment contains enable='between(t,...)'."""
        fc = self._build_single_with_overlay(start_sec=1.0, duration_sec=3.0)
        assert "enable=" in fc
        assert "between(t," in fc

    def test_drawtext_alpha_field(self) -> None:
        """drawtext segment contains alpha= field."""
        fc = self._build_single_with_overlay()
        assert "alpha=" in fc

    def test_drawtext_box_present_when_box_true(self) -> None:
        """box=True causes box=1 in drawtext segment."""
        fc = self._build_single_with_overlay(box=True, box_color="black@0.5")
        assert "box=1" in fc

    def test_drawtext_boxcolor_present_when_box_true(self) -> None:
        """box=True causes boxcolor= in drawtext segment."""
        fc = self._build_single_with_overlay(box=True, box_color="black@0.5")
        assert "boxcolor=" in fc

    def test_drawtext_box_absent_when_box_false(self) -> None:
        """box=False: box= and boxcolor= are omitted."""
        fc = self._build_single_with_overlay(box=False)
        assert "box=1" not in fc


# ---------------------------------------------------------------------------
# AC-2-4: Multi-source path also emits drawtext
# ---------------------------------------------------------------------------


class TestMultiSourceDrawtext:
    """filter_complex contains drawtext in multi-source path (AC-2-4)."""

    def test_drawtext_in_multi_source(self) -> None:
        """Multi-source filter_complex includes drawtext segment (AC-2-4)."""
        clips = [
            _make_clip("/src/a.mp4", 0.0, 5.0),
            _make_clip("/src/b.mp4", 0.0, 5.0),
        ]
        tl = _make_timeline(clips)
        _add_text_overlay_marker(tl, text="Multi", font_path="/fake/font.ttf")

        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(width=1920, height=1080, fps=30.0, audio_count=0)
        probe_b = _make_probe(width=1920, height=1080, fps=30.0, audio_count=0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(
                ranges, probe_a, RenderOptions(), source_probes=source_probes
            )
        assert "drawtext" in plan.filter_complex


# ---------------------------------------------------------------------------
# AC-2-5: subtitle → drawtext order; final label is [outvtext]
# ---------------------------------------------------------------------------


class TestSubtitleDrawtextOrder:
    """subtitle stage appears before drawtext; final label is [outvtext] (AC-2-5)."""

    def test_outvtext_label_present(self) -> None:
        """[outvtext] label appears in filter_complex when overlays present."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())
        assert "[outvtext]" in plan.filter_complex

    def test_subtitle_before_drawtext_in_filter_complex(self) -> None:
        """When subtitle is present, its position precedes drawtext in filter_complex."""
        from clipwright_render.schemas import SubtitleOptions

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0, height=1080)

        subtitle = SubtitleOptions(path="/fake/sub.srt")
        # Mock subtitle file existence and font resolution
        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            plan = build_plan(ranges, probe, RenderOptions(subtitle=subtitle))

        fc = plan.filter_complex
        sub_pos = fc.find("subtitles=")
        dt_pos = fc.find("drawtext")
        assert sub_pos != -1, "subtitles= not found in filter_complex"
        assert dt_pos != -1, "drawtext not found in filter_complex"
        assert sub_pos < dt_pos, "subtitle must precede drawtext in filter_complex"

    def test_final_video_label_is_outvtext(self) -> None:
        """With text overlay, plan.ffmpeg_args maps [outvtext] (not [outv])."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())

        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvtext]" in args_str


# ---------------------------------------------------------------------------
# AC-2-6: Backward compatibility (byte-identical when overlays empty)
# ---------------------------------------------------------------------------


class TestBackwardCompatByteIdentical:
    """filter_complex is identical to pre-extension output when no overlays (AC-2-6)."""

    def test_no_overlay_no_subtitle_identical(self) -> None:
        """No text_overlay markers, no subtitle: filter_complex is unchanged."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        # Baseline (no overlays, no subtitle)
        plan_before = build_plan(ranges, probe, RenderOptions())

        # Call with text_overlays explicitly empty (or absent)
        plan_after = build_plan(ranges, probe, RenderOptions(), text_overlays=[])

        assert plan_before.filter_complex == plan_after.filter_complex

    def test_no_overlay_with_subtitle_identical(self) -> None:
        """No text_overlay markers, subtitle present: filter_complex is unchanged."""
        from clipwright_render.schemas import SubtitleOptions

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0, height=1080)
        subtitle = SubtitleOptions(path="/fake/sub.srt")

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            plan_before = build_plan(ranges, probe, RenderOptions(subtitle=subtitle))
            plan_after = build_plan(
                ranges,
                probe,
                RenderOptions(subtitle=subtitle),
                text_overlays=[],
            )

        assert plan_before.filter_complex == plan_after.filter_complex


# ---------------------------------------------------------------------------
# AC-2-7: render-side re-validation raises INVALID_INPUT for bad OTIO markers
# ---------------------------------------------------------------------------


class TestRenderSideRevalidation:
    """Markers with invalid values are rejected as INVALID_INPUT (AC-2-7)."""

    def _build_with_bad_marker(self, **bad_fields: Any) -> None:
        """Helper: attach a marker with one bad field and call build_plan."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, **bad_fields)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            build_plan(ranges, probe, RenderOptions())

    def test_negative_start_sec_raises_invalid_input(self) -> None:
        """start_sec < 0 in marker → INVALID_INPUT on render side."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_bad_marker(start_sec=-1.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_zero_duration_raises_invalid_input(self) -> None:
        """duration_sec <= 0 in marker → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_bad_marker(duration_sec=0.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_font_color_raises_invalid_input(self) -> None:
        """font_color outside allowlist → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_bad_marker(font_color="white with spaces")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_box_color_raises_invalid_input(self) -> None:
        """box_color with disallowed characters → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_bad_marker(box_color="black;rm -rf /")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_newline_in_text_raises_invalid_input(self) -> None:
        """Control character (newline) in text → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_bad_marker(text="Hello\nWorld")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_control_char_in_x_raises_invalid_input(self) -> None:
        """Control character in x expression → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_bad_marker(x="(w-tw)/2\x00")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# Fade alpha expression: zero-division guard and normal form
# ---------------------------------------------------------------------------


class TestBuildAlphaExpr:
    """_build_alpha_expr generates correct alpha expressions (FR-6-4, §4.4)."""

    def test_both_fades_zero_returns_constant_one(self) -> None:
        """fi=0, fo=0 → alpha='1' (no fade branches, zero-division free)."""
        from clipwright_render.plan import _build_alpha_expr  # type: ignore[attr-defined]

        o = _make_text_overlay_dataclass(fade_in_s=0.0, fade_out_s=0.0)
        expr = _build_alpha_expr(o)
        # Must not contain /0 or /0.0
        assert "/0" not in expr
        # Simple constant 1
        assert expr == "1"

    def test_fade_in_zero_no_division_by_zero(self) -> None:
        """fi=0 → no (t-s)/0 term in expression."""
        from clipwright_render.plan import _build_alpha_expr  # type: ignore[attr-defined]

        o = _make_text_overlay_dataclass(
            start_s=1.0, end_s=4.0, fade_in_s=0.0, fade_out_s=0.3
        )
        expr = _build_alpha_expr(o)
        assert "/0" not in expr
        # Fade-out branch should still be present
        assert "fo" in expr or str(0.3) in expr or "0.3" in expr

    def test_fade_out_zero_no_division_by_zero(self) -> None:
        """fo=0 → no (e-t)/0 term in expression."""
        from clipwright_render.plan import _build_alpha_expr  # type: ignore[attr-defined]

        o = _make_text_overlay_dataclass(
            start_s=1.0, end_s=4.0, fade_in_s=0.3, fade_out_s=0.0
        )
        expr = _build_alpha_expr(o)
        assert "/0" not in expr
        # Fade-in branch should still be present
        assert "0.3" in expr or str(0.3) in expr

    def test_normal_fades_contain_t_minus_s(self) -> None:
        """Both fades non-zero: expression contains (t-s)/fi and (e-t)/fo terms."""
        from clipwright_render.plan import _build_alpha_expr  # type: ignore[attr-defined]

        o = _make_text_overlay_dataclass(
            start_s=2.0, end_s=5.0, fade_in_s=0.5, fade_out_s=0.5
        )
        expr = _build_alpha_expr(o)
        assert "/0" not in expr
        # Expression should contain the fade-in time reference
        assert "2.0" in expr or "2" in expr
        # Expression should be non-trivial
        assert "if(" in expr or "lt(" in expr


# ---------------------------------------------------------------------------
# Font resolution (AC-2-8 / AC-2-9)
# ---------------------------------------------------------------------------


class TestFontResolution:
    """Font resolution: specified / default platform / missing (AC-2-8/AC-2-9)."""

    def test_specified_font_path_used(self) -> None:
        """Explicit font_path in overlay is passed to drawtext fontfile=."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, font_path="/my/custom/font.ttf")
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())

        # The custom font path (or its escaped form) should appear in filter_complex
        assert "font.ttf" in plan.filter_complex or "my" in plan.filter_complex

    def test_missing_specified_font_raises_invalid_input(self) -> None:
        """Explicit font_path that does not exist → INVALID_INPUT."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, font_path="/nonexistent/font.ttf")
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        # Path.is_file returns False → font not found
        with patch("pathlib.Path.is_file", return_value=False):
            with pytest.raises(ClipwrightError) as exc_info:
                build_plan(ranges, probe, RenderOptions())
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_no_font_path_platform_default_used(self) -> None:
        """font_path=None: platform default is explored; mock one as existing."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        # font_path=None → render side will search platform defaults
        _add_text_overlay_marker(tl, font_path=None)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        def _first_default_exists(self_path: Any) -> bool:
            """Return True only for the first platform default path checked."""
            return True  # any is_file check → found

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())
        # If a platform default was found, drawtext should appear
        assert "drawtext" in plan.filter_complex

    def test_all_fonts_missing_raises_invalid_input(self) -> None:
        """font_path=None and all platform defaults missing → INVALID_INPUT."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, font_path=None)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=False):
            with pytest.raises(ClipwrightError) as exc_info:
                build_plan(ranges, probe, RenderOptions())
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_no_overlay_no_font_resolution(self) -> None:
        """No overlays → no font resolution → no INVALID_INPUT (backward compat)."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        # Path.is_file returns False; but no overlay → should not raise
        with patch("pathlib.Path.is_file", return_value=False):
            plan = build_plan(ranges, probe, RenderOptions())
        assert "drawtext" not in plan.filter_complex


# ---------------------------------------------------------------------------
# OQ-4: multiple overlays are comma-joined into a single [outvtext] label
# ---------------------------------------------------------------------------


class TestMultipleOverlaysCommaJoined:
    """Multiple overlays are comma-joined in one filter chain → single [outvtext] (OQ-4)."""

    def test_two_overlays_single_outvtext(self) -> None:
        """Two text_overlay markers produce exactly one [outvtext] label."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, text="First", start_sec=1.0, font_path="/f.ttf")
        _add_text_overlay_marker(tl, text="Second", start_sec=5.0, font_path="/f.ttf")
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())

        fc = plan.filter_complex
        # Exactly one [outvtext] label
        assert fc.count("[outvtext]") == 1

    def test_two_overlays_both_drawtext_in_filter_complex(self) -> None:
        """Two text_overlay markers → drawtext appears at least twice."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, text="First", start_sec=1.0, font_path="/f.ttf")
        _add_text_overlay_marker(tl, text="Second", start_sec=5.0, font_path="/f.ttf")
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())

        # Both drawtext segments should appear (comma-joined chain)
        assert plan.filter_complex.count("drawtext") >= 2

    def test_two_overlays_no_intermediate_label(self) -> None:
        """Comma-joined chain: no intermediate [outvtext0]/[outvtext1] labels."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, text="First", start_sec=1.0, font_path="/f.ttf")
        _add_text_overlay_marker(tl, text="Second", start_sec=5.0, font_path="/f.ttf")
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions())

        fc = plan.filter_complex
        # Intermediate labels like [outvtext0] should not appear
        assert "[outvtext0]" not in fc
        assert "[outvtext1]" not in fc


# ---------------------------------------------------------------------------
# Review fix Red tests — render-side validations not yet implemented
# References: code-review-report-20260617-234432.md (M-1, M-2, H-1, L-3)
#             security-review-report-review-security.md (S-M-1, S-M-2, S-M-3,
#                                                        S-L-2, S-L-3)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S-M-2 / M-1: render-side fade_sum > duration validation
# ---------------------------------------------------------------------------


class TestFadeSumValidation:
    """_marker_to_text_overlay raises INVALID_INPUT when fade_in+fade_out > duration.

    References: S-M-2 (security-review), M-1 (code-review), ADR-T4.
    These tests are RED: the render-side fade_sum check is not yet implemented.
    """

    def _build_with_fades(
        self,
        fade_in_sec: float,
        fade_out_sec: float,
        duration_sec: float,
    ) -> None:
        """Helper: build_plan with a marker whose fade sums may exceed duration."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(
            tl,
            fade_in_sec=fade_in_sec,
            fade_out_sec=fade_out_sec,
            duration_sec=duration_sec,
            font_path="/fake/font.ttf",
        )
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)
        with patch("pathlib.Path.is_file", return_value=True):
            build_plan(ranges, probe, RenderOptions())

    def test_fade_sum_exceeds_duration_raises_invalid_input(self) -> None:
        """fade_in+fade_out > duration → INVALID_INPUT (multi-layer defence ADR-T4)."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fades(fade_in_sec=5.0, fade_out_sec=5.0, duration_sec=3.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_fade_in_alone_exceeds_duration_raises_invalid_input(self) -> None:
        """fade_in alone > duration → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fades(fade_in_sec=4.0, fade_out_sec=0.0, duration_sec=3.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_fade_out_alone_exceeds_duration_raises_invalid_input(self) -> None:
        """fade_out alone > duration → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fades(fade_in_sec=0.0, fade_out_sec=4.0, duration_sec=3.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_fade_sum_exactly_equal_to_duration_passes(self) -> None:
        """fade_in+fade_out == duration is valid (boundary: sum <= duration)."""
        # Must NOT raise: exactly equal is acceptable
        self._build_with_fades(fade_in_sec=1.5, fade_out_sec=1.5, duration_sec=3.0)

    def test_fade_sum_within_1e9_tolerance_passes(self) -> None:
        """fade_in+fade_out barely > duration by float noise (< 1e-9) passes."""
        # sum = duration + 5e-10 → within 1e-9 tolerance → should pass
        duration = 3.0
        self._build_with_fades(
            fade_in_sec=1.5, fade_out_sec=1.5 + 5e-10, duration_sec=duration
        )

    def test_fade_sum_just_over_1e9_tolerance_raises(self) -> None:
        """fade_in+fade_out > duration + 1e-9 → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            # sum = duration + 2e-9, clearly over tolerance
            self._build_with_fades(
                fade_in_sec=1.5, fade_out_sec=1.5 + 2e-9, duration_sec=3.0
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# S-M-3: render-side empty text validation
# ---------------------------------------------------------------------------


class TestEmptyTextValidation:
    """_marker_to_text_overlay raises INVALID_INPUT for empty text.

    Reference: S-M-3 (security-review).
    These tests are RED: the empty-text check is not yet implemented on render side.
    """

    def _build_with_text(self, text: str) -> None:
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, text=text, font_path="/fake/font.ttf")
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)
        with patch("pathlib.Path.is_file", return_value=True):
            build_plan(ranges, probe, RenderOptions())

    def test_empty_text_raises_invalid_input(self) -> None:
        """text='' in marker → INVALID_INPUT (drawtext=text='' must not be emitted)."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_text("")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nonempty_text_passes(self) -> None:
        """Non-empty text passes without raising."""
        # Must NOT raise
        self._build_with_text("Hello")

    def test_whitespace_only_text_raises_invalid_input(self) -> None:
        """text='   ' (whitespace only) → INVALID_INPUT.

        Whitespace-only strings produce an effectively invisible overlay.
        The render side should reject them alongside empty strings.
        Note: if the impl only checks `not text` (falsy), whitespace passes.
        This test pins the stricter behaviour: strip().
        """
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_text("   ")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# S-M-1: error message must NOT expose internal field names or OTIO paths
# ---------------------------------------------------------------------------


class TestErrorMessageDoesNotExposeInternals:
    """ClipwrightError.message must use fixed wording, not field-name expansion.

    Reference: S-M-1 (security-review, CWE-209).
    These tests are RED: the current render-side messages still contain
    field_name (e.g. 'font_color', 'text', 'x', 'y', 'box_color').
    """

    # Internal field names that must NOT appear in .message (only allowed in .hint)
    _INTERNAL_FIELD_NAMES = ("'text'", "'x'", "'y'", "'font_color'", "'box_color'")

    def _get_error_for(self, **bad_fields: Any) -> ClipwrightError:
        """Trigger _marker_to_text_overlay validation and capture the error."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, font_path="/fake/font.ttf", **bad_fields)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)
        with pytest.raises(ClipwrightError) as exc_info:
            with patch("pathlib.Path.is_file", return_value=True):
                build_plan(ranges, probe, RenderOptions())
        return exc_info.value

    def test_control_char_in_text_message_has_no_field_name(self) -> None:
        """Control char in 'text': message must not contain 'text' as quoted name."""
        err = self._get_error_for(text="hello\x01world")
        assert err.code == ErrorCode.INVALID_INPUT
        for fname in self._INTERNAL_FIELD_NAMES:
            assert fname not in err.message, (
                f"message must not contain internal field name {fname!r}: {err.message!r}"
            )

    def test_control_char_in_x_message_has_no_field_name(self) -> None:
        """Control char in 'x': message must not contain 'x' as quoted name."""
        err = self._get_error_for(x="(w-tw)/2\x00")
        assert err.code == ErrorCode.INVALID_INPUT
        for fname in self._INTERNAL_FIELD_NAMES:
            assert fname not in err.message, (
                f"message must not contain internal field name {fname!r}: {err.message!r}"
            )

    def test_invalid_font_color_message_has_no_field_name(self) -> None:
        """Invalid font_color: message must not contain 'font_color' as quoted name."""
        err = self._get_error_for(font_color="white with spaces")
        assert err.code == ErrorCode.INVALID_INPUT
        for fname in self._INTERNAL_FIELD_NAMES:
            assert fname not in err.message, (
                f"message must not contain internal field name {fname!r}: {err.message!r}"
            )

    def test_invalid_box_color_message_has_no_field_name(self) -> None:
        """Invalid box_color: message must not contain 'box_color' as quoted name."""
        err = self._get_error_for(box_color="black;rm -rf /")
        assert err.code == ErrorCode.INVALID_INPUT
        for fname in self._INTERNAL_FIELD_NAMES:
            assert fname not in err.message, (
                f"message must not contain internal field name {fname!r}: {err.message!r}"
            )


# ---------------------------------------------------------------------------
# M-2 / S-L-2: render-side font_path validation (single-quote, control chars,
#               max_length)
# ---------------------------------------------------------------------------


class TestFontPathValidation:
    """_marker_to_text_overlay rejects font_path with dangerous characters.

    References: M-2 (code-review, filtergraph injection via single-quote),
                S-L-2 (security-review, no max_length/pattern constraint).
    These tests are RED: the render-side font_path character/length check
    is not yet implemented.

    Path.is_file is mocked so tests do not require real font files; the
    character/length check must fire BEFORE the is_file check.
    """

    def _build_with_font_path(self, font_path: str | None) -> None:
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(tl, font_path=font_path)
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)
        with patch("pathlib.Path.is_file", return_value=True):
            build_plan(ranges, probe, RenderOptions())

    def test_single_quote_in_font_path_raises_invalid_input(self) -> None:
        """font_path with single-quote → INVALID_INPUT (filtergraph injection risk)."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_font_path("/fake/fon't.ttf")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_newline_in_font_path_raises_invalid_input(self) -> None:
        """font_path with newline (\\n) → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_font_path("/fake/font\n.ttf")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_null_byte_in_font_path_raises_invalid_input(self) -> None:
        """font_path with null byte (\\x00) → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_font_path("/fake/font\x00.ttf")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_font_path_exceeding_max_length_raises_invalid_input(self) -> None:
        """font_path longer than 4096 chars → INVALID_INPUT."""
        long_path = "/fake/" + "a" * 4092 + ".ttf"  # total > 4096
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_font_path(long_path)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_font_path_at_max_length_passes(self) -> None:
        """font_path of exactly 4096 chars passes length check."""
        # 4096 chars total: "/fake/" (6) + "a"*(4084) + ".ttf" (4) = 4094 ≤ 4096
        path_4096 = "/fake/" + "a" * 4086 + ".ttf"  # exactly 4096
        assert len(path_4096) == 4096
        # Must NOT raise for length; is_file mocked True
        self._build_with_font_path(path_4096)

    def test_normal_font_path_passes(self) -> None:
        """A normal font path without special chars passes validation."""
        # Must NOT raise
        self._build_with_font_path("/fake/normal_font.ttf")


# ---------------------------------------------------------------------------
# S-L-3: assert → if-raise in _build_drawtext_segment
# ---------------------------------------------------------------------------


class TestBuildDrawtextSegmentAssertReplaced:
    """_build_drawtext_segment raises ClipwrightError(INTERNAL) for font_path=None.

    Reference: S-L-3 (security-review).
    Current code uses `assert` which is a no-op under python -O.
    This test is RED: the impl still uses assert (not if-raise).
    """

    def test_font_path_none_raises_clipwright_internal_not_assertion_error(
        self,
    ) -> None:
        """font_path=None passed directly → ClipwrightError(INTERNAL), not AssertionError."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_drawtext_segment,
        )

        overlay = _make_text_overlay_dataclass(font_path=None)
        # Under normal execution, assert fires → AssertionError.
        # After fix, if-raise fires → ClipwrightError(INTERNAL).
        with pytest.raises(ClipwrightError) as exc_info:
            _build_drawtext_segment(overlay)
        assert exc_info.value.code == ErrorCode.INTERNAL

    def test_font_path_none_does_not_raise_assertion_error(self) -> None:
        """Calling _build_drawtext_segment(font_path=None) must NOT raise AssertionError."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_drawtext_segment,
        )

        overlay = _make_text_overlay_dataclass(font_path=None)
        # After fix: should raise ClipwrightError, not AssertionError
        with pytest.raises(Exception) as exc_info:
            _build_drawtext_segment(overlay)
        # The exception type must not be AssertionError
        assert not isinstance(exc_info.value, AssertionError), (
            "_build_drawtext_segment should use if-raise, not assert"
        )


# ---------------------------------------------------------------------------
# H-1: _PLATFORM_FONT_CANDIDATES type sanity
# ---------------------------------------------------------------------------


class TestPlatformFontCandidatesType:
    """_PLATFORM_FONT_CANDIDATES is list[str] — iterable with str elements.

    Reference: H-1 (code-review).
    This is a type sanity check.  The full mypy verification is the mypy gate
    in the CI/commit flow; this test verifies runtime behaviour.
    These tests should already pass (the runtime value IS a list[str] due to
    dict.get() with a non-None default).  They serve as a regression guard
    so that future refactors that accidentally introduce None are caught.
    """

    def test_platform_font_candidates_is_not_none(self) -> None:
        """_PLATFORM_FONT_CANDIDATES must not be None."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _PLATFORM_FONT_CANDIDATES,
        )

        assert _PLATFORM_FONT_CANDIDATES is not None

    def test_platform_font_candidates_is_iterable(self) -> None:
        """_PLATFORM_FONT_CANDIDATES must be iterable (list[str])."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _PLATFORM_FONT_CANDIDATES,
        )

        # Will raise TypeError if None
        items = list(_PLATFORM_FONT_CANDIDATES)
        assert isinstance(items, list)

    def test_platform_font_candidates_elements_are_str(self) -> None:
        """Each element of _PLATFORM_FONT_CANDIDATES is a str."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _PLATFORM_FONT_CANDIDATES,
        )

        for item in _PLATFORM_FONT_CANDIDATES:
            assert isinstance(item, str), (
                f"Expected str element, got {type(item).__name__!r}: {item!r}"
            )

    def test_platform_font_candidates_nonempty(self) -> None:
        """_PLATFORM_FONT_CANDIDATES must have at least one candidate."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _PLATFORM_FONT_CANDIDATES,
        )

        assert len(_PLATFORM_FONT_CANDIDATES) >= 1


# ---------------------------------------------------------------------------
# L-3: _get expected_type simplification note
# NOTE: No new Red test for L-3.  The simplification (removing unused
# `expected_type` arg from the nested `_get` helper) is an internal refactor.
# The existing normal-path tests in TestRenderSideRevalidation and
# TestSingleSourceDrawtext exercise _marker_to_text_overlay and will remain
# Green after the simplification.  This comment records that L-3 is
# intentionally covered by the existing Green tests rather than a new Red test.
# ---------------------------------------------------------------------------
