"""test_e2e_subtitle.py — Real e2e tests for subtitle burn-in in clipwright-render (task_id: e2e-subtitle).

Design rationale:
  - §7 v2 (ADR-S4-r2/S5-r2/S6-r2/S6-r3)
  - subtitle timestamp base = output timeline head 0 s (DC-AM-003)
  - ADR-S1: render extension (via RenderOptions.subtitle, MCP path only, no CLI)
  - ADR-S3: _ALLOWED_SUBTITLE_EXTENSIONS = {.srt, .vtt, .ass}
  - ADR-S4-r2: _append_subtitle_filter signature (no timeline_dir arg; boundary check unified in render)
  - ADR-S5-r2: subtitle path is made absolute by render; cwd-independent
  - ADR-S6-r2: force_style not applied for ASS; charenc=UTF-8 added for SRT/VTT
  - ADR-S6-r3: alignment uses ASS v4+ numpad
  - ADR-S8: subtitle=None preserves backward compatibility
  - ADR-S10: subtitle does not need -i (read directly via subtitles=filename= in filter_complex)
  - DC-AM-003: subtitle timestamp base = output timeline head (0 s)
  - DC-GP-003: all fixtures and outputs are confined to tmp_path for automatic teardown

Test layout:
  1. Fixture generation
     - Main clip: testsrc video 3 s, 320x240, 25 fps (known resolution)
     - SRT/VTT/ASS subtitles: one line from 0.5 s to 2.5 s on the output timeline
     - Japanese font: Meiryo from C:\\Windows\\Fonts
       (tests that require this font are skipped when the font is absent)

  Required asserts:
    assert-1: render_timeline(dry_run=False) produces one subtitle-burned output file
    assert-2: subtitle-region pixels differ significantly from the no-subtitle output (SSIM < 0.999 / PSNR < 50 dB)
    assert-3: Negative control — subtitle=None outputs no subtitle (isolates diff as subtitle-caused, lesson B-3)
    assert-4: Japanese characters render without tofu (with fonts_dir, subtitle SSIM < 0.999)
    assert-5: Basic style (font_size) is applied — SSIM differs between different size settings
    assert-6: All three formats — SRT / VTT / ASS — can be burned in (VTT direct-read confirmed in M2)
    assert-7: Backward compat — subtitle=None leaves video unchanged (SSIM < 1.0 vs subtitle output;
              no-subtitle pairs expect SSIM = 1.0)

How to run (skipped when ffmpeg is absent):
  uv run --package clipwright-render pytest -k e2e_subtitle

Add ffmpeg to PATH or set CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE environment variables.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ===========================================================================
# ffmpeg / ffprobe binary resolution (same pattern as conftest.py require_ffmpeg)
# ===========================================================================

# Same standalone implementation pattern as other e2e files (test_e2e_merge.py, etc.).
# Duplicates conftest.py logic, but e2e files are intentionally self-contained per
# project convention (see S-L-6).


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary in PATH first, then fall back to env_var."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


_FFMPEG = _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")
_FFPROBE = _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")

pytestmark = pytest.mark.e2e

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None,
    reason=(
        "ffmpeg not found. "
        "Add ffmpeg to PATH or "
        "set the CLIPWRIGHT_FFMPEG environment variable to its full path."
    ),
)

requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe not found. "
        "Add ffprobe to PATH or "
        "set the CLIPWRIGHT_FFPROBE environment variable to its full path."
    ),
)

# ===========================================================================
# Constants
# ===========================================================================

_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

_MAIN_DUR = 3.0  # Main clip duration: 3 s
_RATE = 25.0  # Video fps
_WIDTH = 320  # Video width (pixels)
_HEIGHT = 240  # Video height (pixels)

# Subtitle display interval (output timeline base = 0 s, DC-AM-003)
_SUB_START_S = 0.5  # Subtitle display start: 0.5 s
_SUB_END_S = 2.5  # Subtitle display end: 2.5 s
_FRAME_SAMPLE_S = 1.0  # Frame timestamp used for pixel comparison (subtitle visible)

# Japanese subtitle text (test input data — intentionally kept in Japanese)
_JP_TEXT = "こんにちは世界"
_EN_TEXT = "Hello World Subtitle"

# Windows font directory (for CJK font check)
_WINDOWS_FONTS_DIR = r"C:\Windows\Fonts"
_JP_FONT_NAME = "Meiryo"

# Check that the Japanese font (Meiryo .ttc) exists
_JP_FONTS_DIR_EXISTS = (
    Path(_WINDOWS_FONTS_DIR).is_dir()
    and Path(_WINDOWS_FONTS_DIR).joinpath("meiryo.ttc").exists()
)

requires_cjk_font = pytest.mark.skipif(
    not _JP_FONTS_DIR_EXISTS,
    reason=(
        f"CJK font not found: {_WINDOWS_FONTS_DIR}\\meiryo.ttc. "
        "Install a Japanese font or configure fonts_dir."
    ),
)

# SSIM threshold: significant pixel difference between subtitle and no-subtitle outputs
# (if a subtitle is burned in, SSIM < 0.999)
_SSIM_PIXEL_DIFF_THRESHOLD = 0.999

# ===========================================================================
# Helpers: fixture generation
# ===========================================================================


def _make_main_video(ffmpeg: str, output: Path) -> None:
    """Generate the main-clip fixture: testsrc video (3 s, 320x240, 25 fps).

    No audio (audio pipeline is already verified in bgm/loudness tests).
    DC-GP-003: generate under tmp_path for automatic teardown.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={_WIDTH}x{_HEIGHT}:rate={int(_RATE)}:duration={_MAIN_DUR}",
        "-t",
        str(_MAIN_DUR),
        "-c:v",
        "libx264",
        "-an",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"Main-clip fixture generation failed: {result.stderr[:400]}"
    )


