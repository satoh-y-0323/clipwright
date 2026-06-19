"""test_render_retiming.py — Red tests for plan.py / render.py integration layer.

Target functions (not yet implemented):
  - clipwright_render.plan.retime_text_overlays
  - clipwright_render.render._generate_retimed_srt

Architecture references: architecture-report-20260620-030930.md §7-D
Requirements satisfied:
  - AC-5: identity timeline (no cut/warp) -> cue/overlay unchanged, no warnings
  - AC-6: retime_markers="off" -> re-timing skipped
  - AC-7: subtitle source .srt unmodified (non-destructive)
  - AC-9: multi-source -> skip + warning "retime_markers skipped: multi-source..."
  - FR-6: warning text per dropped/split/shifted/clipped overlay/cue (B3)
  - Decision A: all cues dropped -> empty srt NOT written, subtitle filter skipped,
                warning=1 emitted
  - Decision B (corrected): retimed .srt name = {output_stem}.retimed.srt
    (output file stem, NOT subtitle source stem)

Test groups:
  T1: retime_text_overlays — plan.py adapter (build_plan via marker injection)
      T1-A: marker 1 -> N drawtext segments (split)
      T1-B: marker drop -> 0 drawtext segments
      T1-C: identity (no cut/warp) -> overlay unchanged, no warnings
  T2: _generate_retimed_srt — render.py helper (direct import / attribute check)
      T2-A: ImportError / AttributeError for missing function
  T3: render_timeline subtitle integration (mocked build_plan / inspect_media)
      T3-A: retime_markers="auto" + .srt + cut -> retimed path in options.subtitle
      T3-B: retime_markers="off" -> re-timing skipped (AC-6)
      T3-C: multi-source -> skip + warning (AC-9)
      T3-D: .vtt subtitle -> skip + warning
      T3-E: .ass subtitle -> skip + warning
      T3-F: all-cue-drop -> subtitle filter skipped + 1 warning (Decision A)
      T3-G: retimed .srt name = {output_stem}.retimed.srt (Decision B)
      T3-H: overwrite=False + existing retimed .srt -> INVALID_INPUT hint
      T3-I: overwrite=True + existing retimed .srt -> succeeds (replaces)
  T4: identity regression
      T4-A: no cut / no warp -> overlay start_s/end_s unchanged, warnings=[]

Test isolation:
  - All tests that call render_timeline use dry_run=True or mock build_plan to
    avoid needing real ffmpeg binaries.
  - plan.py / render.py / schemas.py must NOT be modified by this test file.
  - retime_text_overlays / _generate_retimed_srt are expected NOT to exist yet;
    AttributeError / ImportError is the correct Red state.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import (
    KeptRange,
    ProbeInfo,
    TextOverlay,
    build_plan,
    resolve_kept_ranges,
)
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FPS = 30.0
_RATE = 30


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
    audio_count: int = 0,
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
    start_sec: float = 2.0,
    duration_sec: float = 4.0,
    font_path: str = "/fake/font.ttf",
) -> None:
    """Attach a text_overlay marker to the first video track."""
    video_track = next(
        t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video
    )
    marked_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start_sec * FPS, FPS),
        duration=otio.opentime.RationalTime(duration_sec * FPS, FPS),
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
                "x": "(w-tw)/2",
                "y": "h-th-40",
                "font_size": 48,
                "font_color": "white",
                "box": False,
                "box_color": "black@0.5",
                "fade_in_sec": 0.0,
                "fade_out_sec": 0.0,
                "font_path": font_path,
            }
        },
    )
    video_track.markers.append(marker)


def _make_text_overlay(
    text: str = "Hello",
    start_s: float = 2.0,
    end_s: float = 6.0,
    font_path: str = "/fake/font.ttf",
) -> TextOverlay:
    return TextOverlay(
        text=text,
        start_s=start_s,
        end_s=end_s,
        x="(w-tw)/2",
        y="h-th-40",
        font_size=48,
        font_color="white",
        box=False,
        box_color="black@0.5",
        fade_in_s=0.0,
        fade_out_s=0.0,
        font_path=font_path,
    )


def _make_kept(
    start_s: float, end_s: float, source: str = "clip.mp4", scalar: float = 1.0
) -> KeptRange:
    return KeptRange(
        source=source, source_range=_tr(start_s, end_s - start_s), time_scalar=scalar
    )


_SIMPLE_SRT = textwrap.dedent("""\
    1
    00:00:01,000 --> 00:00:03,000
    First cue

    2
    00:00:07,000 --> 00:00:09,000
    Second cue
    """)


# ---------------------------------------------------------------------------
# T1: retime_text_overlays — plan.py adapter
# ---------------------------------------------------------------------------


class TestRetimeTextOverlays:
    """T1: retime_text_overlays in plan.py (function not yet implemented → Red).

    The function signature is defined in architecture-report §2.1:
        retime_text_overlays(overlays, tmap, retime) -> tuple[list[TextOverlay], list[str]]

    Expected Red reason: AttributeError on import (function does not exist in plan.py).
    """

    def _import_retime_text_overlays(self) -> Any:
        """Import retime_text_overlays from plan; expected to raise AttributeError."""
        from clipwright_render import plan as _plan  # type: ignore[import]

        return getattr(_plan, "retime_text_overlays")  # AttributeError if not present

    def test_retime_text_overlays_exists_in_plan(self) -> None:
        """retime_text_overlays must be importable from clipwright_render.plan.

        Red state: AttributeError because the function does not exist yet.
        """
        # This will raise AttributeError until retime_text_overlays is implemented.
        fn = self._import_retime_text_overlays()
        assert callable(fn), "retime_text_overlays must be callable"

    def test_split_marker_produces_n_drawtext_segments(self) -> None:
        """T1-A: Marker spanning a cut boundary -> build_plan produces >=2 drawtext segments.

        Setup: source 0-10s, cut at 5s, second range 10-20s.
        Marker: source 3-12s -> crosses cut -> should split into 2 segments:
          seg1: source 3-5s -> program 3-5s
          seg2: source 10-12s -> program 5-7s
        After retime_text_overlays, build_plan should produce >=2 drawtext entries.

        Red reason: retime_text_overlays does not exist; build_plan will NOT call it
        and will produce only 1 drawtext segment (the original marker in source time),
        causing this test to fail with AssertionError.
        """
        # Build a timeline with two non-contiguous clips (cut at source 5s)
        tl = _make_timeline(
            [
                _make_clip("/src/clip.mp4", 0.0, 5.0),  # source 0-5s
                _make_clip("/src/clip.mp4", 10.0, 10.0),  # source 10-20s (cut 5-10s)
            ]
        )
        # Add marker that spans the cut: source 3-12s -> split expected
        _add_text_overlay_marker(tl, text="Split me", start_sec=3.0, duration_sec=9.0)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(
                ranges,
                probe,
                RenderOptions(retime_markers="auto"),
            )

        # With retime_text_overlays implemented: split -> >=2 drawtext segments
        # Without it (current state): only 1 drawtext segment in source time
        drawtext_count = plan.filter_complex.count("drawtext=")
        assert drawtext_count >= 2, (
            f"Expected >=2 drawtext segments after split re-timing, got {drawtext_count}. "
            "retime_text_overlays is not implemented yet."
        )

    def test_drop_marker_produces_zero_drawtext_segments(self) -> None:
        """T1-B: Marker fully in removed (cut) region -> 0 drawtext segments in filter_complex.

        Setup: source 0-5s kept, 5-10s cut, 10-20s kept.
        Marker: source 6-9s -> entirely in cut -> should be dropped (0 segments).

        Red reason: retime_text_overlays not implemented; build_plan currently
        produces 1 drawtext segment even for dropped markers.
        """
        tl = _make_timeline(
            [
                _make_clip("/src/clip.mp4", 0.0, 5.0),
                _make_clip("/src/clip.mp4", 10.0, 10.0),
            ]
        )
        # Marker fully in the cut zone (source 6-9s, which is removed)
        _add_text_overlay_marker(tl, text="Dropped", start_sec=6.0, duration_sec=3.0)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(
                ranges,
                probe,
                RenderOptions(retime_markers="auto"),
            )

        # With retime_text_overlays: drop -> 0 drawtext in filter_complex
        # Without it: 1 drawtext appears (source time, not remapped)
        assert "drawtext" not in plan.filter_complex, (
            "Expected 0 drawtext segments for fully-dropped overlay, but found drawtext. "
            "retime_text_overlays is not implemented yet."
        )

    def test_drop_marker_emits_warning(self) -> None:
        """T1-B: Dropped overlay emits exactly 1 warning (FR-6 / B3).

        Red reason: retime_text_overlays not implemented; no warnings generated.
        """
        tl = _make_timeline(
            [
                _make_clip("/src/clip.mp4", 0.0, 5.0),
                _make_clip("/src/clip.mp4", 10.0, 10.0),
            ]
        )
        _add_text_overlay_marker(tl, text="Dropped", start_sec=6.0, duration_sec=3.0)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(
                ranges,
                probe,
                RenderOptions(retime_markers="auto"),
            )

        # Warning must mention the drop (FR-6 text: "dropped (source range removed by cuts)")
        drop_warnings = [w for w in plan.warnings if "drop" in w.lower()]
        assert len(drop_warnings) == 1, (
            f"Expected 1 drop warning, got {len(drop_warnings)}: {plan.warnings}. "
            "retime_text_overlays is not implemented yet."
        )

    def test_split_marker_emits_warning(self) -> None:
        """T1-A: Split overlay emits exactly 1 warning (FR-6 / B3).

        Red reason: retime_text_overlays not implemented; no warnings generated.
        """
        tl = _make_timeline(
            [
                _make_clip("/src/clip.mp4", 0.0, 5.0),
                _make_clip("/src/clip.mp4", 10.0, 10.0),
            ]
        )
        _add_text_overlay_marker(tl, text="Split me", start_sec=3.0, duration_sec=9.0)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(
                ranges,
                probe,
                RenderOptions(retime_markers="auto"),
            )

        split_warnings = [w for w in plan.warnings if "split" in w.lower()]
        assert len(split_warnings) == 1, (
            f"Expected 1 split warning, got {len(split_warnings)}: {plan.warnings}. "
            "retime_text_overlays is not implemented yet."
        )

    def test_warnings_reach_render_plan(self) -> None:
        """T1: Warnings from retime_text_overlays must appear in RenderPlan.warnings.

        Verifies the build_plan.warnings -> RenderPlan.warnings path (ADR-4 / §0-5).
        Red reason: retime_text_overlays not implemented; plan.warnings is empty.
        """
        tl = _make_timeline(
            [
                _make_clip("/src/clip.mp4", 0.0, 5.0),
                _make_clip("/src/clip.mp4", 10.0, 10.0),
            ]
        )
        # Dropped marker -> at least 1 drop warning expected
        _add_text_overlay_marker(tl, text="Dropped", start_sec=6.0, duration_sec=3.0)

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(
                ranges,
                probe,
                RenderOptions(retime_markers="auto"),
            )

        assert len(plan.warnings) >= 1, (
            "Expected >=1 warning in RenderPlan.warnings for dropped overlay. "
            "retime_text_overlays is not implemented yet."
        )


# ---------------------------------------------------------------------------
# T2: _generate_retimed_srt — render.py helper
# ---------------------------------------------------------------------------


class TestGenerateRetimedSrt:
    """T2: _generate_retimed_srt in render.py (function not yet implemented -> Red).

    Expected Red reason: AttributeError because _generate_retimed_srt does not
    exist in render.py.
    """

    def test_generate_retimed_srt_exists_in_render(self) -> None:
        """_generate_retimed_srt must be importable from clipwright_render.render.

        Red state: AttributeError because the function does not exist yet.
        """
        from clipwright_render import render as _render  # type: ignore[import]

        fn = getattr(_render, "_generate_retimed_srt")  # AttributeError if missing
        assert callable(fn), "_generate_retimed_srt must be callable"


# ---------------------------------------------------------------------------
# T3: render_timeline subtitle integration (dry_run / mocked)
# ---------------------------------------------------------------------------


class TestRenderSubtitleRetiming:
    """T3: Subtitle re-timing integration via render_timeline (dry_run).

    render_timeline is called with dry_run=True (or build_plan is mocked) to avoid
    requiring real ffmpeg binaries.  The subtitle re-timing stage in render.py
    (_generate_retimed_srt / subtitle_warnings) is not yet implemented.

    Red reason for all tests in this group:
      - _generate_retimed_srt does not exist -> AttributeError when the
        subtitle re-timing branch is entered.
      - OR the expected postcondition (retimed .srt created / options path changed /
        warning emitted) is not satisfied because the code path is absent.
    """

    # -------------------------------------------------------------------
    # Shared setup helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _make_cut_timeline_with_subtitle(
        tmp_path: Path,
        srt_suffix: str = ".srt",
    ) -> tuple[Path, Path, Path]:
        """Create a minimal OTIO + subtitle fixture for re-timing tests.

        Returns (timeline_path, source_path, subtitle_path).
        All files are created under tmp_path.
        Timeline has a cut: source 0-5s kept, 5-10s cut, 10-20s kept.
        """
        source = tmp_path / "clip.mp4"
        source.touch()

        # Build a cut timeline: 2 clips from same source, 5s gap between them
        clip1 = _make_clip(str(source), 0.0, 5.0)
        clip2 = _make_clip(str(source), 10.0, 10.0)
        tl = _make_timeline([clip1, clip2])

        tl_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(tl_path))

        # Subtitle: cue at source 1-3s (lands in first kept) + cue at 7-9s (in cut)
        srt_name = "subs" + srt_suffix
        subtitle_path = tmp_path / srt_name
        subtitle_path.write_text(_SIMPLE_SRT, encoding="utf-8")

        return tl_path, source, subtitle_path

    @staticmethod
    def _make_identity_timeline(tmp_path: Path) -> tuple[Path, Path, Path]:
        """Create an identity timeline (no cut, no warp) with a subtitle."""
        source = tmp_path / "clip.mp4"
        source.touch()

        clip = _make_clip(str(source), 0.0, 10.0)
        tl = _make_timeline([clip])
        tl_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(tl_path))

        subtitle_path = tmp_path / "subs.srt"
        subtitle_path.write_text(_SIMPLE_SRT, encoding="utf-8")

        return tl_path, source, subtitle_path

    @staticmethod
    def _make_multi_source_timeline(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        """Create a multi-source timeline (2 different sources) with a subtitle."""
        src_a = tmp_path / "clip_a.mp4"
        src_b = tmp_path / "clip_b.mp4"
        src_a.touch()
        src_b.touch()

        clip_a = _make_clip(str(src_a), 0.0, 5.0)
        clip_b = _make_clip(str(src_b), 0.0, 5.0)
        tl = _make_timeline([clip_a, clip_b])
        tl_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(tl_path))

        subtitle_path = tmp_path / "subs.srt"
        subtitle_path.write_text(_SIMPLE_SRT, encoding="utf-8")

        return tl_path, src_a, src_b, subtitle_path

    @staticmethod
    def _multi_source_render_options(subtitle_path: Path) -> RenderOptions:
        """RenderOptions for multi-source render (explicit fps/width/height required)."""
        return RenderOptions(
            subtitle=SubtitleOptions(path=str(subtitle_path)),
            retime_markers="auto",
            # Multi-source requires explicit fps/width/height (render.py raises
            # INVALID_INPUT otherwise when no fps/resolution info from MediaInfo)
            fps=30.0,
            width=1920,
            height=1080,
        )

    def _make_media_info_for(self, path: str) -> Any:
        from clipwright.schemas import MediaInfo, StreamInfo

        return MediaInfo(
            path=path,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
            ],
            bit_rate=8_000_000,
        )

    # -------------------------------------------------------------------
    # T3-A: retime_markers="auto" + .srt + cut -> retimed .srt created
    # -------------------------------------------------------------------

    def test_auto_srt_cut_creates_retimed_srt(self, tmp_path: Path) -> None:
        """T3-A: auto + .srt + cut timeline -> {output_stem}.retimed.srt is created.

        Decision B (corrected): retimed filename = output_stem.retimed.srt, not
        subtitle_stem.retimed.srt.

        Red reason: _generate_retimed_srt does not exist in render.py -> the retimed
        .srt file is never written.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        output_path = tmp_path / "edited.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                ),
                dry_run=True,
            )

        # Decision B: retimed file must be named after OUTPUT stem (edited.retimed.srt)
        expected_retimed = tmp_path / "edited.retimed.srt"
        assert expected_retimed.exists(), (
            f"Expected {expected_retimed} to be created by _generate_retimed_srt. "
            "_generate_retimed_srt is not implemented yet."
        )

    def test_auto_srt_cut_options_path_is_retimed(self, tmp_path: Path) -> None:
        """T3-A: In the dry_run data, the subtitle path must point to the retimed .srt.

        After _generate_retimed_srt, render.py should replace options.subtitle.path
        with the retimed file path before passing to build_plan.  The dry_run
        filter_complex / ffmpeg_args should reference the retimed path.

        Red reason: _generate_retimed_srt not implemented; options.path not replaced.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        output_path = tmp_path / "edited.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        fc: str = result["data"]["filter_complex"]
        # The filter_complex should reference "edited.retimed.srt", not "subs.srt"
        assert "edited.retimed.srt" in fc or "edited.retimed" in fc, (
            f"Expected 'edited.retimed.srt' in filter_complex, but got:\n{fc}\n"
            "_generate_retimed_srt is not implemented yet."
        )
        assert "subs.srt" not in fc or "edited.retimed.srt" in fc, (
            "filter_complex still references original subs.srt; options.path not replaced. "
            "_generate_retimed_srt is not implemented yet."
        )

    def test_auto_srt_source_file_unchanged(self, tmp_path: Path) -> None:
        """T3-A / AC-7: Original .srt file must not be modified (non-destructive).

        Red reason: not applicable (non-destructive is trivially true when the
        function is absent, but this acts as a regression guard once implemented).
        The test is written as a positive assertion: original content unchanged.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        original_content = srt_path.read_text(encoding="utf-8")
        output_path = tmp_path / "edited.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            try:
                render_timeline(
                    str(tl_path),
                    str(output_path),
                    options=RenderOptions(
                        subtitle=SubtitleOptions(path=str(srt_path)),
                        retime_markers="auto",
                    ),
                    dry_run=True,
                )
            except (AttributeError, NotImplementedError):
                pass  # Expected Red failure; still check file

        assert srt_path.read_text(encoding="utf-8") == original_content, (
            "Original .srt was modified! Non-destructive invariant (AC-7) violated."
        )

    # -------------------------------------------------------------------
    # T3-B: retime_markers="off" -> re-timing skipped (AC-6)
    # -------------------------------------------------------------------

    def test_off_skips_retiming_no_retimed_file(self, tmp_path: Path) -> None:
        """T3-B / AC-6: retime_markers='off' -> retimed .srt NOT created.

        Even with a cut timeline + .srt, 'off' must skip re-timing completely.
        Red reason: once _generate_retimed_srt exists, the 'off' guard must be
        respected; if it is missing, the retimed file will be created unexpectedly.
        Currently Red because _generate_retimed_srt does not exist, but the test
        asserts the negative outcome (no file) which is trivially true now.
        This is a regression guard.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        output_path = tmp_path / "edited.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="off",
                ),
                dry_run=True,
            )

        expected_retimed = tmp_path / "edited.retimed.srt"
        assert not expected_retimed.exists(), (
            "retime_markers='off' must not create a retimed .srt file (AC-6)."
        )

    def test_off_filter_complex_uses_original_srt(self, tmp_path: Path) -> None:
        """T3-B / AC-6: retime_markers='off' -> filter_complex references original .srt.

        Red reason: once _generate_retimed_srt is implemented, the 'off' branch must
        not replace options.subtitle.path.  This test pins the correct behaviour.
        Currently this test passes trivially (function absent = path never replaced).
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        output_path = tmp_path / "edited.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="off",
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        fc: str = result["data"]["filter_complex"]
        # Original srt should appear (or its abs path), NOT the retimed path
        assert "edited.retimed.srt" not in fc, (
            "retime_markers='off' must not replace subtitle path with retimed .srt (AC-6)."
        )

    # -------------------------------------------------------------------
    # T3-C: multi-source -> skip + warning (AC-9)
    # -------------------------------------------------------------------

    def test_multi_source_skip_warning(self, tmp_path: Path) -> None:
        """T3-C / AC-9: Multi-source timeline -> re-timing skipped + warning emitted.

        Warning text must contain: "retime_markers skipped: multi-source timeline
        is not supported" (§4.2 / FR-7).

        Red reason: warning emission logic not yet implemented in render.py.
        """
        from clipwright_render.render import render_timeline

        tl_path, src_a, src_b, srt_path = self._make_multi_source_timeline(tmp_path)
        output_path = tmp_path / "out.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=self._multi_source_render_options(srt_path),
                dry_run=True,
            )

        assert result["ok"] is True, (
            f"render_timeline must succeed for multi-source. error={result.get('error')}"
        )
        warnings: list[str] = result.get("warnings", [])
        multi_source_warns = [
            w for w in warnings if "multi-source" in w.lower()
        ]
        assert len(multi_source_warns) >= 1, (
            f"Expected warning containing 'multi-source' for multi-source timeline, "
            f"got warnings={warnings}. "
            "Multi-source skip warning not yet implemented in render.py (AC-9)."
        )

    def test_multi_source_no_retimed_file_created(self, tmp_path: Path) -> None:
        """T3-C / AC-9: Multi-source -> no retimed .srt created (skipped)."""
        from clipwright_render.render import render_timeline

        tl_path, src_a, src_b, srt_path = self._make_multi_source_timeline(tmp_path)
        output_path = tmp_path / "out.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            render_timeline(
                str(tl_path),
                str(output_path),
                options=self._multi_source_render_options(srt_path),
                dry_run=True,
            )

        expected_retimed = tmp_path / "out.retimed.srt"
        assert not expected_retimed.exists(), (
            "Multi-source timeline must NOT create a retimed .srt file (AC-9)."
        )

    # -------------------------------------------------------------------
    # T3-D / T3-E: .vtt / .ass subtitle -> skip + warning
    # -------------------------------------------------------------------

    @pytest.mark.parametrize("suffix", [".vtt", ".ass"])
    def test_non_srt_subtitle_skip_warning(
        self, suffix: str, tmp_path: Path
    ) -> None:
        """T3-D/E: .vtt or .ass subtitle -> re-timing skipped + warning emitted.

        Warning text must contain: "subtitle re-timing skipped: only .srt is
        supported in this version".

        Red reason: warning emission for non-.srt subtitles not yet implemented.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(
            tmp_path, srt_suffix=suffix
        )
        output_path = tmp_path / "out.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        warnings: list[str] = result.get("warnings", [])
        skip_warns = [w for w in warnings if "only .srt" in w.lower()]
        assert len(skip_warns) >= 1, (
            f"Expected warning 'only .srt is supported' for {suffix} subtitle, "
            f"got warnings={warnings}. "
            "Non-.srt skip warning not yet implemented."
        )

    # -------------------------------------------------------------------
    # T3-F: All-cue-drop -> subtitle filter skipped + 1 warning (Decision A)
    # -------------------------------------------------------------------

    def test_all_cues_dropped_subtitle_filter_skipped(self, tmp_path: Path) -> None:
        """T3-F / Decision A: All SRT cues drop -> subtitle filter NOT in filter_complex.

        When every cue falls in removed region, no empty .srt is generated;
        instead the subtitle filter is skipped entirely + 1 warning is emitted.

        Red reason: _generate_retimed_srt + all-drop handling not implemented.
        """
        from clipwright_render.render import render_timeline

        source = tmp_path / "clip.mp4"
        source.touch()

        # Cut: keep 0-2s and 12-20s, remove 2-12s entirely
        clip1 = _make_clip(str(source), 0.0, 2.0)
        clip2 = _make_clip(str(source), 12.0, 8.0)
        tl = _make_timeline([clip1, clip2])
        tl_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(tl_path))

        # Subtitle: both cues fall in the removed range 2-12s
        all_drop_srt = textwrap.dedent("""\
            1
            00:00:03,000 --> 00:00:05,000
            Cue in cut

            2
            00:00:07,000 --> 00:00:09,000
            Also in cut
            """)
        srt_path = tmp_path / "subs.srt"
        srt_path.write_text(all_drop_srt, encoding="utf-8")

        output_path = tmp_path / "out.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        fc: str = result["data"]["filter_complex"]

        # Decision A: subtitle filter must be skipped (not present in filter_complex)
        assert "subtitles=" not in fc, (
            "All-cue-drop must skip the subtitle filter entirely (Decision A), "
            "but 'subtitles=' found in filter_complex. "
            "_generate_retimed_srt not yet implemented."
        )

        # 1 warning for the all-drop event
        warnings: list[str] = result.get("warnings", [])
        all_drop_warns = [
            w for w in warnings
            if "all" in w.lower() or "drop" in w.lower() or "no cues" in w.lower()
        ]
        assert len(all_drop_warns) >= 1, (
            f"Expected >=1 warning for all-cues-dropped, got warnings={warnings}. "
            "All-cue-drop warning not yet implemented."
        )

    # -------------------------------------------------------------------
    # T3-G: Retimed .srt name = {output_stem}.retimed.srt (Decision B corrected)
    # -------------------------------------------------------------------

    def test_retimed_srt_named_after_output_stem(self, tmp_path: Path) -> None:
        """T3-G / Decision B: Retimed .srt must be named {output_stem}.retimed.srt.

        Output = 'my_video.mp4' + subtitle = 'captions.srt'
        -> Retimed file must be 'my_video.retimed.srt' (output stem, NOT subtitle stem).
        So 'captions.retimed.srt' must NOT be created.

        Red reason: _generate_retimed_srt not yet implemented.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        # Use a different output stem than the subtitle stem to distinguish them
        output_path = tmp_path / "my_video.mp4"

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                ),
                dry_run=True,
            )

        # Correct name: output stem (my_video.retimed.srt)
        expected_correct = tmp_path / "my_video.retimed.srt"
        # Wrong name: subtitle stem (subs.retimed.srt)
        expected_wrong = tmp_path / "subs.retimed.srt"

        assert expected_correct.exists(), (
            f"Expected '{expected_correct}' (output-stem based name) to be created. "
            "_generate_retimed_srt is not implemented yet."
        )
        assert not expected_wrong.exists(), (
            f"'{expected_wrong}' must NOT be created (subtitle-stem naming is wrong). "
            "Decision B (corrected): output stem must be used."
        )

    # -------------------------------------------------------------------
    # T3-H: overwrite=False + existing retimed .srt -> INVALID_INPUT + hint
    # -------------------------------------------------------------------

    def test_overwrite_false_existing_retimed_srt_raises(self, tmp_path: Path) -> None:
        """T3-H: overwrite=False + existing retimed .srt -> INVALID_INPUT with hint.

        Decision B: if {output_stem}.retimed.srt already exists and overwrite=False,
        raise ClipwrightError(INVALID_INPUT) with hint to set overwrite=True.

        Red reason: _generate_retimed_srt not yet implemented.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        output_path = tmp_path / "edited.mp4"

        # Pre-create the retimed .srt to simulate collision
        retimed_path = tmp_path / "edited.retimed.srt"
        retimed_path.write_text("existing content", encoding="utf-8")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda p: self._make_media_info_for(p),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                    overwrite=False,
                ),
                dry_run=True,
            )

        assert exc_info.value.code == ErrorCode.INVALID_INPUT, (
            f"Expected INVALID_INPUT, got {exc_info.value.code}. "
            "_generate_retimed_srt overwrite guard not yet implemented."
        )
        # Hint must mention overwrite=True
        assert "overwrite" in exc_info.value.hint.lower(), (
            f"Hint must mention 'overwrite=True', got: {exc_info.value.hint!r}. "
            "_generate_retimed_srt overwrite guard not yet implemented."
        )

    # -------------------------------------------------------------------
    # T3-I: overwrite=True + existing retimed .srt -> succeeds (replaces)
    # -------------------------------------------------------------------

    def test_overwrite_true_existing_retimed_srt_replaces(
        self, tmp_path: Path
    ) -> None:
        """T3-I: overwrite=True + existing retimed .srt -> succeeds, replaces file.

        Red reason: _generate_retimed_srt not yet implemented.
        """
        from clipwright_render.render import render_timeline

        tl_path, source, srt_path = self._make_cut_timeline_with_subtitle(tmp_path)
        output_path = tmp_path / "edited.mp4"

        # Pre-create the retimed .srt
        retimed_path = tmp_path / "edited.retimed.srt"
        retimed_path.write_text("old content", encoding="utf-8")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda p: self._make_media_info_for(p),
        ):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                    overwrite=True,
                ),
                dry_run=True,
            )

        assert result["ok"] is True, (
            "overwrite=True must not raise even when retimed .srt exists. "
            "_generate_retimed_srt overwrite handling not yet implemented."
        )
        # The retimed file must be overwritten (content changed)
        assert retimed_path.read_text(encoding="utf-8") != "old content", (
            "Retimed .srt must be overwritten when overwrite=True. "
            "_generate_retimed_srt not yet implemented."
        )


# ---------------------------------------------------------------------------
# T4: Identity timeline regression (AC-5)
# ---------------------------------------------------------------------------


class TestIdentityTimelineRegression:
    """T4: Identity (no cut, no warp) timeline -> cue/overlay unchanged, warnings=[].

    AC-5: When ProgramTimeMap.has_cut=False and has_warp=False, re-timing is a
    no-op; text_overlay start_s/end_s must remain unchanged and warnings must be [].
    This test also verifies the build_plan.warnings path is clean.

    For the identity case, retime_text_overlays would return overlays unchanged.
    The test verifies:
      1. drawtext enable range matches the original source times (start_sec=2.0, end_sec=6.0).
      2. plan.warnings is empty.

    Red reason for part (1): once retime_text_overlays is implemented, it might
    incorrectly shift windows even for identity timelines if the no-op guard
    (has_cut=False and has_warp=False) is missing.  Currently this test PASSES
    (no retime_text_overlays call), so it acts as a regression guard that must
    remain Green after implementation.

    The test is included here to fulfil the spec requirement ("恒等タイムライン回帰
    1ケース含める").
    """

    def test_identity_timeline_overlay_unchanged_no_warnings(self) -> None:
        """AC-5: Identity timeline -> overlay start/end unchanged, plan.warnings=[].

        Marker: start_sec=2.0, duration_sec=4.0 -> source 2-6s.
        Identity map (0-10s, no cut, no warp):
          - program 2s == source 2s, program 6s == source 6s.
          - drawtext enable='between(t,2.0,6.0)' (or equivalent).
          - plan.warnings == [].
        """
        tl = _make_timeline([_make_clip("/src/clip.mp4", 0.0, 10.0)])
        _add_text_overlay_marker(
            tl, text="Identity", start_sec=2.0, duration_sec=4.0
        )

        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0)

        with patch("pathlib.Path.is_file", return_value=True):
            plan = build_plan(ranges, probe, RenderOptions(retime_markers="auto"))

        # 1. Overlay must appear in filter_complex (drawtext present)
        assert "drawtext" in plan.filter_complex, (
            "drawtext must be present for identity timeline overlay."
        )

        # 2. enable range must reference the original source times (2.0 and 6.0)
        fc = plan.filter_complex
        assert "2.0" in fc or "2," in fc, (
            f"Identity overlay start_s=2.0 must appear in filter_complex. Got:\n{fc}"
        )
        assert "6.0" in fc or ",6." in fc, (
            f"Identity overlay end_s=6.0 must appear in filter_complex. Got:\n{fc}"
        )

        # 3. No re-timing warnings for identity map (AC-5)
        retime_warns = [
            w for w in plan.warnings
            if any(kw in w.lower() for kw in ("drop", "split", "shift", "clip"))
        ]
        assert retime_warns == [], (
            f"Identity timeline must produce 0 re-timing warnings. "
            f"Got: {retime_warns}"
        )

    def test_identity_timeline_subtitle_no_retimed_file(self, tmp_path: Path) -> None:
        """AC-5: Identity timeline with .srt -> no retimed .srt file created.

        Identity map has has_cut=False and has_warp=False, so re-timing is a no-op.
        No {output_stem}.retimed.srt should be written.
        """
        from clipwright_render.render import render_timeline

        source = tmp_path / "clip.mp4"
        source.touch()
        clip = _make_clip(str(source), 0.0, 10.0)
        tl = _make_timeline([clip])
        tl_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(tl_path))

        srt_path = tmp_path / "subs.srt"
        srt_path.write_text(_SIMPLE_SRT, encoding="utf-8")
        output_path = tmp_path / "out.mp4"

        from clipwright.schemas import MediaInfo, StreamInfo

        def _mi(p: str) -> MediaInfo:
            return MediaInfo(
                path=p,
                container="mov,mp4,m4a,3gp,3g2,mj2",
                duration=None,
                streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
                bit_rate=8_000_000,
            )

        with patch("clipwright_render.render.inspect_media", side_effect=_mi):
            result = render_timeline(
                str(tl_path),
                str(output_path),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt_path)),
                    retime_markers="auto",
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        retimed_path = tmp_path / "out.retimed.srt"
        assert not retimed_path.exists(), (
            "Identity timeline (no cut/warp) must NOT create a retimed .srt file (AC-5)."
        )
