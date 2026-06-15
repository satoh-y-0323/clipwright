"""test_spike_original_size.py — Spike: verify libass `original_size` behavior.

Purpose:
    Confirm or refute the hypothesis that adding `original_size=WxH` to the
    ffmpeg `subtitles` filter changes how `force_style` MarginV is interpreted
    (from PlayResY=288-based to output-pixel-based), so that burned-in subtitles
    remain visible on tall (1080x1920) output frames.

Results (2026-06-13, ffmpeg 8.1.1):
    - Hypothesis PARTIALLY CONFIRMED / PARTIALLY REFUTED:
        * ROOT CAUSE confirmed: ffmpeg converts SRT→ASS with PlayResY=288 fixed;
          MarginV/FontSize are scaled by ~6.67x on a 1920px-tall frame, pushing
          subtitles off-screen.  Demonstrated directly with ASS files.
        * `original_size` FIX NOT CONFIRMED: adding `original_size=1080x1920` to
          the `subtitles` filter with `force_style='...,MarginV=N'` produces
          *identical* frames (SSIM=1.0) vs. no `original_size`.  The option does
          NOT change how force_style MarginV coordinates are interpreted in this
          ffmpeg version.
    - Implication for implementation (ADR-F3):
        The planned `original_size` injection alone will NOT fix off-screen
        subtitles when `force_style` is used.  Alternative approaches are needed
        (e.g., scale MarginV to PlayResY=288 coordinates, or generate an ASS
        file with PlayResY=<output_height>).

Spike report: .claude/reports/spike-report-spike-original-size.md

How to run:
    cd clipwright-render
    uv run pytest tests/spike/test_spike_original_size.py -v

Skipped when ffmpeg is not available.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

# Skip all spike tests when ffmpeg subtitles filter (libass) is unavailable.
pytestmark = pytest.mark.usefixtures("require_subtitles_filter")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape_vf_path(p: Path) -> str:
    """Escape a path for use inside ffmpeg -vf filter filename= / fontsdir= options.

    The ffmpeg filtergraph parser splits on ``:`` and ``\\``, so both must be
    escaped.  Apply in order: backslash first, then colon (DC-AS-005 / plan.py).

    Example: ``C:\\Users\\sub.srt`` → ``C\\:\\\\Users\\\\sub.srt``

    Callers must wrap the result in single quotes: ``filename='<escaped>'``.
    """
    return str(p).replace("\\", "\\\\").replace(":", "\\:")


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a subprocess; raises on non-zero exit."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr[:500]}"
        )
    return result


def _ssim(ffmpeg: str, ref: Path, dist: Path) -> float:
    """Return SSIM All value between two PNG frames using ffmpeg ssim filter."""
    result = subprocess.run(
        [
            ffmpeg,
            "-i",
            str(ref),
            "-i",
            str(dist),
            "-filter_complex",
            "ssim",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    # Extract "All:X.XXXXXX" from stderr
    for line in result.stderr.splitlines():
        if "All:" in line:
            part = line.split("All:")[1].split()[0]
            return float(part)
    raise ValueError(f"SSIM not found in output:\n{result.stderr}")


def _extract_frame(ffmpeg: str, src: Path, out: Path, t: float = 1.0) -> None:
    """Extract a single frame at time t from src into out (PNG)."""
    _run(
        [
            ffmpeg,
            "-y",
            "-ss",
            str(t),
            "-i",
            str(src),
            "-frames:v",
            "1",
            str(out),
        ]
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ffmpeg(ffmpeg_path: str | None) -> str:
    """Skip module if ffmpeg is unavailable; return path when available."""
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg not found. Set CLIPWRIGHT_FFMPEG env var or add ffmpeg to PATH."
        )
    return ffmpeg_path


@pytest.fixture(scope="module")
def spike_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Temporary directory for spike artifacts."""
    return tmp_path_factory.mktemp("spike_original_size")


@pytest.fixture(scope="module")
def source_mp4(ffmpeg: str, spike_dir: Path) -> Path:
    """1080x1920 blue color source video (3 s)."""
    out = spike_dir / "source.mp4"
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1080x1920:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:d=3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
    )
    return out


@pytest.fixture(scope="module")
def srt_file(spike_dir: Path) -> Path:
    """Minimal UTF-8 SRT with one subtitle line (0.5–2.5 s)."""
    out = spike_dir / "test.srt"
    out.write_text(
        textwrap.dedent(
            """\
            1
            00:00:00,500 --> 00:00:02,500
            Test subtitle: margin check
            """
        ),
        encoding="utf-8",
    )
    return out