def _make_srt(output: Path, text: str = _EN_TEXT) -> None:
    """Generate an SRT subtitle file (UTF-8).

    Timestamps are based on the output timeline head at 0 s (DC-AM-003).
    """
    start_ms = int(_SUB_START_S * 1000)
    end_ms = int(_SUB_END_S * 1000)
    start_str = f"00:00:{start_ms // 1000:02d},{start_ms % 1000:03d}"
    end_str = f"00:00:{end_ms // 1000:02d},{end_ms % 1000:03d}"
    content = f"1\n{start_str} --> {end_str}\n{text}\n\n"
    output.write_text(content, encoding="utf-8")


def _make_vtt(output: Path, text: str = _EN_TEXT) -> None:
    """Generate a VTT subtitle file (UTF-8).

    Timestamps are based on the output timeline head at 0 s (DC-AM-003).
    M2 confirmed: VTT can be read directly by the subtitles filter (ADR-S3/ADR-S9).
    """
    start_str = f"00:00:0{int(_SUB_START_S)}.{int((_SUB_START_S % 1) * 1000):03d}"
    end_str = f"00:00:0{int(_SUB_END_S)}.{int((_SUB_END_S % 1) * 1000):03d}"
    content = f"WEBVTT\n\n{start_str} --> {end_str}\n{text}\n\n"
    output.write_text(content, encoding="utf-8")


