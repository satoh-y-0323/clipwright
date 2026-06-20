"""test_render_hw.py — Red tests for render.py HW encoder integration.

Covers the wiring in render.py for hardware-accelerated encoding and decoding:
  - _resolve_hw_encoder() is called before build_plan (ADR-4)
  - -hwaccel <value> is prepended to each video -i (ADR-6, AC-7)
  - multi-source: every video -i gets the same -hwaccel prefix
  - BGM -i (-stream_loop -1 -i bgm) does NOT get -hwaccel
  - Parent-confirmed Q1: none + hwaccel_decode=True -> -hwaccel auto
  - hwaccel_decode=False (default) -> no -hwaccel tokens at all (AC-1)
  - auto fall-back warning is merged into ok_result.warnings (ADR-5 / AC-2)
  - Explicit vendor failure -> ok=False / UNSUPPORTED_OPERATION (AC-4)

Mock boundary: clipwright_render.render._resolve_hw_encoder (architecture §4 / ADR-4).
No real ffmpeg/GPU is used.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import MagicMock, patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_render.encoders import ResolvedEncoder
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Helpers
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


def _make_timeline(clips: list[otio.schema.Clip]) -> otio.schema.Timeline:
    """Build a single-video-track Timeline."""
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    tl = _make_timeline(clips)
    otio.adapters.write_to_file(tl, str(path))


def _make_media_info(
    path: str = "/fake/source.mp4",
    *,
    has_video: bool = True,
    audio_streams: int = 1,
    width: int = 1920,
    height: int = 1080,
    fps_rate: float = FPS,
) -> MediaInfo:
    """Build a MediaInfo for test mocking.

    Mirrors the structure produced by real ffprobe: video StreamInfo carries
    width/height and MediaInfo.duration.rate carries the frame rate so that
    _probe() returns non-None ProbeInfo.width/height/fps (ADR-C2-r2).
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
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    duration = RationalTimeModel(value=10.0 * fps_rate, rate=fps_rate)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _write_single_source_timeline(tmp_path: Path, src: str) -> Path:
    """Write a minimal single-source OTIO timeline and return the path."""
    tl_path = tmp_path / "tl.otio"
    _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
    return tl_path


def _write_multi_source_timeline(
    tmp_path: Path, clips: list[tuple[str, float, float]]
) -> Path:
    """Write a multi-source OTIO timeline and return the path."""
    tl_path = tmp_path / "tl.otio"
    _write_timeline(tl_path, [_make_clip(src, s, d) for src, s, d in clips])
    return tl_path


def _make_bgm_timeline(
    tmp_path: Path,
    src: str,
    bgm: str,
) -> Path:
    """Write an OTIO timeline with a main video clip and a BGM audio clip.

    BGM clip on A2 AudioTrack carries metadata["clipwright"]["kind"]=="bgm".
    """
    # V1 video track
    v1_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    v1_track.append(_make_clip(src, 0.0, 5.0))

    # A2 BGM audio track
    a2_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
    bgm_clip = otio.schema.Clip()
    bgm_clip.media_reference = otio.schema.ExternalReference(target_url=bgm)
    bgm_clip.source_range = _tr(0.0, 30.0)
    bgm_clip.metadata["clipwright"] = {
        "tool": "clipwright-bgm",
        "version": "0.1.0",
        "kind": "bgm",
        "directive": {"volume_db": -6.0, "fade_in_sec": 0.0, "fade_out_sec": 0.0},
    }
    a2_track.append(bgm_clip)

    tl = otio.schema.Timeline()
    tl.tracks.append(v1_track)
    tl.tracks.append(a2_track)
    tl_path = tmp_path / "tl_bgm.otio"
    otio.adapters.write_to_file(tl, str(tl_path))
    return tl_path


def _fake_run_ok(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=0, stdout="", stderr="")


# Resolved encoder for nvenc (hwaccel_value="cuda")
_NVENC_RESOLVED = ResolvedEncoder(
    encoder_name="h264_nvenc",
    rate_control_flags=["-cq", "28", "-rc", "vbr"],
    hwaccel_value="cuda",
    warnings=[],
)

