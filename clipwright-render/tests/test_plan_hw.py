"""test_plan_hw.py — Tests for plan.py HW encoder path (ADR-7).

Target:
  - _build_ffmpeg_args(..., resolved_encoder=None)  → backward-compat argv (AC-1/NFR-1)
  - _build_ffmpeg_args(..., resolved_encoder=ResolvedEncoder(...))  → HW argv (AC-3/5/8/9)
  - build_plan(..., resolved_encoder=...)  → passes resolved_encoder through (ADR-7)

Mock boundary: ResolvedEncoder is passed directly (no _resolve_hw_encoder mock needed).
"""

from __future__ import annotations

import opentimelineio as otio
import pytest

from clipwright_render.encoders import ResolvedEncoder
from clipwright_render.plan import ProbeInfo
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Helpers (mirror test_plan.py conventions)
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
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


def _simple_ranges() -> object:
    """Return a single-clip KeptRangeList for minimal test setups."""
    from clipwright_render.plan import resolve_kept_ranges

    tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
    return resolve_kept_ranges(tl)


def _simple_probe(*, has_audio: bool = False) -> ProbeInfo:
    return ProbeInfo(
        has_video=True,
        audio_count=1 if has_audio else 0,
        bit_rate=None,
    )


# ---------------------------------------------------------------------------
# AC-1 / NFR-1: resolved_encoder=None must produce identical argv to current
# ---------------------------------------------------------------------------


class TestBuildFfmpegArgsBackwardCompat:
    """resolved_encoder=None must leave the existing -c:v / -crf path untouched (AC-1)."""

    def test_video_codec_present_when_resolved_encoder_none(self) -> None:
        """With video_codec and resolved_encoder=None, -c:v video_codec must appear."""
        from clipwright_render.plan import _build_ffmpeg_args

        # Will raise TypeError until _build_ffmpeg_args accepts resolved_encoder kwarg
        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(video_codec="libx264"),
            resolved_encoder=None,
        )
        assert "-c:v" in args
        idx = args.index("-c:v")
        assert args[idx + 1] == "libx264"

    def test_crf_present_when_resolved_encoder_none(self) -> None:
        """With crf=23 and resolved_encoder=None, -crf 23 must appear."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(crf=23),
            resolved_encoder=None,
        )
        assert "-crf" in args
        idx = args.index("-crf")
        assert args[idx + 1] == "23"

    def test_video_codec_and_crf_together_resolved_none(self) -> None:
        """With video_codec + crf + resolved_encoder=None, both -c:v and -crf must appear."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(video_codec="libx264", crf=18),
            resolved_encoder=None,
        )
        assert "-c:v" in args
        assert "-crf" in args
        # -c:v value
        idx_cv = args.index("-c:v")
        assert args[idx_cv + 1] == "libx264"
        # -crf value
        idx_crf = args.index("-crf")
        assert args[idx_crf + 1] == "18"

    def test_no_video_codec_no_crf_resolved_none_no_spurious_flags(self) -> None:
        """With no codec/crf and resolved_encoder=None, neither -c:v nor -crf must appear."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(),
            resolved_encoder=None,
        )
        assert "-c:v" not in args
        assert "-crf" not in args

    def test_audio_codec_present_resolved_none(self) -> None:
        """-c:a must still appear unchanged when resolved_encoder=None (non-HW args unaffected)."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=1[outv][outa]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=True,
            options=RenderOptions(audio_codec="aac"),
            resolved_encoder=None,
        )
        assert "-c:a" in args
        idx = args.index("-c:a")
        assert args[idx + 1] == "aac"


# ---------------------------------------------------------------------------
# AC-3/5/8/9: resolved_encoder non-None → HW argv, no -crf token
# ---------------------------------------------------------------------------