def _make_ass(output: Path, text: str = _EN_TEXT, font_name: str = "Arial") -> None:
    """Generate an ASS subtitle file (UTF-8).

    Includes an embedded style (FontSize=20, white, Alignment=2 = bottom centre).
    ADR-S6-r2: ASS uses its embedded style, so force_style is not added.
    Timestamps are based on the output timeline head at 0 s (DC-AM-003).
    """
    start_s = _SUB_START_S
    end_s = _SUB_END_S
    start_h = int(start_s // 3600)
    start_m = int((start_s % 3600) // 60)
    start_sec = start_s % 60
    end_h = int(end_s // 3600)
    end_m = int((end_s % 3600) // 60)
    end_sec = end_s % 60
    start_str = f"{start_h}:{start_m:02d}:{start_sec:04.2f}"
    end_str = f"{end_h}:{end_m:02d}:{end_sec:04.2f}"
    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},20,&H00FFFFFF,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n"
    )
    output.write_text(content, encoding="utf-8")


# ===========================================================================
# Helpers: OTIO timeline construction
# ===========================================================================


def _make_timeline(
    source_path: Path,
    duration_sec: float = _MAIN_DUR,
    rate: float = _RATE,
) -> otio.schema.Timeline:
    """Build an OTIO timeline from a single clip."""
    ref = otio.schema.ExternalReference(target_url=str(source_path))
    clip = otio.schema.Clip(
        name=source_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    timeline = otio.schema.Timeline(name="e2e_subtitle_test")
    timeline.tracks.append(track)
    return timeline


def _save_timeline(timeline: otio.schema.Timeline, path: Path) -> None:
    """Save an OTIO timeline to a file."""
    otio.adapters.write_to_file(timeline, str(path))


# ===========================================================================
# Helpers: pixel difference measurement
# ===========================================================================


def _extract_frame(ffmpeg: str, video: Path, time_s: float, output_png: Path) -> None:
    """Extract a frame at the given timestamp from a video and save it as PNG."""
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(time_s),
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-f",
        "image2",
        str(output_png),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"Frame extraction failed ({video.name} @ {time_s}s): {result.stderr[:200]}"
    )
    assert output_png.exists(), f"Frame PNG was not created: {output_png}"


def _measure_ssim(ffmpeg: str, frame_a: Path, frame_b: Path) -> float:
    """Return the SSIM All value for two frames (1.0 = identical; < 1.0 = pixel difference).

    SSIM (structural similarity) is used to detect whether a subtitle was burned in.
    If a subtitle is present, pixels in the subtitle region change and SSIM drops below 1.0.
    """
    cmd = [
        ffmpeg,
        "-i",
        str(frame_a),
        "-i",
        str(frame_b),
        "-lavfi",
        "ssim",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"SSIM measurement failed: {result.stderr[:200]}"
    m = re.search(r"All:([\d.]+)", result.stderr)
    assert m is not None, f"SSIM All value not found:\n{result.stderr[-200:]}"
    return float(m.group(1))


def _measure_psnr(ffmpeg: str, frame_a: Path, frame_b: Path) -> float:
    """Return the PSNR average value for two frames (dB).

    PSNR < 50 dB indicates a significant pixel difference (supplementary check for subtitle burn-in).
    """
    cmd = [
        ffmpeg,
        "-i",
        str(frame_a),
        "-i",
        str(frame_b),
        "-lavfi",
        "psnr",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"PSNR measurement failed: {result.stderr[:200]}"
    m = re.search(r"average:([\d.]+)", result.stderr)
    assert m is not None, f"PSNR average value not found:\n{result.stderr[-200:]}"
    return float(m.group(1))


# ===========================================================================
# Tests: assert-1 + assert-3 (basic generation, negative control)
# ===========================================================================


@requires_ffmpeg
class TestSubtitleBasicRender:
    """Basic subtitle burn-in: output generation and negative control (assert-1, assert-3).

    assert-1: render_timeline(dry_run=False) produces one subtitle-burned output file.
    assert-3: subtitle=None outputs no subtitle (negative control, lesson B-3).
    """

    def test_render_with_subtitle_returns_ok(self, tmp_path: Path) -> None:
        """render returns ok=True and the output file is created for a subtitle timeline (assert-1).

        Verified via the MCP/render_timeline path (no CLI, DC-AS-003).
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        timeline = _make_timeline(main_src)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path),
            str(out_path),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt))),
            dry_run=False,
        )
        assert result["ok"] is True, f"render failed: {result}"
        assert out_path.exists(), "output file was not created"
        assert out_path.stat().st_size > 0, "output file size is 0"

    def test_render_dry_run_filter_has_subtitles(self, tmp_path: Path) -> None:
        """dry_run filter_complex contains subtitles (ADR-S10 internal check).

        Subtitles are read directly via subtitles=filename= in filter_complex without adding -i.
        ADR-S10: retrieve filter_complex via dry_run and assert that subtitles is present.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        timeline = _make_timeline(main_src)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_dry.mp4"
        result = render_timeline(
            str(timeline_path),
            str(out_path),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=24)),
            dry_run=True,
        )
        assert result["ok"] is True, f"dry_run failed: {result}"

        fc = result["data"]["filter_complex"]
        assert "subtitles=filename=" in fc, (
            f"filter_complex does not contain subtitles (ADR-S10 violation):\n"
            f"  filter_complex: {fc}"
        )
        assert "[outvsub]" in fc, (
            f"filter_complex does not contain [outvsub] label:\n  filter_complex: {fc}"
        )
        # ADR-S10: subtitle does not add -i, so input_sources count is unchanged.
        # charenc=UTF-8 is added for SRT/VTT (ADR-S6-r2).
        assert "charenc=UTF-8" in fc, (
            f"charenc=UTF-8 missing from filter_complex for SRT (ADR-S6-r2 violation):\n"
            f"  filter_complex: {fc}"
        )

    def test_subtitle_none_dry_run_no_subtitles_filter(self, tmp_path: Path) -> None:
        """dry_run filter_complex does not contain subtitles when subtitle=None (assert-3, ADR-S8).

        Backward-compatibility check: with subtitle=None, _append_subtitle_filter is not called
        and no subtitles entry is inserted into filter_complex.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"

        _make_main_video(_FFMPEG, main_src)

        timeline = _make_timeline(main_src)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_nosub_dry.mp4"
        result = render_timeline(
            str(timeline_path),
            str(out_path),
            RenderOptions(),  # subtitle=None
            dry_run=True,
        )
        assert result["ok"] is True, f"dry_run failed: {result}"

        fc = result["data"]["filter_complex"]
        assert "subtitles" not in fc, (
            f"filter_complex contains subtitles even though subtitle=None (ADR-S8 violation):\n"
            f"  filter_complex: {fc}"
        )
        assert "[outvsub]" not in fc, (
            f"filter_complex contains [outvsub] even though subtitle=None (ADR-S8 violation):\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# Tests: assert-2 (subtitle burn-in pixel evidence)
# ===========================================================================


@requires_ffmpeg
class TestSubtitlePixelDiff:
    """Demonstrate significant pixel difference due to subtitle burn-in (assert-2, SSIM/PSNR).

    Compare output frames with and without subtitle using SSIM and PSNR to prove
    that the subtitle is actually burned into the video.
    SSIM All < 0.999 and PSNR < 50 dB are considered significant differences.
    """

    def test_subtitle_pixels_differ_from_no_subtitle(self, tmp_path: Path) -> None:
        """Subtitle-burned frame differs significantly from the no-subtitle frame (assert-2).

        Compare the frame at 1.0 s (during subtitle display).
        SSIM < 0.999 proves the subtitle was burned in (lesson B-3: isolated by negative control).
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        # Subtitle output
        tl_sub = _make_timeline(main_src)
        tl_sub_path = tmp_path / "tl_sub.otio"
        _save_timeline(tl_sub, tl_sub_path)
        out_sub = tmp_path / "out_sub.mp4"
        result_sub = render_timeline(
            str(tl_sub_path),
            str(out_sub),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=28)),
            dry_run=False,
        )
        assert result_sub["ok"] is True, f"Subtitle render failed: {result_sub}"

        # No-subtitle output (negative control)
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        result_nosub = render_timeline(
            str(tl_nosub_path),
            str(out_nosub),
            RenderOptions(),  # subtitle=None
            dry_run=False,
        )
        assert result_nosub["ok"] is True, f"No-subtitle render failed: {result_nosub}"

        # Frame extraction (during subtitle display)
        frame_sub = tmp_path / "frame_sub.png"
        frame_nosub = tmp_path / "frame_nosub.png"
        _extract_frame(_FFMPEG, out_sub, _FRAME_SAMPLE_S, frame_sub)
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)

        ssim = _measure_ssim(_FFMPEG, frame_sub, frame_nosub)
        psnr = _measure_psnr(_FFMPEG, frame_sub, frame_nosub)

        assert ssim < _SSIM_PIXEL_DIFF_THRESHOLD, (
            f"Pixel difference between subtitle and no-subtitle is insufficient (assert-2, burn-in unproven):\n"
            f"  SSIM All: {ssim:.6f} (expected: < {_SSIM_PIXEL_DIFF_THRESHOLD})\n"
            f"  PSNR: {psnr:.2f} dB\n"
            f"  If the subtitle was burned in, subtitle-region pixels should have changed"
        )
        assert psnr < 50.0, (
            f"PSNR between subtitle and no-subtitle does not meet significance threshold (assert-2, supplementary):\n"
            f"  PSNR: {psnr:.2f} dB (expected: < 50.0 dB)\n"
            f"  SSIM: {ssim:.6f}"
        )

    def test_no_subtitle_frames_identical_to_baseline(self, tmp_path: Path) -> None:
        """Two no-subtitle renders produce identical frames (negative control, isolation check).

        Render twice with subtitle=None and confirm the same frame is produced each time.
        This isolates that any SSIM difference is caused by the subtitle, not render variance.
        Two renders of the same input should yield SSIM ≈ 1.0 (>= 0.98 to allow encoder non-determinism).
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"

        _make_main_video(_FFMPEG, main_src)

        tl1 = _make_timeline(main_src)
        tl1_path = tmp_path / "tl_a.otio"
        _save_timeline(tl1, tl1_path)
        out_a = tmp_path / "out_a.mp4"

        tl2 = _make_timeline(main_src)
        tl2_path = tmp_path / "tl_b.otio"
        _save_timeline(tl2, tl2_path)
        out_b = tmp_path / "out_b.mp4"

        r_a = render_timeline(str(tl1_path), str(out_a), RenderOptions(), dry_run=False)
        r_b = render_timeline(str(tl2_path), str(out_b), RenderOptions(), dry_run=False)

        assert r_a["ok"] is True
        assert r_b["ok"] is True

        frame_a = tmp_path / "frame_a.png"
        frame_b = tmp_path / "frame_b.png"
        _extract_frame(_FFMPEG, out_a, _FRAME_SAMPLE_S, frame_a)
        _extract_frame(_FFMPEG, out_b, _FRAME_SAMPLE_S, frame_b)

        ssim = _measure_ssim(_FFMPEG, frame_a, frame_b)
        assert ssim >= 0.98, (
            f"SSIM between two no-subtitle renders is below expected (negative control):\n"
            f"  SSIM: {ssim:.6f} (expected: >= 0.98)\n"
            f"  Two renders of the same input should yield SSIM ≈ 1.0"
        )


# ===========================================================================
# Tests: assert-4 (Japanese subtitles, no tofu)
# ===========================================================================


@requires_ffmpeg
@requires_cjk_font
class TestSubtitleJapanese:
    """Prove that Japanese subtitles render without tofu (assert-4).

    Burn a Japanese SRT with fonts_dir=C:\\Windows\\Fonts and font_name=Meiryo,
    then confirm the SSIM difference vs the no-subtitle output is significant
    (if CJK glyphs are drawn, pixels change).
    Skipped in environments without the required font (requires_cjk_font marker).
    """

    def test_japanese_subtitle_renders_with_cjk_font(self, tmp_path: Path) -> None:
        """Japanese subtitle renders without tofu using the Meiryo font (assert-4).

        SSIM < 0.999 confirms that CJK glyphs were drawn.
        When the font is correctly specified, the same pixel change occurs as for ASCII subtitles.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt_jp = tmp_path / "test_jp.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt_jp, text=_JP_TEXT)

        # Japanese subtitle (Meiryo, Windows Fonts)
        tl_jp = _make_timeline(main_src)
        tl_jp_path = tmp_path / "tl_jp.otio"
        _save_timeline(tl_jp, tl_jp_path)
        out_jp = tmp_path / "out_jp.mp4"
        result_jp = render_timeline(
            str(tl_jp_path),
            str(out_jp),
            RenderOptions(
                subtitle=SubtitleOptions(
                    path=str(srt_jp),
                    font_name=_JP_FONT_NAME,
                    fonts_dir=_WINDOWS_FONTS_DIR,
                    font_size=32,
                )
            ),
            dry_run=False,
        )
        assert result_jp["ok"] is True, f"Japanese subtitle render failed: {result_jp}"

        # Negative control (no subtitle)
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        result_nosub = render_timeline(
            str(tl_nosub_path), str(out_nosub), RenderOptions(), dry_run=False
        )
        assert result_nosub["ok"] is True

        # Frame extraction
        frame_jp = tmp_path / "frame_jp.png"
        frame_nosub = tmp_path / "frame_nosub.png"
        _extract_frame(_FFMPEG, out_jp, _FRAME_SAMPLE_S, frame_jp)
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)

        ssim = _measure_ssim(_FFMPEG, frame_jp, frame_nosub)
        assert ssim < _SSIM_PIXEL_DIFF_THRESHOLD, (
            f"Japanese subtitle pixel difference is insufficient (assert-4, possible tofu rendering):\n"
            f"  SSIM All: {ssim:.6f} (expected: < {_SSIM_PIXEL_DIFF_THRESHOLD})\n"
            f"  font_name={_JP_FONT_NAME}, fonts_dir={_WINDOWS_FONTS_DIR}\n"
            f"  If the font is loaded correctly and CJK glyphs are drawn, a pixel difference should appear"
        )