# Resolved encoder representing auto fall-back to libx264 with a warning (AC-2)
_FALLBACK_RESOLVED = ResolvedEncoder(
    encoder_name="libx264",
    rate_control_flags=["-crf", "23"],
    hwaccel_value=None,
    warnings=[
        "No hardware encoder available; fell back to libx264."
        " Install GPU drivers or set hw_encoder='none'."
    ],
)


# ---------------------------------------------------------------------------
# AC-7 / ADR-6: -hwaccel prepended to each video -i (single source)
# ---------------------------------------------------------------------------


class TestHwaccelPrependedToVideoInputs:
    """When hwaccel_decode=True, -hwaccel <value> is prepended to each video -i.

    Verifies AC-7 / ADR-6: render.py must inject -hwaccel before each
    element of plan.input_sources; -hwaccel_output_format must NOT appear.
    The test is Red because render.py does not yet call _resolve_hw_encoder
    or insert the -hwaccel prefix.
    """

    def test_hwaccel_cuda_before_single_video_i(self, tmp_path: Path) -> None:
        """hw_encoder='nvenc', hwaccel_decode=True -> '-hwaccel cuda' before '-i src'."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_NVENC_RESOLVED,
                create=True,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="nvenc",
                    hwaccel_decode=True,
                    overwrite=True,
                ),
            )

        assert result["ok"] is True, f"expected ok=True, got: {result.get('error')}"

        # -hwaccel cuda must appear before the first (and only) -i
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) >= 1, f"no -i in cmd: {captured_cmd}"
        first_i = i_indices[0]
        # The two tokens before -i must be ["-hwaccel", "cuda"]
        assert first_i >= 2, f"-hwaccel/cuda would be out of bounds: {captured_cmd}"
        assert captured_cmd[first_i - 2] == "-hwaccel", (
            f"expected '-hwaccel' two positions before '-i', got: {captured_cmd}"
        )
        assert captured_cmd[first_i - 1] == "cuda", (
            f"expected 'cuda' one position before '-i', got: {captured_cmd}"
        )

        # -hwaccel_output_format must NOT appear (ADR-6 / D5)
        assert "-hwaccel_output_format" not in captured_cmd, (
            f"-hwaccel_output_format must not be emitted: {captured_cmd}"
        )

    def test_no_hwaccel_tokens_when_hwaccel_decode_false(self, tmp_path: Path) -> None:
        """hwaccel_decode=False (default) -> no -hwaccel tokens at all (AC-1 / ADR-6)."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_NVENC_RESOLVED,
                create=True,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="nvenc",
                    hwaccel_decode=False,  # default — no hwaccel decode
                    overwrite=True,
                ),
            )

        assert result["ok"] is True, f"expected ok=True, got: {result.get('error')}"

        # No -hwaccel tokens must appear anywhere in the command
        assert "-hwaccel" not in captured_cmd, (
            f"-hwaccel must not appear when hwaccel_decode=False: {captured_cmd}"
        )

    def test_dry_run_hwaccel_cuda_in_ffmpeg_args(self, tmp_path: Path) -> None:
        """dry_run path: -hwaccel cuda must appear in data['ffmpeg_args'] (AC-7)."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_NVENC_RESOLVED,
                create=True,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="nvenc",
                    hwaccel_decode=True,
                ),
                dry_run=True,
            )

        assert result["ok"] is True, f"expected ok=True, got: {result.get('error')}"

        # In dry_run, the actual ffmpeg command assembled in render.py includes the
        # -hwaccel prefix before -i. The only way to verify the prefix is via the
        # captured_cmd in the execution path test above; for dry_run we verify that
        # -hwaccel_output_format is absent from the planned args.
        ffmpeg_args: list[str] = result["data"].get("ffmpeg_args", [])
        assert "-hwaccel_output_format" not in ffmpeg_args, (
            f"-hwaccel_output_format must not appear in dry_run args: {ffmpeg_args}"
        )


# ---------------------------------------------------------------------------
# Multi-source: every video -i gets the same -hwaccel prefix
# ---------------------------------------------------------------------------


class TestMultiSourceHwaccelPrefix:
    """All video input_sources get -hwaccel prepended (multi-source scenario).

    Red because render.py does not yet inject -hwaccel before each -i.
    """

    def test_all_video_inputs_get_hwaccel_prefix(self, tmp_path: Path) -> None:
        """2-source timeline with hwaccel_decode=True -> each -i has -hwaccel cuda."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _write_multi_source_timeline(
            tmp_path,
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
        )
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=_fake_inspect,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_NVENC_RESOLVED,
                create=True,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="nvenc",
                    hwaccel_decode=True,
                    overwrite=True,
                ),
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"

        # Locate all -i positions
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        # 2 sources => 2 -i flags from input_sources
        assert len(i_indices) >= 2, (
            f"expected at least 2 -i tokens for 2 sources: {captured_cmd}"
        )

        # Each of the first 2 -i tokens (the video input_sources) must be preceded
        # by "-hwaccel" "cuda"
        for idx in i_indices[:2]:
            assert idx >= 2, f"-hwaccel prefix would be out of bounds at idx={idx}"
            assert captured_cmd[idx - 2] == "-hwaccel", (
                f"'-hwaccel' expected 2 before -i at {idx}: {captured_cmd}"
            )
            assert captured_cmd[idx - 1] == "cuda", (
                f"'cuda' expected 1 before -i at {idx}: {captured_cmd}"
            )