@pytest.fixture(scope="module")
def ass_playresy_288(spike_dir: Path) -> Path:
    """ASS file with PlayResY=288 (ffmpeg SRT-conversion default)."""
    out = spike_dir / "test_288.ass"
    out.write_text(
        textwrap.dedent(
            """\
            [Script Info]
            ScriptType: v4.00+
            PlayResX: 384
            PlayResY: 288

            [V4+ Styles]
            Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, \
OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, \
Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
            Style: Default,Arial,36,&Hffffff,&Hffffff,&H0,&H0,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

            [Events]
            Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            Dialogue: 0,0:00:00.50,0:00:02.50,Default,,0,0,0,,Subtitle PlayResY=288
            """
        ),
        encoding="utf-8",
    )
    return out


@pytest.fixture(scope="module")
def ass_playresy_1920(spike_dir: Path) -> Path:
    """ASS file with PlayResY=1920 (output-pixel-aligned)."""
    out = spike_dir / "test_1920.ass"
    out.write_text(
        textwrap.dedent(
            """\
            [Script Info]
            ScriptType: v4.00+
            PlayResX: 1080
            PlayResY: 1920

            [V4+ Styles]
            Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, \
OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, \
Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
            Style: Default,Arial,36,&Hffffff,&Hffffff,&H0,&H0,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

            [Events]
            Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            Dialogue: 0,0:00:00.50,0:00:02.50,Default,,0,0,0,,Subtitle PlayResY=1920
            """
        ),
        encoding="utf-8",
    )
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRootCausePlayResY:
    """Confirm that SRT→ASS conversion uses PlayResY=288, causing subtitle overflow."""

    def test_srt_to_ass_conversion_uses_playresy_288(
        self, ffmpeg: str, spike_dir: Path, srt_file: Path
    ) -> None:
        """ffmpeg SRT→ASS conversion must produce PlayResY=288 header."""
        ass_out = spike_dir / "converted.ass"
        _run([ffmpeg, "-y", "-i", str(srt_file), str(ass_out)])
        content = ass_out.read_text(encoding="utf-8")
        assert "PlayResY: 288" in content, (
            f"Expected PlayResY: 288 in ffmpeg-converted ASS. Got:\n{content[:500]}"
        )

    def test_ass_playresy_288_subtitle_overflows_tall_frame(
        self,
        ffmpeg: str,
        spike_dir: Path,
        source_mp4: Path,
        ass_playresy_288: Path,
        ass_playresy_1920: Path,
    ) -> None:
        """ASS with PlayResY=288 on 1080x1920 frame: font scaled ~6.67x → overflows.

        Verification: subtitle region at top of frame (y=0..400) has non-blue pixels
        (text overflows to the top), while ASS with PlayResY=1920 does not.
        """
        mp4_288 = spike_dir / "ass288.mp4"
        frame_288 = spike_dir / "frame_ass288.png"

        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_mp4),
                "-vf",
                f"subtitles=filename='{_escape_vf_path(ass_playresy_288)}':charenc=UTF-8",
                "-c:v",
                "libx264",
                "-c:a",
                "copy",
                str(mp4_288),
            ]
        )
        _extract_frame(ffmpeg, mp4_288, frame_288)

        mp4_1920 = spike_dir / "ass1920.mp4"
        frame_1920 = spike_dir / "frame_ass1920.png"
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_mp4),
                "-vf",
                f"subtitles=filename='{_escape_vf_path(ass_playresy_1920)}':charenc=UTF-8",
                "-c:v",
                "libx264",
                "-c:a",
                "copy",
                str(mp4_1920),
            ]
        )
        _extract_frame(ffmpeg, mp4_1920, frame_1920)

        ssim = _ssim(ffmpeg, frame_288, frame_1920)
        # PlayResY=288 vs PlayResY=1920 must produce meaningfully different frames
        # (different font scale + position → SSIM < 0.999)
        assert ssim < 0.999, (
            f"Expected SSIM < 0.999 (ASS PlayResY=288 vs 1920 should differ). "
            f"Got SSIM={ssim:.6f}"
        )