class TestBuildFfmpegArgsHwPath:
    """resolved_encoder non-None must produce HW argv without -crf (AC-3/5/8/9)."""

    _NVENC_ENCODER = ResolvedEncoder(
        encoder_name="h264_nvenc",
        rate_control_flags=["-cq", "28", "-rc", "vbr"],
        hwaccel_value="cuda",
        warnings=[],
    )

    def test_hw_encoder_name_in_args(self) -> None:
        """With resolved_encoder h264_nvenc, -c:v h264_nvenc must appear (AC-8/9)."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=self._NVENC_ENCODER,
        )
        assert "-c:v" in args
        idx = args.index("-c:v")
        assert args[idx + 1] == "h264_nvenc"

    def test_no_crf_token_in_hw_path(self) -> None:
        """With HW resolved_encoder, -crf must not appear anywhere in argv (AC-3)."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(hw_encoder="nvenc", crf=23),  # crf ignored in HW path
            resolved_encoder=self._NVENC_ENCODER,
        )
        assert "-crf" not in args

    def test_rate_control_flags_expanded(self) -> None:
        """rate_control_flags from ResolvedEncoder are expanded verbatim into argv (AC-5)."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=self._NVENC_ENCODER,
        )
        # All rate_control_flags tokens must appear in the argv
        for token in ["-cq", "28", "-rc", "vbr"]:
            assert token in args

    def test_rate_control_flags_order(self) -> None:
        """rate_control_flags tokens appear in the correct order in argv."""
        from clipwright_render.plan import _build_ffmpeg_args

        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=self._NVENC_ENCODER,
        )
        # Find the position of -cq and verify order
        assert "-cq" in args
        idx_cq = args.index("-cq")
        assert args[idx_cq + 1] == "28"
        assert args[idx_cq + 2] == "-rc"
        assert args[idx_cq + 3] == "vbr"

    def test_empty_rate_control_flags_q3(self) -> None:
        """Q3: rate_control_flags=[] (quality=None) → no rate-control tokens at all."""
        from clipwright_render.plan import _build_ffmpeg_args

        encoder_no_rc = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=[],  # quality=None → empty list per Q3
            hwaccel_value="cuda",
            warnings=[],
        )
        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=encoder_no_rc,
        )
        # -c:v must still be present
        assert "-c:v" in args
        assert args[args.index("-c:v") + 1] == "h264_nvenc"
        # No rate-control tokens
        for token in ("-cq", "-rc", "-crf", "-global_quality", "-q:v"):
            assert token not in args

    def test_hevc_nvenc_encoder_name(self) -> None:
        """ResolvedEncoder with hevc_nvenc → -c:v hevc_nvenc (AC-9 family mapping)."""
        from clipwright_render.plan import _build_ffmpeg_args

        hevc_encoder = ResolvedEncoder(
            encoder_name="hevc_nvenc",
            rate_control_flags=["-cq", "25", "-rc", "vbr"],
            hwaccel_value="cuda",
            warnings=[],
        )
        args = _build_ffmpeg_args(
            filter_complex="[0:v]concat=n=1:v=1:a=0[outv]",
            video_map_label="[outv]",
            audio_map_label="[outa]",
            has_audio=False,
            options=RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=hevc_encoder,
        )
        assert "-c:v" in args
        assert args[args.index("-c:v") + 1] == "hevc_nvenc"
        assert "-crf" not in args


# ---------------------------------------------------------------------------
# build_plan pass-through: resolved_encoder is forwarded to _build_ffmpeg_args
# ---------------------------------------------------------------------------


class TestBuildPlanHwPassthrough:
    """build_plan must accept and pass resolved_encoder to _build_ffmpeg_args (ADR-7)."""

    def test_build_plan_with_resolved_encoder_none_no_crf_in_ffmpeg_args(self) -> None:
        """build_plan(..., resolved_encoder=None) with crf=23 → -crf present (backward compat)."""
        from clipwright_render.plan import build_plan

        ranges = _simple_ranges()
        probe = _simple_probe()
        # Will TypeError until build_plan accepts resolved_encoder kwarg
        plan = build_plan(ranges, probe, RenderOptions(crf=23), resolved_encoder=None)
        assert "-crf" in plan.ffmpeg_args

    def test_build_plan_with_hw_encoder_produces_hw_argv(self) -> None:
        """build_plan(..., resolved_encoder=<nvenc>) → -c:v h264_nvenc, no -crf (AC-3/8)."""
        from clipwright_render.plan import build_plan

        ranges = _simple_ranges()
        probe = _simple_probe()
        nvenc_enc = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=["-cq", "28", "-rc", "vbr"],
            hwaccel_value="cuda",
            warnings=[],
        )
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=nvenc_enc,
        )
        assert "-c:v" in plan.ffmpeg_args
        assert plan.ffmpeg_args[plan.ffmpeg_args.index("-c:v") + 1] == "h264_nvenc"
        assert "-crf" not in plan.ffmpeg_args

    def test_build_plan_hw_rate_control_flags_in_argv(self) -> None:
        """build_plan with HW encoder exposes rate_control_flags tokens in ffmpeg_args."""
        from clipwright_render.plan import build_plan

        ranges = _simple_ranges()
        probe = _simple_probe()
        nvenc_enc = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=["-cq", "28", "-rc", "vbr"],
            hwaccel_value="cuda",
            warnings=[],
        )
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=nvenc_enc,
        )
        for token in ["-cq", "28", "-rc", "vbr"]:
            assert token in plan.ffmpeg_args

    def test_build_plan_hw_empty_rate_control_flags_q3(self) -> None:
        """build_plan with rate_control_flags=[] → no rate-control tokens in ffmpeg_args (Q3)."""
        from clipwright_render.plan import build_plan

        ranges = _simple_ranges()
        probe = _simple_probe()
        enc_no_rc = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=[],
            hwaccel_value="cuda",
            warnings=[],
        )
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(hw_encoder="nvenc"),
            resolved_encoder=enc_no_rc,
        )
        assert "-c:v" in plan.ffmpeg_args
        assert plan.ffmpeg_args[plan.ffmpeg_args.index("-c:v") + 1] == "h264_nvenc"
        for token in ("-cq", "-rc", "-crf"):
            assert token not in plan.ffmpeg_args

    def test_build_plan_default_no_resolved_encoder_arg_backward_compat(self) -> None:
        """build_plan without resolved_encoder kwarg (caller omits it) → backward compat.

        When resolved_encoder is not passed, it must default to None and produce
        the same argv as before (AC-1: calling code that predates the new argument
        must not break).
        """
        from clipwright_render.plan import build_plan

        ranges = _simple_ranges()
        probe = _simple_probe()
        # No resolved_encoder argument — must work with default None
        plan = build_plan(ranges, probe, RenderOptions(video_codec="libx264", crf=18))
        assert "-c:v" in plan.ffmpeg_args
        assert plan.ffmpeg_args[plan.ffmpeg_args.index("-c:v") + 1] == "libx264"
        assert "-crf" in plan.ffmpeg_args