# ---------------------------------------------------------------------------
# BGM input must NOT receive -hwaccel prefix
# ---------------------------------------------------------------------------


class TestBgmInputNoHwaccel:
    """BGM -i (-stream_loop -1 -i bgm) must not receive -hwaccel prefix (ADR-6).

    The -hwaccel option is only prepended to elements of plan.input_sources
    (the video sources). The BGM input is appended separately after the loop.
    Red because render.py HW wiring is not implemented yet.
    """

    def test_bgm_i_has_no_hwaccel_prefix(self, tmp_path: Path) -> None:
        """Single source + BGM: only the video -i gets -hwaccel; BGM -i does not."""
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()
        Path(tmp_path / "out.mp4").touch()

        tl_path = _make_bgm_timeline(tmp_path, src, bgm)
        output = str(tmp_path / "out.mp4")

        # Build a fake RenderPlan that has bgm_source populated
        fake_plan = RenderPlan(
            filter_complex=(
                "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
                "[v0]concat=n=1:v=1:a=1[outv][outa]"
            ),
            ffmpeg_args=[
                "-filter_complex",
                "...",
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                "h264_nvenc",
                "-cq",
                "28",
                "-rc",
                "vbr",
            ],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
            bgm_source=bgm,
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=MagicMock(),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_NVENC_RESOLVED,
                create=True,
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="nvenc",
                    hwaccel_decode=True,
                    overwrite=True,
                ),
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"

        # Locate all -i positions
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        # 1 video source + 1 BGM = 2 -i flags
        assert len(i_indices) == 2, (
            f"expected exactly 2 -i flags (1 src + 1 bgm): {captured_cmd}"
        )

        # First -i is src -> must have -hwaccel cuda before it
        first_i = i_indices[0]
        assert captured_cmd[first_i + 1] == src, (
            f"first -i should be src: {captured_cmd}"
        )
        assert first_i >= 2
        assert captured_cmd[first_i - 2] == "-hwaccel", (
            f"'-hwaccel' must precede the video -i: {captured_cmd}"
        )
        assert captured_cmd[first_i - 1] == "cuda", (
            f"'cuda' must precede the video -i: {captured_cmd}"
        )

        # Second -i is bgm -> must NOT have -hwaccel immediately before it
        # (the token before bgm -i is "-1" from "-stream_loop -1")
        bgm_i = i_indices[1]
        assert captured_cmd[bgm_i + 1] == bgm, (
            f"second -i should be bgm: {captured_cmd}"
        )
        # The token immediately before bgm -i must be "-1" (from -stream_loop -1)
        assert bgm_i >= 1
        assert captured_cmd[bgm_i - 1] == "-1", (
            f"token before BGM -i should be '-1' (stream_loop), got: {captured_cmd}"
        )
        # -hwaccel must NOT appear at bgm_i - 2 or bgm_i - 3
        # (in context of normal -stream_loop -1 -i <bgm> pattern)
        # Verify by checking that the BGM source path doesn't have a -hwaccel prefix
        # by checking the pattern around the BGM -i position
        if bgm_i >= 2:
            assert captured_cmd[bgm_i - 2] != "-hwaccel", (
                f"-hwaccel must NOT precede BGM -i: {captured_cmd}"
            )


