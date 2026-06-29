"""test_karaoke_render.py — Tests for karaoke wiring in render.py / build_plan.

Dependency: s2-karaoke-plan-impl is complete (plan.py has _parse_word_vtt,
_build_karaoke_ass, etc., and SubtitleOptions has karaoke/highlight_color/chars_per_line/
max_lines).  This file covers the render.py / build_plan wiring implemented in
s2-render-wiring-impl.

Coverage areas:
  - render.py karaoke extension guard: Section 1 verifies that karaoke=True + .srt / .ass
    returns INVALID_INPUT (result["ok"] is False).
  - build_plan scratch_dir / karaoke.ass generation: Sections 3 and 5 verify that the
    filter_complex references the generated .ass file (charenc=UTF-8 absent, force_style
    absent, .ass extension present).

Regression guards (must stay Green):
  - Section 1c: karaoke=False + .srt accepted (existing path unaffected).
  - Section 2:  symlink .vtt rejected (check_media_ref already active; POSIX CI only).
  - Section 3d: subtitles=filename= node present in filter_complex.
  - Section 4:  -pix_fmt yuv420p in ffmpeg_args (always added, no regression expected).
  - Section 6:  existing SRT/VTT/ASS/None paths unchanged (AC-7 regression suite).

Coverage: F-R-04/06/07 / AC-7 / SEC-05 / CWE-22 / plan-report §s2-render-wiring-test.

Drift guards (must stay in sync with plan-report §4 / plan.py / schemas.py):
  _MAX_WORDS = 50_000
  _MAX_CUES  = 10_000
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, StreamInfo

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ---------------------------------------------------------------------------
# Drift-guard constants (plan-report §4 / architecture §5 / ADR-K8)
# ---------------------------------------------------------------------------
_MAX_WORDS: int = 50_000
_MAX_CUES: int = 10_000

# Canonical word-level VTT fixture (placed by s0-contract-fixture)
_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_CANONICAL_VTT = _FIXTURE_DIR / "word_vtt_canonical.vtt"

# Frame dimensions for probe stubs (matches test_e2e_subtitle.py convention)
_FRAME_W: int = 320
_FRAME_H: int = 240
_FPS: float = 25.0


# ---------------------------------------------------------------------------
# Helpers (mirrors test_pathpolicy_render.py / test_render.py style)
# ---------------------------------------------------------------------------


def _rt(seconds: float, rate: float = _FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = _FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(clips: list[otio.schema.Clip]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    otio.adapters.write_to_file(_make_timeline(clips), str(path))


def _make_media_info(
    path: str = "/fake/source.mp4",
    *,
    has_video: bool = True,
    audio_streams: int = 0,
    bit_rate: int | None = 8_000_000,
    width: int = _FRAME_W,
    height: int = _FRAME_H,
) -> MediaInfo:
    """Build a MediaInfo stub for inspect_media mocking.

    audio_streams=0 by default to avoid audio-mapping complexity in plan tests.
    """
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(
            StreamInfo(
                index=0,
                codec_type="video",
                codec_name="h264",
                width=width,
                height=height,
            )
        )
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


# ===========================================================================
# Section 1 — Extension guard (F-R-04 / ADR-K7)
# karaoke=True requires .vtt; any other extension → INVALID_INPUT
# ===========================================================================


class TestKaraokeExtensionGuard:
    """render.py must reject non-.vtt paths when karaoke=True (F-R-04 / ADR-K7).

    karaoke=True + .srt / .ass must return INVALID_INPUT; karaoke requires a
    word-level WebVTT input.
    """

    def test_karaoke_true_srt_returns_invalid_input(self, tmp_path: Path) -> None:
        """karaoke=True + .srt path → ok=False / INVALID_INPUT with VTT hint.

        F-R-04 / ADR-K7: karaoke burn-in requires a word-level WebVTT as input.
        The hint must guide users to supply a word-level .vtt file.
        """
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        srt = tmp_path / "sub.srt"
        srt.write_text("1\n00:00:00,500 --> 00:00:02,500\nHello\n\n", encoding="utf-8")
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt), karaoke=True)
                ),
                dry_run=True,
            )

        assert result["ok"] is False, (
            "Expected INVALID_INPUT for karaoke=True + .srt, "
            f"but render succeeded: {result}"
        )
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT, (
            f"Expected INVALID_INPUT, got {result['error']['code']}"
        )
        hint: str = result["error"]["hint"].lower()
        assert "vtt" in hint or "word" in hint, (
            f"hint must mention word-level VTT requirement, got: {result['error']['hint']!r}"
        )

    def test_karaoke_true_ass_returns_invalid_input(self, tmp_path: Path) -> None:
        """karaoke=True + .ass path → ok=False / INVALID_INPUT (F-R-04 / ADR-K7).

        ASS with karaoke=True is rejected even though .ass is in the extension WL.
        """
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        ass = tmp_path / "sub.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\n\n[Events]\n",
            encoding="utf-8",
        )
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(ass), karaoke=True)
                ),
                dry_run=True,
            )

        assert result["ok"] is False, (
            "Expected INVALID_INPUT for karaoke=True + .ass, "
            f"but render succeeded: {result}"
        )
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_karaoke_false_srt_is_accepted(self, tmp_path: Path) -> None:
        """karaoke=False (default) + .srt → ok=True, existing path unaffected (AC-7).

        Regression guard: the extension guard must be conditional on karaoke=True only.
        Green before and after implementation.
        """
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        srt = tmp_path / "sub.srt"
        srt.write_text("1\n00:00:00,500 --> 00:00:02,500\nHello\n\n", encoding="utf-8")
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt))  # karaoke defaults to False
                ),
                dry_run=True,
            )

        assert result["ok"] is True, (
            f"karaoke=False + .srt should be accepted (regression): {result}"
        )


# ===========================================================================
# Section 2 — Path validation (SEC-05 / CWE-22)
# word-VTT goes through check_media_ref; no re-implementation (POSIX CI only)
# ===========================================================================


class TestKaraokePathValidation:
    """SEC-05/CWE-22: word-VTT path is validated via check_media_ref delegation.

    Symlink rejection must apply to the karaoke .vtt path as well.
    Green before and after implementation (check_media_ref already active).
    Skipped on non-POSIX (Windows dev machine); runs in CI (3-OS matrix).
    """

    @pytest.mark.skipif(
        os.name != "posix",
        reason="symlink test runs only on POSIX (CI); skipped on Windows dev machine",
    )
    def test_karaoke_symlink_vtt_rejected(self, tmp_path: Path) -> None:
        """karaoke=True with a symlink .vtt path → PATH_NOT_ALLOWED (CWE-22).

        Verifies that check_media_ref delegation is preserved for karaoke paths
        (SEC-05 re-implementation prevention).
        """
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        real_vtt = tmp_path / "real.vtt"
        real_vtt.write_bytes(_CANONICAL_VTT.read_bytes())
        sym_vtt = tmp_path / "sym.vtt"
        try:
            sym_vtt.symlink_to(real_vtt)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation failed (may require elevated privileges)")

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(sym_vtt), karaoke=True)
                ),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED, (
            f"Expected PATH_NOT_ALLOWED for symlink .vtt (CWE-22), "
            f"got: {result['error']['code']}"
        )


# ===========================================================================
# Section 3 — build_plan karaoke wiring (F-R-04 / ADR-K4 / DC-AS-002)
# _parse_word_vtt → _build_karaoke_ass → is_ass=True branch in filter_complex
# ===========================================================================


class TestKaraokeFilterWiring:
    """build_plan karaoke wiring: .ass generated in scratch dir, is_ass=True branch.

    Verifies that build_plan writes karaoke.ass to a scratch dir and routes the
    filter_complex through the is_ass=True branch (charenc=UTF-8 absent,
    force_style absent, .ass extension present).
    """

    def _render_karaoke_dry(self, tmp_path: Path) -> dict[str, Any]:
        """Run render_timeline with karaoke=True against the canonical VTT fixture.

        Uses font_size=24 to verify that force_style is suppressed for karaoke paths
        (is_ass=True branch active; DC-AS-002 / ADR-K4).
        """
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        tl_path = tmp_path / "tl.otio"
        # canonical VTT covers 0–6.2 s; clip duration 7.0 s is safe
        _write_timeline(tl_path, [_make_clip(src, 0.0, 7.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src, width=_FRAME_W, height=_FRAME_H),
        ):
            return render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(
                        path=str(_CANONICAL_VTT),
                        karaoke=True,
                        font_size=24,
                    )
                ),
                dry_run=True,
            )

    def test_karaoke_filter_complex_has_ass_extension(self, tmp_path: Path) -> None:
        """karaoke=True: filter_complex filename references a generated .ass file.

        After implementation, karaoke.ass is written to a scratch dir and its
        path (ending in .ass) appears in the subtitles=filename= filter node.

        """
        result = self._render_karaoke_dry(tmp_path)

        assert result["ok"] is True, f"render_timeline failed unexpectedly: {result}"
        fc = result["data"]["filter_complex"]
        assert ".ass" in fc, (
            "filter_complex does not reference a .ass file — "
            "karaoke.ass build_plan wiring is missing:\n"
            f"  filter_complex: {fc}"
        )

    def test_karaoke_filter_no_charenc_utf8(self, tmp_path: Path) -> None:
        """karaoke=True: filter_complex uses is_ass=True branch → charenc=UTF-8 absent.

        is_ass=True (detected from the generated .ass extension) suppresses the
        charenc=UTF-8 option that is added for SRT/VTT paths (ADR-K4 / DC-AS-002).
        """
        result = self._render_karaoke_dry(tmp_path)

        assert result["ok"] is True, f"render_timeline failed unexpectedly: {result}"
        fc = result["data"]["filter_complex"]
        assert "charenc=UTF-8" not in fc, (
            "filter_complex contains charenc=UTF-8 for karaoke path — "
            "is_ass=True branch not used (ADR-K4 / DC-AS-002 violation):\n"
            f"  filter_complex: {fc}"
        )

    def test_karaoke_filter_no_force_style(self, tmp_path: Path) -> None:
        """karaoke=True: filter_complex uses is_ass=True branch → force_style absent.

        The generated karaoke.ass carries its own V4+ Style section, so force_style
        must not be applied (DC-AS-002 / ADR-K4). font_size=24 is set in the
        helper to confirm that is_ass=True suppresses force_style even when font_size
        is specified.
        """
        result = self._render_karaoke_dry(tmp_path)

        assert result["ok"] is True, f"render_timeline failed unexpectedly: {result}"
        fc = result["data"]["filter_complex"]
        assert "force_style=" not in fc, (
            "filter_complex contains force_style= for karaoke ASS — "
            "is_ass=True branch not applied (DC-AS-002 / ADR-K4 violation):\n"
            f"  filter_complex: {fc}"
        )

    def test_karaoke_filter_has_subtitles_node(self, tmp_path: Path) -> None:
        """karaoke=True: filter_complex still contains the subtitles= filter node.

        Regression guard — the subtitles filter node must always be present
        regardless of whether the source is a word-VTT or a generated .ass.
        Green before and after implementation.
        """
        result = self._render_karaoke_dry(tmp_path)

        assert result["ok"] is True, f"render_timeline failed unexpectedly: {result}"
        fc = result["data"]["filter_complex"]
        assert "subtitles=filename=" in fc, (
            "subtitles=filename= missing from filter_complex for karaoke path:\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# Section 4 — pix_fmt yuv420p maintained in karaoke path (F-R-06)
# ===========================================================================


class TestKaraokePIxFmt:
    """-pix_fmt yuv420p is present in ffmpeg_args even with karaoke=True (F-R-06).

    Regression guard: the pix_fmt insertion must not be disrupted by the karaoke
    branch.  Green before and after implementation.
    """

    def test_karaoke_pix_fmt_yuv420p_in_ffmpeg_args(self, tmp_path: Path) -> None:
        """-pix_fmt / yuv420p adjacent pair is in ffmpeg_args for karaoke=True (F-R-06)."""
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 7.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src, width=_FRAME_W, height=_FRAME_H),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(
                        path=str(_CANONICAL_VTT),
                        karaoke=True,
                    )
                ),
                dry_run=True,
            )

        assert result["ok"] is True, f"render_timeline failed unexpectedly: {result}"
        args: list[str] = result["data"]["ffmpeg_args"]
        assert "-pix_fmt" in args, f"-pix_fmt not found in ffmpeg_args: {args}"
        idx = args.index("-pix_fmt")
        assert args[idx + 1] == "yuv420p", (
            f"Expected yuv420p immediately after -pix_fmt, "
            f"got {args[idx + 1]!r}: {args}"
        )


# ===========================================================================
# Section 5 — Temp lifecycle: karaoke.ass lives in TemporaryDirectory, not sidecar
# ===========================================================================


class TestKaraokeTempLifecycle:
    """karaoke.ass must be generated in a TemporaryDirectory (not in the output dir).

    The two-step assertion:
      1. filter_complex must reference a .ass file (karaoke.ass was generated).
      2. The .ass path must NOT reside under the timeline/output tmp_path directory.
    """

    def test_karaoke_ass_path_not_under_output_dir(self, tmp_path: Path) -> None:
        """karaoke.ass in filter_complex must be in a TemporaryDirectory, not output dir.

        Uses dry_run=True so that:
          - The .ass is generated (inside build_plan karaoke path).
          - Its path is embedded in filter_complex and can be inspected.
          - No real ffmpeg is invoked.
        """
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 7.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src, width=_FRAME_W, height=_FRAME_H),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(
                        path=str(_CANONICAL_VTT),
                        karaoke=True,
                    )
                ),
                dry_run=True,
            )

        assert result["ok"] is True, f"render_timeline failed unexpectedly: {result}"
        fc = result["data"]["filter_complex"]

        # Assertion 1: filter_complex must reference a .ass path
        assert ".ass" in fc, (
            "filter_complex does not reference a .ass file — "
            "build_plan scratch_dir wiring is missing (temp lifecycle cannot be verified):\n"
            f"  filter_complex: {fc}"
        )

        # Assertion 2: the .ass path must NOT be under the output directory (tmp_path)
        m = re.search(r"filename='([^']+\.ass)'", fc)
        assert m is not None, f"Could not extract .ass path from filter_complex: {fc}"
        # Normalise backslash escapes that ffmpeg filtergraph may introduce
        raw_ass_path = m.group(1).replace("\\\\", "\\").replace("\\/", "/")
        ass_path = Path(raw_ass_path)
        resolved_tmp = tmp_path.resolve()
        assert not str(ass_path.resolve()).startswith(str(resolved_tmp)), (
            f"karaoke.ass ({ass_path}) is under the output directory ({tmp_path}) — "
            "TemporaryDirectory scope was not applied (sidecar contamination)"
        )


# ===========================================================================
# Section 6 — Regression (AC-7): karaoke=False leaves existing paths unchanged
# ===========================================================================


class TestKaraokeRegression:
    """AC-7: karaoke=False (default) must not alter existing SRT/VTT/ASS burn-in paths.

    All tests in this section are regression guards that must remain Green both
    before and after the karaoke implementation is merged.
    """

    def test_srt_karaoke_false_filter_has_charenc(self, tmp_path: Path) -> None:
        """karaoke=False + .srt → filter_complex retains charenc=UTF-8 (regression guard)."""
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        srt = tmp_path / "sub.srt"
        srt.write_text(
            "1\n00:00:00,500 --> 00:00:02,500\nHello World\n\n", encoding="utf-8"
        )
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(srt), font_size=24)
                    # karaoke defaults to False
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        fc = result["data"]["filter_complex"]
        assert "charenc=UTF-8" in fc, (
            "Regression: charenc=UTF-8 missing from SRT filter_complex — "
            "karaoke=False path was modified:\n"
            f"  filter_complex: {fc}"
        )

    def test_vtt_karaoke_false_filter_has_charenc(self, tmp_path: Path) -> None:
        """karaoke=False + .vtt → filter_complex retains charenc=UTF-8 (regression guard)."""
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        vtt = tmp_path / "sub.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:00.500 --> 00:00:02.500\nHello World\n\n",
            encoding="utf-8",
        )
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(vtt), font_size=24)
                    # karaoke defaults to False
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        fc = result["data"]["filter_complex"]
        assert "charenc=UTF-8" in fc, (
            "Regression: charenc=UTF-8 missing from VTT filter_complex — "
            "karaoke=False path was modified:\n"
            f"  filter_complex: {fc}"
        )

    def test_ass_karaoke_false_filter_no_charenc(self, tmp_path: Path) -> None:
        """karaoke=False + .ass → filter_complex has no charenc=UTF-8 (regression guard)."""
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        ass = tmp_path / "sub.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold,"
            " Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
            " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR,"
            " MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H00000000,0,0,0,0,100,100,"
            "0,0,1,2,0,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
            " MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.50,0:00:02.50,Default,,0,0,0,,Hello\n",
            encoding="utf-8",
        )
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(
                    subtitle=SubtitleOptions(path=str(ass))  # karaoke defaults to False
                ),
                dry_run=True,
            )

        assert result["ok"] is True
        fc = result["data"]["filter_complex"]
        assert "charenc=UTF-8" not in fc, (
            "Regression: charenc=UTF-8 present in ASS filter_complex — "
            "karaoke=False path was modified:\n"
            f"  filter_complex: {fc}"
        )

    def test_subtitle_none_no_subtitles_in_filter(self, tmp_path: Path) -> None:
        """subtitle=None: subtitles filter absent from filter_complex (ADR-S8 / AC-7)."""
        src = str(tmp_path / "clip.mp4")
        Path(src).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 3.0)])

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(tmp_path / "out.mp4"),
                options=RenderOptions(),  # subtitle=None by default
                dry_run=True,
            )

        assert result["ok"] is True
        fc = result["data"]["filter_complex"]
        assert "subtitles" not in fc, (
            "subtitle=None must not inject subtitles into filter_complex "
            "(ADR-S8 backward-compat regression):\n"
            f"  filter_complex: {fc}"
        )