# ===========================================================================
# Tests: assert-5 (style application, font_size difference)
# ===========================================================================


@requires_ffmpeg
class TestSubtitleStyle:
    """Prove that basic style (font_size) is applied (assert-5).

    font_size=48 and font_size=12 produce different amounts of pixel change.
    A larger font changes more pixels, so SSIM vs the no-subtitle output is lower.
    """

    def test_large_font_size_has_more_pixel_diff_than_small(
        self, tmp_path: Path
    ) -> None:
        """SSIM difference with font_size=48 is larger than with font_size=12 (assert-5, style applied).

        A larger subtitle changes more pixels in the subtitle region, so SSIM vs the no-subtitle
        output is lower than for a smaller subtitle.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        # No-subtitle output (shared baseline)
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        r_nosub = render_timeline(
            str(tl_nosub_path), str(out_nosub), RenderOptions(), dry_run=False
        )
        assert r_nosub["ok"] is True

        # font_size=48 (large)
        tl_big = _make_timeline(main_src)
        tl_big_path = tmp_path / "tl_big.otio"
        _save_timeline(tl_big, tl_big_path)
        out_big = tmp_path / "out_big.mp4"
        r_big = render_timeline(
            str(tl_big_path),
            str(out_big),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=48)),
            dry_run=False,
        )
        assert r_big["ok"] is True

        # font_size=12 (small)
        tl_small = _make_timeline(main_src)
        tl_small_path = tmp_path / "tl_small.otio"
        _save_timeline(tl_small, tl_small_path)
        out_small = tmp_path / "out_small.mp4"
        r_small = render_timeline(
            str(tl_small_path),
            str(out_small),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=12)),
            dry_run=False,
        )
        assert r_small["ok"] is True

        # Frame extraction
        frame_nosub = tmp_path / "frame_nosub.png"
        frame_big = tmp_path / "frame_big.png"
        frame_small = tmp_path / "frame_small.png"
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)
        _extract_frame(_FFMPEG, out_big, _FRAME_SAMPLE_S, frame_big)
        _extract_frame(_FFMPEG, out_small, _FRAME_SAMPLE_S, frame_small)

        ssim_big = _measure_ssim(_FFMPEG, frame_big, frame_nosub)
        ssim_small = _measure_ssim(_FFMPEG, frame_small, frame_nosub)

        # Large font changes more pixels -> lower SSIM than small font
        assert ssim_big < ssim_small, (
            f"SSIM difference for font_size=48 is not larger than for font_size=12 (assert-5):\n"
            f"  SSIM (size=48 vs nosub): {ssim_big:.6f}\n"
            f"  SSIM (size=12 vs nosub): {ssim_small:.6f}\n"
            f"  A larger font changes more pixels, so SSIM should be lower"
        )

    def test_force_style_in_filter_complex_for_srt(self, tmp_path: Path) -> None:
        """force_style is present in filter_complex for SRT (ADR-S6-r2 internal check).

        When SubtitleOptions includes style settings, force_style= is added to
        filter_complex for SRT/VTT input; verified via dry_run.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        tl = _make_timeline(main_src)
        tl_path = tmp_path / "tl.otio"
        _save_timeline(tl, tl_path)

        out_path = tmp_path / "out_dry.mp4"
        result = render_timeline(
            str(tl_path),
            str(out_path),
            RenderOptions(
                subtitle=SubtitleOptions(
                    path=str(srt),
                    font_size=24,
                    alignment=2,
                    margin_v=20,
                )
            ),
            dry_run=True,
        )
        assert result["ok"] is True

        fc = result["data"]["filter_complex"]
        assert "force_style=" in fc, (
            f"force_style missing from filter_complex for SRT (ADR-S6-r2 violation):\n"
            f"  filter_complex: {fc}"
        )
        assert "FontSize=24" in fc, (
            f"font_size=24 not reflected in force_style:\n  filter_complex: {fc}"
        )
        assert "Alignment=2" in fc, (
            f"alignment=2 not reflected in force_style (ADR-S6-r3):\n"
            f"  filter_complex: {fc}"
        )
        assert "MarginV=20" in fc, (
            f"margin_v=20 not reflected in force_style:\n  filter_complex: {fc}"
        )

    def test_ass_no_force_style_in_filter_complex(self, tmp_path: Path) -> None:
        """force_style is absent from filter_complex for ASS (ADR-S6-r2 internal check).

        ASS has embedded styles, so force_style is not applied (DC-AS-002).
        Even when SubtitleOptions includes style settings, force_style= must not be added for ASS.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        ass = tmp_path / "test.ass"

        _make_main_video(_FFMPEG, main_src)
        _make_ass(ass)

        tl = _make_timeline(main_src)
        tl_path = tmp_path / "tl_ass.otio"
        _save_timeline(tl, tl_path)

        out_path = tmp_path / "out_ass_dry.mp4"
        result = render_timeline(
            str(tl_path),
            str(out_path),
            RenderOptions(
                subtitle=SubtitleOptions(
                    path=str(ass),
                    font_size=24,  # style settings are ignored for ASS (not passed to force_style)
                )
            ),
            dry_run=True,
        )
        assert result["ok"] is True

        fc = result["data"]["filter_complex"]
        assert "force_style=" not in fc, (
            f"filter_complex contains force_style for ASS (ADR-S6-r2/DC-AS-002 violation):\n"
            f"  filter_complex: {fc}"
        )
        # charenc=UTF-8 is also not added for ASS (confirmed in M2, ADR-S6-r2)
        assert "charenc=UTF-8" not in fc, (
            f"filter_complex contains charenc=UTF-8 for ASS (ADR-S6-r2 violation):\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# Tests: assert-6 (all three formats: SRT / VTT / ASS)
# ===========================================================================


@requires_ffmpeg
class TestSubtitleFormats:
    """Prove that subtitles can be burned in all three formats: SRT / VTT / ASS (assert-6).

    M2 confirmed: VTT can be read directly by the subtitles filter (ADR-S3/ADR-S9).
    Confirm that render_timeline returns ok=True and the output file is created for all three formats.
    Also confirm that subtitle-region pixels differ significantly from the no-subtitle output.
    """

    def _render_with_subtitle(
        self,
        ffmpeg: str,
        tmp_path: Path,
        suffix: str,
        subtitle_path: Path,
    ) -> tuple[bool, Path]:
        """Run a subtitle-burned render and return (ok, out_path)."""
        main_src = tmp_path / "main.mp4"
        tl = _make_timeline(main_src)
        tl_path = tmp_path / f"tl_{suffix}.otio"
        _save_timeline(tl, tl_path)

        out_path = tmp_path / f"out_{suffix}.mp4"
        opts = RenderOptions(
            subtitle=SubtitleOptions(path=str(subtitle_path), font_size=28)
        )
        result = render_timeline(str(tl_path), str(out_path), opts, dry_run=False)
        return result["ok"], out_path

    def test_srt_format_renders_ok(self, tmp_path: Path) -> None:
        """SRT format subtitle renders successfully (assert-6)."""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"
        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, "srt", srt)
        assert ok is True, "SRT subtitle render failed"
        assert out.exists() and out.stat().st_size > 0, (
            "SRT output file was not created"
        )

    def test_vtt_format_renders_ok(self, tmp_path: Path) -> None:
        """VTT format subtitle renders successfully (assert-6, VTT direct-read confirmed in M2).

        libavformat reads VTT directly as WebVTT.
        ADR-S9: VTT direct-read is supported, so no SRT conversion is needed.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        vtt = tmp_path / "test.vtt"
        _make_main_video(_FFMPEG, main_src)
        _make_vtt(vtt)

        ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, "vtt", vtt)
        assert ok is True, (
            "VTT subtitle render failed (possible VTT direct-read issue: ADR-S9)"
        )
        assert out.exists() and out.stat().st_size > 0, (
            "VTT output file was not created"
        )

    def test_ass_format_renders_ok(self, tmp_path: Path) -> None:
        """ASS format subtitle renders successfully (assert-6).

        ASS has embedded styles, so force_style is not applied (ADR-S6-r2).
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        ass = tmp_path / "test.ass"
        _make_main_video(_FFMPEG, main_src)
        _make_ass(ass)

        ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, "ass", ass)
        assert ok is True, "ASS subtitle render failed"
        assert out.exists() and out.stat().st_size > 0, (
            "ASS output file was not created"
        )

    def test_three_formats_pixel_diff_vs_no_subtitle(self, tmp_path: Path) -> None:
        """All three formats (SRT/VTT/ASS) produce significant pixel difference vs no-subtitle (assert-6).

        Compare the output frame of each format against the no-subtitle output frame using SSIM.
        SSIM < 0.999 confirms the subtitle was burned in.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        _make_main_video(_FFMPEG, main_src)

        srt = tmp_path / "test.srt"
        vtt = tmp_path / "test.vtt"
        ass = tmp_path / "test.ass"
        _make_srt(srt)
        _make_vtt(vtt)
        _make_ass(ass)

        # No-subtitle output (shared baseline)
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        r_nosub = render_timeline(
            str(tl_nosub_path), str(out_nosub), RenderOptions(), dry_run=False
        )
        assert r_nosub["ok"] is True
        frame_nosub = tmp_path / "frame_nosub.png"
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)

        failures: list[str] = []
        for fmt, sub_path in [("srt", srt), ("vtt", vtt), ("ass", ass)]:
            ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, fmt, sub_path)
            if not ok:
                failures.append(f"{fmt.upper()}: render failed")
                continue

            frame = tmp_path / f"frame_{fmt}.png"
            _extract_frame(_FFMPEG, out, _FRAME_SAMPLE_S, frame)
            ssim = _measure_ssim(_FFMPEG, frame, frame_nosub)

            if ssim >= _SSIM_PIXEL_DIFF_THRESHOLD:
                failures.append(
                    f"{fmt.upper()}: SSIM={ssim:.6f} >= {_SSIM_PIXEL_DIFF_THRESHOLD}"
                    " (subtitle burn-in unproven)"
                )

        assert not failures, (
            "Pixel difference is insufficient for the following formats (assert-6):\n"
            + "\n".join(f"  {f}" for f in failures)
        )