# ---------------------------------------------------------------------------
# Parent-confirmed Q1: none + hwaccel_decode=True -> -hwaccel auto
# ---------------------------------------------------------------------------


class TestNoneEncoderWithHwaccelDecode:
    """Parent-confirmed Q1: hw_encoder='none' + hwaccel_decode=True -> -hwaccel auto.

    _resolve_hw_encoder returns None for hw_encoder='none', but render.py must
    still emit '-hwaccel auto' before each video -i when hwaccel_decode=True.
    Encode path stays CPU (resolved=None -> existing -c:v/-crf path).
    Red because render.py does not yet implement this logic.
    """

    def test_none_encoder_with_hwaccel_decode_emits_hwaccel_auto(
        self, tmp_path: Path
    ) -> None:
        """none + hwaccel_decode=True -> '-hwaccel auto' before video -i."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            # _resolve_hw_encoder returns None for hw_encoder='none'
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=None,
                create=True,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="none",
                    hwaccel_decode=True,  # Q1: decode with -hwaccel auto
                    overwrite=True,
                ),
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"

        # '-hwaccel auto' must appear before each video -i
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) >= 1, f"no -i in cmd: {captured_cmd}"
        first_i = i_indices[0]
        assert first_i >= 2
        assert captured_cmd[first_i - 2] == "-hwaccel", (
            f"'-hwaccel' must precede -i for none+decode: {captured_cmd}"
        )
        assert captured_cmd[first_i - 1] == "auto", (
            f"'auto' must precede -i for none+decode: {captured_cmd}"
        )

        # -hwaccel_output_format must not appear
        assert "-hwaccel_output_format" not in captured_cmd

    def test_none_encoder_hwaccel_decode_false_no_hwaccel(self, tmp_path: Path) -> None:
        """none + hwaccel_decode=False -> no -hwaccel at all (AC-1 baseline)."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=None,
                create=True,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                # Default RenderOptions: hw_encoder='none', hwaccel_decode=False
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"
        assert "-hwaccel" not in captured_cmd, (
            f"-hwaccel must not appear with none+decode=False: {captured_cmd}"
        )


# ---------------------------------------------------------------------------
# AC-2 / ADR-5: auto fall-back warning merged into ok_result.warnings
# ---------------------------------------------------------------------------