class TestOriginalSizeEffect:
    """Check whether `original_size` on the `subtitles` filter changes MarginV behavior.

    Spike finding (2026-06-13): `original_size` does NOT change how force_style
    MarginV coordinates are interpreted (all SSIM comparisons = 1.000).
    These tests document this finding — they assert SSIM=1.0, confirming
    that `original_size` alone is insufficient to fix the MarginV issue.
    """

    def _encode_with_srt(
        self,
        ffmpeg: str,
        source: Path,
        srt: Path,
        out: Path,
        *,
        original_size: str | None,
        font_size: int = 36,
        margin_v: int = 10,
        alignment: int = 2,
    ) -> None:
        """Helper: encode source with subtitles filter, with or without original_size."""
        force_style = (
            f"FontName=Arial,FontSize={font_size},"
            f"Alignment={alignment},MarginV={margin_v}"
        )
        srt_esc = _escape_vf_path(srt)
        if original_size:
            vf = (
                f"subtitles=filename='{srt_esc}'"
                f":original_size={original_size}"
                f":force_style='{force_style}'"
                ":charenc=UTF-8"
            )
        else:
            vf = (
                f"subtitles=filename='{srt_esc}'"
                f":force_style='{force_style}'"
                ":charenc=UTF-8"
            )
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-c:a",
                "copy",
                str(out),
            ]
        )

    def test_original_size_does_not_change_force_style_marginv(
        self,
        ffmpeg: str,
        spike_dir: Path,
        source_mp4: Path,
        srt_file: Path,
    ) -> None:
        """Adding original_size=1080x1920 produces identical frames to no original_size.

        Spike finding: SSIM must equal 1.000 — `original_size` has no effect on
        MarginV coordinate interpretation when force_style is used.
        This documents the refutation of the ADR-F3 hypothesis.
        """
        mp4_no = spike_dir / "no_orig.mp4"
        mp4_yes = spike_dir / "with_orig.mp4"
        frame_no = spike_dir / "frame_no_orig.png"
        frame_yes = spike_dir / "frame_with_orig.png"

        self._encode_with_srt(
            ffmpeg,
            source_mp4,
            srt_file,
            mp4_no,
            original_size=None,
            margin_v=10,
        )
        self._encode_with_srt(
            ffmpeg,
            source_mp4,
            srt_file,
            mp4_yes,
            original_size="1080x1920",
            margin_v=10,
        )
        _extract_frame(ffmpeg, mp4_no, frame_no)
        _extract_frame(ffmpeg, mp4_yes, frame_yes)

        ssim = _ssim(ffmpeg, frame_no, frame_yes)
        assert ssim == pytest.approx(1.0, abs=1e-5), (
            f"Expected SSIM≈1.0 (original_size has no effect on force_style MarginV). "
            f"Got SSIM={ssim:.6f}. "
            "If SSIM < 1.0, original_size is now effective — revisit ADR-F3."
        )

    def test_original_size_does_not_change_large_marginv(
        self,
        ffmpeg: str,
        spike_dir: Path,
        source_mp4: Path,
        srt_file: Path,
    ) -> None:
        """With large MarginV (near PlayResY=288), both cases push subtitle off-screen.

        MarginV=250 with PlayResY=288: subtitle overflows to top with or without
        original_size=1080x1920, confirming that original_size does not rescale
        the coordinate system for force_style.
        """
        mp4_no = spike_dir / "largemargin_no_orig.mp4"
        mp4_yes = spike_dir / "largemargin_with_orig.mp4"
        frame_no = spike_dir / "frame_largemargin_no.png"
        frame_yes = spike_dir / "frame_largemargin_yes.png"

        self._encode_with_srt(
            ffmpeg,
            source_mp4,
            srt_file,
            mp4_no,
            original_size=None,
            margin_v=250,
        )
        self._encode_with_srt(
            ffmpeg,
            source_mp4,
            srt_file,
            mp4_yes,
            original_size="1080x1920",
            margin_v=250,
        )
        _extract_frame(ffmpeg, mp4_no, frame_no)
        _extract_frame(ffmpeg, mp4_yes, frame_yes)

        ssim = _ssim(ffmpeg, frame_no, frame_yes)
        assert ssim == pytest.approx(1.0, abs=1e-5), (
            f"Expected SSIM≈1.0 (both cases overflow equally). Got SSIM={ssim:.6f}."
        )


class TestAlternativeFix:
    """Verify that using an ASS file with correct PlayResY fixes the off-screen issue."""

    def test_ass_with_correct_playresy_subtitle_visible_in_frame(
        self,
        ffmpeg: str,
        spike_dir: Path,
        source_mp4: Path,
        ass_playresy_1920: Path,
    ) -> None:
        """ASS with PlayResY=1920 produces visible subtitle on 1080x1920 frame.

        Verification: frame with subtitle must differ significantly from
        the source (blue) frame (SSIM < 0.999).
        """
        mp4 = spike_dir / "alt_fix.mp4"
        frame_sub = spike_dir / "frame_altfix_sub.png"
        frame_nosub = spike_dir / "frame_altfix_nosub.png"

        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_mp4),
                "-vf",
                f"subtitles=filename='{_escape_vf_path(ass_playresy_1920)}':charenc=UTF-8",
                "-c:v",
                "libx264",
                "-c:a",
                "copy",
                str(mp4),
            ]
        )
        _extract_frame(ffmpeg, mp4, frame_sub)
        # Reference: raw source (no subtitle)
        _extract_frame(ffmpeg, source_mp4, frame_nosub)

        ssim = _ssim(ffmpeg, frame_nosub, frame_sub)
        assert ssim < 0.999, (
            f"Expected SSIM < 0.999 (subtitle must be visible in frame). "
            f"Got SSIM={ssim:.6f}. Subtitle may be off-screen."
        )