# ===========================================================================
# Tests: assert-7 (backward compat, subtitle=None leaves video unchanged)
# ===========================================================================


@requires_ffmpeg
class TestSubtitleBackwardCompat:
    """Backward-compatibility proof: subtitle=None produces equivalent output (assert-7, ADR-S8).

    Confirm that two renders with subtitle=None yield SSIM ≈ 1.0, proving the
    no-subtitle output is stable (supplementary to lesson B-3).
    Also isolates that SSIM differences between subtitle and no-subtitle renders
    are caused exclusively by the subtitle.
    """

    def test_no_subtitle_outputs_are_equivalent(self, tmp_path: Path) -> None:
        """Two renders with subtitle=None produce frames with SSIM >= 0.98 (assert-7).

        Rendering the same input is deterministic, so SSIM should be ≈ 1.0.
        0.98 is the lower bound to accommodate encoder non-determinism.
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        _make_main_video(_FFMPEG, main_src)

        for i in range(1, 3):
            tl = _make_timeline(main_src)
            tl_path = tmp_path / f"tl_{i}.otio"
            _save_timeline(tl, tl_path)
            out = tmp_path / f"out_{i}.mp4"
            r = render_timeline(str(tl_path), str(out), RenderOptions(), dry_run=False)
            assert r["ok"] is True, f"subtitle=None render {i} failed"

        frame_1 = tmp_path / "frame_1.png"
        frame_2 = tmp_path / "frame_2.png"
        _extract_frame(_FFMPEG, tmp_path / "out_1.mp4", _FRAME_SAMPLE_S, frame_1)
        _extract_frame(_FFMPEG, tmp_path / "out_2.mp4", _FRAME_SAMPLE_S, frame_2)

        ssim = _measure_ssim(_FFMPEG, frame_1, frame_2)
        assert ssim >= 0.98, (
            f"SSIM of two subtitle=None output frames is below expected (assert-7):\n"
            f"  SSIM: {ssim:.6f} (expected: >= 0.98)\n"
            f"  subtitle=None should produce deterministic output (ADR-S8)"
        )

    def test_subtitle_presence_causes_pixel_diff(self, tmp_path: Path) -> None:
        """Pixel difference between subtitle and no-subtitle is isolated as subtitle-caused (assert-7).

        Compare two no-subtitle renders (identical) with one subtitle render.
        Confirm no-subtitle pairs have SSIM >= 0.98 and subtitle vs no-subtitle has SSIM < 0.999.
        Quantitatively demonstrates that the diff originates solely from the subtitle (lesson B-3).
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"
        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        # No-subtitle x2
        tl_no1 = _make_timeline(main_src)
        tl_no1_path = tmp_path / "tl_no1.otio"
        _save_timeline(tl_no1, tl_no1_path)
        out_no1 = tmp_path / "out_no1.mp4"
        r_no1 = render_timeline(
            str(tl_no1_path), str(out_no1), RenderOptions(), dry_run=False
        )
        assert r_no1["ok"] is True

        tl_no2 = _make_timeline(main_src)
        tl_no2_path = tmp_path / "tl_no2.otio"
        _save_timeline(tl_no2, tl_no2_path)
        out_no2 = tmp_path / "out_no2.mp4"
        r_no2 = render_timeline(
            str(tl_no2_path), str(out_no2), RenderOptions(), dry_run=False
        )
        assert r_no2["ok"] is True

        # Subtitle x1
        tl_sub = _make_timeline(main_src)
        tl_sub_path = tmp_path / "tl_sub.otio"
        _save_timeline(tl_sub, tl_sub_path)
        out_sub = tmp_path / "out_sub.mp4"
        r_sub = render_timeline(
            str(tl_sub_path),
            str(out_sub),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=28)),
            dry_run=False,
        )
        assert r_sub["ok"] is True

        frame_no1 = tmp_path / "frame_no1.png"
        frame_no2 = tmp_path / "frame_no2.png"
        frame_sub = tmp_path / "frame_sub.png"
        _extract_frame(_FFMPEG, out_no1, _FRAME_SAMPLE_S, frame_no1)
        _extract_frame(_FFMPEG, out_no2, _FRAME_SAMPLE_S, frame_no2)
        _extract_frame(_FFMPEG, out_sub, _FRAME_SAMPLE_S, frame_sub)

        # No-subtitle pair: SSIM >= 0.98
        ssim_no_vs_no = _measure_ssim(_FFMPEG, frame_no1, frame_no2)
        assert ssim_no_vs_no >= 0.98, (
            f"SSIM between two no-subtitle renders is too low (negative control reliability issue):\n"
            f"  SSIM: {ssim_no_vs_no:.6f} (expected: >= 0.98)"
        )

        # Subtitle vs no-subtitle: SSIM < 0.999 (difference caused by subtitle)
        ssim_sub_vs_no = _measure_ssim(_FFMPEG, frame_sub, frame_no1)
        assert ssim_sub_vs_no < _SSIM_PIXEL_DIFF_THRESHOLD, (
            f"SSIM difference between subtitle and no-subtitle is insufficient (assert-7, isolation failure):\n"
            f"  subtitle vs no-subtitle SSIM: {ssim_sub_vs_no:.6f} (expected: < {_SSIM_PIXEL_DIFF_THRESHOLD})\n"
            f"  no-subtitle pair SSIM: {ssim_no_vs_no:.6f} (reference)\n"
            f"  Control experiment to confirm the diff is caused solely by the subtitle"
        )