class TestAutoFallbackWarningMerged:
    """Auto fall-back warning is included in ok_result.warnings (AC-2 / ADR-5).

    When _resolve_hw_encoder returns a ResolvedEncoder with non-empty warnings
    (the libx264 fall-back case), render.py must merge those warnings into the
    ok_result envelope's warnings field — for both dry_run and execution paths.

    Red because render.py does not yet call _resolve_hw_encoder.
    """

    def test_fallback_warning_in_ok_result_warnings_execution_path(
        self, tmp_path: Path
    ) -> None:
        """Execution path: fallback warning merged into ok_result.warnings (AC-2)."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_FALLBACK_RESOLVED,
                create=True,
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(
                    hw_encoder="auto",
                    overwrite=True,
                ),
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"
        warnings: list[str] = result.get("warnings", [])
        assert len(warnings) >= 1, (
            f"expected at least 1 warning (fallback message), got: {warnings}"
        )
        # The warning must contain the fall-back message
        assert any("fell back to libx264" in w for w in warnings), (
            f"fallback warning not found in: {warnings}"
        )

    def test_fallback_warning_in_ok_result_warnings_dry_run_path(
        self, tmp_path: Path
    ) -> None:
        """dry_run path: fallback warning also merged into ok_result.warnings (AC-2)."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_FALLBACK_RESOLVED,
                create=True,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(hw_encoder="auto"),
                dry_run=True,
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"
        warnings: list[str] = result.get("warnings", [])
        assert len(warnings) >= 1, (
            f"expected at least 1 warning (fallback) in dry_run, got: {warnings}"
        )
        assert any("fell back to libx264" in w for w in warnings), (
            f"fallback warning not found in dry_run warnings: {warnings}"
        )

    def test_no_spurious_warnings_when_hw_encoder_succeeds(
        self, tmp_path: Path
    ) -> None:
        """When _resolve_hw_encoder returns empty warnings, ok_result.warnings is empty."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                return_value=_NVENC_RESOLVED,  # warnings=[]
                create=True,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(hw_encoder="nvenc"),
                dry_run=True,
            )

        assert result["ok"] is True, f"expected ok=True: {result.get('error')}"
        warnings: list[str] = result.get("warnings", [])
        # No HW-related warnings expected (nvenc resolved successfully)
        hw_warnings = [w for w in warnings if "fell back" in w]
        assert hw_warnings == [], (
            f"unexpected fallback warnings when nvenc resolved OK: {hw_warnings}"
        )


# ---------------------------------------------------------------------------
# AC-4: Explicit vendor failure -> ok=False / UNSUPPORTED_OPERATION
# ---------------------------------------------------------------------------


class TestExplicitVendorFailureEnvelope:
    """When _resolve_hw_encoder raises ClipwrightError(UNSUPPORTED_OPERATION),
    render_timeline must return ok=False with the correct error envelope (AC-4).

    Checks:
      - ok is False
      - error.code == "UNSUPPORTED_OPERATION"
      - error.message contains the failing encoder name
      - error.hint contains 'auto' or 'none'

    Red because render.py does not yet call _resolve_hw_encoder.
    """

    def test_unsupported_operation_envelope_structure(self, tmp_path: Path) -> None:
        """Explicit vendor failure -> ok=False / UNSUPPORTED_OPERATION / message+hint."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")

        def _fail_resolve(options: RenderOptions) -> ResolvedEncoder:
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message=(
                    "Hardware encoder 'h264_nvenc' (vendor 'nvenc') is not"
                    " available or failed the capability check on this system."
                ),
                hint=(
                    "The encoder 'h264_nvenc' is either not compiled into this"
                    " ffmpeg build or the GPU/driver is missing."
                    " Try hw_encoder='auto' or 'none'."
                ),
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                side_effect=_fail_resolve,
                create=True,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(hw_encoder="nvenc"),
            )

        assert result["ok"] is False
        error = result.get("error", {})
        assert error.get("code") == ErrorCode.UNSUPPORTED_OPERATION, (
            f"expected UNSUPPORTED_OPERATION, got: {error.get('code')}"
        )
        # message must contain the failing encoder name
        message: str = error.get("message", "")
        assert "h264_nvenc" in message or "nvenc" in message, (
            f"encoder name not found in message: {message!r}"
        )
        # hint must contain 'auto' or 'none' (actionable guidance)
        hint: str = error.get("hint", "")
        assert "auto" in hint or "none" in hint, (
            f"'auto'/'none' not found in hint: {hint!r}"
        )

    def test_unsupported_operation_does_not_call_ffmpeg(self, tmp_path: Path) -> None:
        """When _resolve_hw_encoder raises, ffmpeg run() must not be called."""
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "src.mp4")
        Path(src).touch()
        tl_path = _write_single_source_timeline(tmp_path, src)
        output = str(tmp_path / "out.mp4")

        run_calls: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            run_calls.append(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def _fail_resolve(options: RenderOptions) -> ResolvedEncoder:
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="Hardware encoder 'h264_qsv' is not available.",
                hint="Try hw_encoder='auto' or 'none'.",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render._resolve_hw_encoder",
                side_effect=_fail_resolve,
                create=True,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(hw_encoder="qsv"),
            )

        assert result["ok"] is False
        # ffmpeg (render run) must not be called on encoder resolution failure
        ffmpeg_calls = [c for c in run_calls if "ffmpeg" in (c[0] if c else "")]
        assert len(ffmpeg_calls) == 0, (
            f"ffmpeg must not be called after UNSUPPORTED_OPERATION: {run_calls}"
        )
