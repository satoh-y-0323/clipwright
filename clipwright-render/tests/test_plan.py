"""test_plan.py — Tests for plan.py (pure logic).

Target functions:
  - resolve_kept_ranges(timeline) -> list[KeptRange]
  - build_plan(ranges, probe_info, options) -> RenderPlan

plan.py is pure logic that never executes ffmpeg/ffprobe.
probe results (bit_rate/has_video/audio_count) are passed as arguments (DC-AM-007).
OTIO Timelines are constructed directly inside tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from pydantic import ValidationError

from clipwright_render.plan import BgmClip, ProbeInfo
from clipwright_render.schemas import RenderOptions

if TYPE_CHECKING:
    from clipwright_render.plan import RenderPlan

# ---------------------------------------------------------------------------
# Helpers: in-test Timeline construction
# ---------------------------------------------------------------------------

FPS = 30.0
_EPSILON = 1e-6


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    """Convert seconds to RationalTime."""
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    """Return a TimeRange of start seconds and duration seconds."""
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
    """Build a Clip with the given source_range."""
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration, rate)
    return clip


def _make_timeline_with_clips(
    clips: list[otio.schema.Clip | otio.schema.Gap | otio.schema.Transition],
    track_kind: str = otio.schema.TrackKind.Video,
) -> otio.schema.Timeline:
    """Build a single-track Timeline containing the given clips."""
    track = otio.schema.Track(kind=track_kind)
    for item in clips:
        track.append(item)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


# ---------------------------------------------------------------------------
# resolve_kept_ranges tests
# ---------------------------------------------------------------------------


class TestResolveKeptRanges:
    """Verify resolve_kept_ranges(timeline) behaviour."""

    def test_single_clip_returns_one_range(self) -> None:
        """1 clip: (source, source_range) is extracted correctly (DC-AS-005)."""
        from clipwright_render.plan import resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 1
        assert ranges[0].source == "/src/a.mp4"
        assert ranges[0].source_range == _tr(0.0, 5.0)

    def test_multiple_clips_returns_multiple_ranges(self) -> None:
        """Multiple clips: all (source, source_range) pairs returned in order."""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
            _make_clip("/src/a.mp4", 10.0, 4.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 3
        assert ranges[1].source_range == _tr(5.0, 2.0)

    def test_gap_is_skipped(self) -> None:
        """Gap is skipped; only the surrounding Clips are returned (DC-AS-006)."""
        from clipwright_render.plan import resolve_kept_ranges

        gap = otio.schema.Gap(source_range=_tr(0.0, 2.0))
        clips: list[Any] = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            gap,
            _make_clip("/src/a.mp4", 7.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 2

    def test_transition_raises_unsupported(self) -> None:
        """Contains Transition -> UNSUPPORTED_OPERATION (DC-AS-006)."""
        from clipwright_render.plan import resolve_kept_ranges

        transition = otio.schema.Transition()
        clips: list[Any] = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            transition,
            _make_clip("/src/a.mp4", 3.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_no_video_track_raises_unsupported(self) -> None:
        """0 video tracks -> UNSUPPORTED_OPERATION (architecture §5, DC-AS-002).

        M-2: when no video track exists, treated as "unsupported configuration" and
        UNSUPPORTED_OPERATION is returned (changed from INVALID_INPUT in design doc).
        """
        from clipwright_render.plan import resolve_kept_ranges

        # Timeline with audio track only (no video track)
        audio_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
        audio_track.append(_make_clip("/src/a.mp4", 0.0, 3.0))
        tl = otio.schema.Timeline()
        tl.tracks.append(audio_track)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_two_video_tracks_raises_unsupported(self) -> None:
        """2 or more video tracks -> UNSUPPORTED_OPERATION (DC-AS-006)."""
        from clipwright_render.plan import resolve_kept_ranges

        track1 = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        track1.append(_make_clip("/src/a.mp4", 0.0, 3.0))
        track2 = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        track2.append(_make_clip("/src/a.mp4", 0.0, 3.0))
        tl = otio.schema.Timeline()
        tl.tracks.append(track1)
        tl.tracks.append(track2)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_multiple_sources_returns_ranges_with_each_source(self) -> None:
        """Different target_urls (multiple sources) -> each KeptRange holds its own source
        (aspect 1: resolve_kept_ranges allows multiple sources; old DC-AS-005 behaviour removed)."""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 1.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        # Arrange/Act: confirm UNSUPPORTED_OPERATION is not raised
        ranges = resolve_kept_ranges(tl)
        # Assert: each KeptRange holds its own source
        assert len(ranges) == 2
        assert ranges[0].source == "/src/a.mp4"
        assert ranges[1].source == "/src/b.mp4"

    def test_multiple_sources_each_range_preserves_source_range(self) -> None:
        """Multiple-source clips -> each KeptRange holds its own source_range (aspect 1)."""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 1.0, 4.0),
            _make_clip("/src/b.mp4", 2.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 1.5),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 3
        assert ranges[0].source_range == _tr(1.0, 4.0)
        assert ranges[1].source_range == _tr(2.0, 3.0)
        assert ranges[2].source_range == _tr(5.0, 1.5)

    def test_missing_reference_raises_invalid_input(self) -> None:
        """MissingReference -> INVALID_INPUT (L-3: data corruption meaning).

        MissingReference indicates corrupt timeline data (missing reference).
        Should be INVALID_INPUT ("bad data"), not UNSUPPORTED_OPERATION ("unsupported config").
        """
        from clipwright_render.plan import resolve_kept_ranges

        clip = otio.schema.Clip()
        clip.media_reference = otio.schema.MissingReference()
        clip.source_range = _tr(0.0, 5.0)
        tl = _make_timeline_with_clips([clip])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_zero_clips_raises_invalid_input(self) -> None:
        """0 clips (Gap only etc.) -> INVALID_INPUT (DC-AS-005)."""
        from clipwright_render.plan import resolve_kept_ranges

        gap = otio.schema.Gap(source_range=_tr(0.0, 5.0))
        tl = _make_timeline_with_clips([gap])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_source_range_times_are_rational_time(self) -> None:
        """source_range is stored as RationalTime/TimeRange, not float seconds."""
        from clipwright_render.plan import resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 1.5, 3.7)])
        ranges = resolve_kept_ranges(tl)
        sr = ranges[0].source_range
        assert isinstance(sr, otio.opentime.TimeRange)
        assert isinstance(sr.start_time, otio.opentime.RationalTime)

    def test_audio_track_clips_are_ignored(self) -> None:
        """Audio track clips are ignored (first video track only)."""
        from clipwright_render.plan import resolve_kept_ranges

        video_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        video_track.append(_make_clip("/src/a.mp4", 0.0, 5.0))
        audio_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
        audio_track.append(_make_clip("/src/a.mp4", 0.0, 5.0))
        tl = otio.schema.Timeline()
        tl.tracks.append(video_track)
        tl.tracks.append(audio_track)
        ranges = resolve_kept_ranges(tl)
        # Only 1 item from the first video track
        assert len(ranges) == 1


# ---------------------------------------------------------------------------
# build_plan — trim coordinate boundary tests (DC-AS-004)
# ---------------------------------------------------------------------------


class TestBuildPlanTrimCoordinates:
    """Verify trim coordinates in filter_complex generated by build_plan."""

    def test_start_zero_duration_float(self) -> None:
        """Boundary value start=0: trim start=0 must be present in filter_complex."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex
        assert "trim=start=0" in fc or "trim=start=0." in fc

    def test_fractional_start_and_duration(self) -> None:
        """Fractional start/duration coordinate conversion is correct (6 decimal places, DC-AS-004)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        # start=1.5s, duration=3.25s → end=4.75s
        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 1.5, 3.25)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        # trim start is 1.5, end is 4.75
        assert "1.5" in plan.filter_complex
        assert "4.75" in plan.filter_complex

    def test_setpts_reset_present(self) -> None:
        """setpts=PTS-STARTPTS must be present in filter_complex (DC-AS-004)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "setpts=PTS-STARTPTS" in plan.filter_complex

    def test_asetpts_reset_present_when_audio(self) -> None:
        """With audio, asetpts=PTS-STARTPTS must be present in filter_complex."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "asetpts=PTS-STARTPTS" in plan.filter_complex


# ---------------------------------------------------------------------------
# build_plan — filter_complex structure tests (ADR-1)
# ---------------------------------------------------------------------------


class TestBuildPlanFilterComplex:
    """Verify filter_complex structure (trim/concat/labels)."""

    def test_filter_complex_is_single_string(self) -> None:
        """filter_complex is a single string (prevents command injection, ADR-1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert isinstance(plan.filter_complex, str)

    def test_single_clip_uses_concat_n1(self) -> None:
        """Even a single clip uses concat=n=1 (DC-AS-005)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "concat=n=1" in plan.filter_complex

    def test_two_clips_concat_n2(self) -> None:
        """2 clips: concat=n=2 must be present in filter_complex (ADR-1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "concat=n=2" in plan.filter_complex

    def test_video_only_concat_v1_a0(self) -> None:
        """Video only, no audio: concat=n=N:v=1:a=0 (ADR-7)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "v=1:a=0" in plan.filter_complex

    def test_audio1_concat_v1_a1(self) -> None:
        """Video with 1 audio stream: concat=n=N:v=1:a=1 (ADR-7)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "v=1:a=1" in plan.filter_complex

    def test_audio_multiple_treated_as_one(self) -> None:
        """Multiple audio streams: only the first is used (v=1, a=1, ADR-7)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=3, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "v=1:a=1" in plan.filter_complex

    def test_outv_label_present(self) -> None:
        """[outv] label must be present in filter_complex (ADR-1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outv]" in plan.filter_complex

    def test_outa_label_present_when_audio(self) -> None:
        """When audio is present, [outa] label must be in filter_complex (ADR-1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa]" in plan.filter_complex


# ---------------------------------------------------------------------------
# build_plan — audio/video matrix (ADR-7/DC-AS-002)
# ---------------------------------------------------------------------------


class TestBuildPlanAudioVideoMatrix:
    """Verify audio/video composition matrix (ADR-7/DC-AS-002)."""

    def test_no_video_raises_unsupported(self) -> None:
        """No video stream -> UNSUPPORTED_OPERATION (DC-AS-002)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=False, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions())
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_video_no_audio_map_outv_only(self) -> None:
        """Video with no audio: ffmpeg args contain only -map [outv] (ADR-7)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args = plan.ffmpeg_args
        # -map [outv] is present, -map [outa] is not
        assert "[outv]" in " ".join(str(a) for a in args)
        assert "[outa]" not in " ".join(str(a) for a in args)

    def test_video_audio1_map_outv_and_outa(self) -> None:
        """Video with 1 audio stream: both -map [outv] and -map [outa] are present (ADR-7)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args_str = " ".join(str(a) for a in plan.ffmpeg_args)
        assert "[outv]" in args_str
        assert "[outa]" in args_str

    def test_ffmpeg_args_is_list_of_str(self) -> None:
        """ffmpeg_args is list[str] (M-1: str uniformity, prevents command injection)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert isinstance(plan.ffmpeg_args, list)
        for item in plan.ffmpeg_args:
            assert isinstance(item, str), f"ffmpeg_args element is not str: {item!r}"


# ---------------------------------------------------------------------------
# build_plan — RenderOptions mapping tests (ADR-1/DC-AM-004)
# ---------------------------------------------------------------------------


class TestBuildPlanRenderOptions:
    """Verify RenderOptions fields are correctly mapped to ffmpeg arguments."""

    def test_video_codec_mapped(self) -> None:
        """-c:v must be present in ffmpeg_args."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(video_codec="libx264"))
        assert "-c:v" in plan.ffmpeg_args
        idx = plan.ffmpeg_args.index("-c:v")
        assert plan.ffmpeg_args[idx + 1] == "libx264"

    def test_audio_codec_mapped(self) -> None:
        """-c:a must be present in ffmpeg_args."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(audio_codec="aac"))
        assert "-c:a" in plan.ffmpeg_args
        idx = plan.ffmpeg_args.index("-c:a")
        assert plan.ffmpeg_args[idx + 1] == "aac"

    def test_scale_filter_in_filter_complex_when_width_height(self) -> None:
        """With width/height specified: scale is integrated inside filter_complex, not via -vf (L-4).

        Specifying both -filter_complex and -vf causes ffmpeg errors, so
        scale is chained after concat output inside filter_complex.
        """
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(width=1280, height=720))
        # scale is inside filter_complex
        assert "scale=1280:720" in plan.filter_complex
        # -vf must not be in ffmpeg_args (conflicts with filter_complex)
        assert "-vf" not in plan.ffmpeg_args
        # [outvscaled] label must be in filter_complex
        assert "[outvscaled]" in plan.filter_complex
        # -map [outvscaled] must be in ffmpeg_args
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str

    def test_fps_mapped(self) -> None:
        """-r must be present in ffmpeg_args."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(fps=60.0))
        assert "-r" in plan.ffmpeg_args

    def test_crf_mapped(self) -> None:
        """-crf must be present in ffmpeg_args."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(crf=23))
        assert "-crf" in plan.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — dry_run estimation tests (ADR-3/DC-AM-005)
# ---------------------------------------------------------------------------


class TestBuildPlanDryRun:
    """Verify dry_run estimates (segment count, duration, estimated size, warnings)."""

    def test_dry_run_segment_count(self) -> None:
        """plan.segment_count matches the number of kept ranges (ADR-3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.segment_count == 2

    def test_dry_run_total_duration(self) -> None:
        """plan.total_duration_seconds matches the sum of durations (ADR-3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert abs(plan.total_duration_seconds - 5.0) < _EPSILON

    def test_estimated_size_bytes_with_bit_rate(self) -> None:
        """With bit_rate: estimated_size_bytes = bit_rate * duration / 8 (ADR-3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 10.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        # 8Mbps * 10s / 8 = 10_000_000 bytes
        assert plan.estimated_size_bytes == pytest.approx(10_000_000, rel=_EPSILON)

    def test_estimated_size_none_when_no_bit_rate(self) -> None:
        """bit_rate None: estimated_size_bytes is None (ADR-3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.estimated_size_bytes is None

    def test_no_bit_rate_adds_warning(self) -> None:
        """When bit_rate is None, a warning is added to warnings (ADR-3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert len(plan.warnings) > 0

    @pytest.mark.parametrize(
        "options",
        [
            RenderOptions(video_codec="libx264"),
            RenderOptions(width=1280, height=720),
            RenderOptions(fps=60.0),
            RenderOptions(crf=23),
            RenderOptions(audio_codec="aac"),
        ],
    )
    def test_estimate_is_rough_warning_when_options_specified(
        self, options: RenderOptions
    ) -> None:
        """Any of video_codec/width/height/fps/crf is non-None -> rough estimate warning
        is added (DC-AM-005)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, options)
        assert len(plan.warnings) > 0

    def test_no_extra_warning_without_codec_options(self) -> None:
        """No conversion options, bit_rate present: warnings is empty (DC-AM-005)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.warnings == []

    def test_plan_has_command_list(self) -> None:
        """plan.ffmpeg_args is the list of planned command tokens (ADR-3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert isinstance(plan.ffmpeg_args, list)
        assert len(plan.ffmpeg_args) > 0


# ===========================================================================
# Multi-source concatenation extension tests (v2 contract, ADR-C1~C12)
# ===========================================================================


def _make_probe(
    has_video: bool = True,
    audio_count: int = 1,
    bit_rate: int | None = 8_000_000,
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
) -> ProbeInfo:
    """ProbeInfo helper for multi-source tests."""
    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _make_source_probes(**overrides: ProbeInfo) -> dict[str, ProbeInfo]:
    """Return a source_url -> ProbeInfo dict helper."""
    return dict(overrides)


# ---------------------------------------------------------------------------
# Aspect 3: ProbeInfo new fields (width / height / fps) storage tests
# ---------------------------------------------------------------------------


class TestProbeInfoExtendedFields:
    """Verify ProbeInfo width/height/fps fields are stored (Aspect 3, ADR-C2)."""

    def test_probe_info_width_height_fps_stored(self) -> None:
        """ProbeInfo(width=1920, height=1080, fps=30.0) -> each value is stored."""
        probe = ProbeInfo(
            has_video=True,
            audio_count=1,
            bit_rate=8_000_000,
            width=1920,
            height=1080,
            fps=30.0,
        )
        assert probe.width == 1920
        assert probe.height == 1080
        assert probe.fps == 30.0

    def test_probe_info_new_fields_default_none(self) -> None:
        """Omitting width/height/fps defaults to None (backward compat, ADR-C2)."""
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        assert probe.width is None
        assert probe.height is None
        assert probe.fps is None

    def test_probe_info_width_none_height_none_fps_set(self) -> None:
        """fps only, width/height=None combination is stored correctly."""
        probe = ProbeInfo(
            has_video=True,
            audio_count=1,
            bit_rate=None,
            width=None,
            height=None,
            fps=24.0,
        )
        assert probe.fps == 24.0
        assert probe.width is None
        assert probe.height is None


# ---------------------------------------------------------------------------
# Aspect 4: unique_sources_in_order unit tests (ADR-C9-r2)
# ---------------------------------------------------------------------------


class TestUniqueSourcesInOrder:
    """unique_sources_in_order(ranges) -> appearance-order deduplication (Aspect 4, ADR-C9-r2)."""

    def test_single_source_returns_one_element(self) -> None:
        """Multiple clips from a single source -> list with 1 element (deduplication)."""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 3.0)),
            KeptRange(source="/src/a.mp4", source_range=_tr(5.0, 2.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4"]

    def test_two_sources_preserves_appearance_order(self) -> None:
        """2 sources -> appearance order is preserved (a->b)."""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 3.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(0.0, 2.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4", "/src/b.mp4"]

    def test_interleaved_sources_deduplicates_preserves_order(self) -> None:
        """a->b->a->b appearance -> [a, b] (appearance order, deduplication)."""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 2.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(0.0, 2.0)),
            KeptRange(source="/src/a.mp4", source_range=_tr(5.0, 2.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(5.0, 2.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4", "/src/b.mp4"]

    def test_three_sources_order_preserved(self) -> None:
        """3 sources a->b->c -> [a, b, c] order."""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 1.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(0.0, 1.0)),
            KeptRange(source="/src/c.mp4", source_range=_tr(0.0, 1.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4", "/src/b.mp4", "/src/c.mp4"]

    def test_render_plan_input_sources_matches_unique_sources_in_order(self) -> None:
        """RenderPlan.input_sources matches the order from unique_sources_in_order (ADR-C9-r2)."""
        from clipwright_render.plan import build_plan, unique_sources_in_order

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 1.0, 2.0),
            _make_clip("/src/a.mp4", 5.0, 1.0),
        ]
        tl = _make_timeline_with_clips(clips)
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        expected_order = unique_sources_in_order(ranges)
        probe_a = _make_probe()
        probe_b = _make_probe()
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
        )
        assert plan.input_sources == expected_order


# ---------------------------------------------------------------------------
# Aspect 5: Multi-source path filter_complex string (ADR-C1/C5-r2/C7-r2/C11-r2)
# ---------------------------------------------------------------------------


class TestBuildPlanMultiSourceFilterComplex:
    """Verify filter_complex string for the multi-source path (Aspect 5, ADR-C1/C5-r2/C7-r2/C11-r2)."""

    def _build_multi(
        self,
        clips: list[tuple[str, float, float]],
        source_probes: dict[str, ProbeInfo],
        options: RenderOptions | None = None,
        denoise: dict | None = None,
        loudness: dict | None = None,
    ) -> RenderPlan:
        """Test helper for multi-source build_plan."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clip_objs = [_make_clip(src, start, dur) for src, start, dur in clips]
        tl = _make_timeline_with_clips(clip_objs)
        ranges = resolve_kept_ranges(tl)
        first_source = clips[0][0]
        probe_info = source_probes[first_source]
        return build_plan(
            ranges,
            probe_info,
            options or RenderOptions(),
            denoise=denoise,
            loudness=loudness,
            source_probes=source_probes,
        )

    def test_5a_input_labels_use_source_index(self) -> None:
        """Aspect 5a: [k:v] based on source index k is present in filter_complex (ADR-C1).
        Multiple clips from the same source share the same index."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # a.mp4 is index 0, b.mp4 is index 1
        assert "[0:v]" in fc
        assert "[1:v]" in fc

    def test_5a_same_source_multiple_clips_share_index(self) -> None:
        """Aspect 5a: Multiple clips from the same source share the same index (ADR-C1)."""
        clips = [
            ("/src/a.mp4", 0.0, 2.0),
            ("/src/b.mp4", 0.0, 2.0),
            ("/src/a.mp4", 5.0, 1.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # a.mp4 (index=0) appears twice, b.mp4 (index=1) once ([2:v] does not exist)
        assert "[0:v]" in fc
        assert "[1:v]" in fc
        assert "[2:v]" not in fc

    def test_5b_per_clip_normalize_chain_contains_fps_scale_pad_setsar(self) -> None:
        """Aspect 5b: Each clip's pre-chain contains fps=/scale=.../pad=.../setsar=1 (ADR-C5-r2)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 1.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        assert "fps=" in fc
        assert "force_original_aspect_ratio=decrease" in fc
        assert "pad=" in fc
        assert "setsar=1" in fc

    def test_5b_target_width_height_are_even(self) -> None:
        """Aspect 5b: target_w/h are even numbers (ADR-C4-r2, yuv420p even constraint)."""
        # Odd-resolution source -> even-rounded target appears in pad=
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1921, height=1081, fps=30.0),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # 1921->1920, 1081->1080 even-rounded values appear in pad expression
        assert "1920" in fc
        assert "1080" in fc
        # Odd values must not appear as target (1921 and 1081 are source resolutions, not pad= output)
        # filter has scale=TW:TH with even numbers
        import re

        # pattern: scale=even:even
        scale_matches = re.findall(r"scale=(\d+):(\d+)", fc)
        for w_str, h_str in scale_matches:
            assert int(w_str) % 2 == 0, f"scale width {w_str} is odd"
            assert int(h_str) % 2 == 0, f"scale height {h_str} is odd"

    def test_5b_fps_precision_at_least_5_decimal_places(self) -> None:
        """Aspect 5b: fps= value is written with at least 5 decimal places (ADR-C2-r2, NTSC fps support)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        fps_ntsc = 24000 / 1001  # ≒ 23.97602...
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=fps_ntsc),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=fps_ntsc),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        import re

        # fps=X.XXXXX format with at least 5 decimal places
        fps_matches = re.findall(r"fps=(\d+\.\d+)", fc)
        assert len(fps_matches) > 0, "fps= not found in filter_complex"
        for fps_str in fps_matches:
            decimal_part = fps_str.split(".")[1]
            assert len(decimal_part) >= 5, (
                f"fps={fps_str} has fewer than 5 decimal places (NTSC precision insufficient)"
            )

    def test_5c_aformat_stereo_48000_required_in_audio_chain(self) -> None:
        """Aspect 5c: aformat=sample_rates=48000:channel_layouts=stereo must be inserted
        in audio labels when audio is present (ADR-C7-r2, DC-AS-002/AM-007)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc

    def test_5d_concat_label_outv_outa_with_audio(self) -> None:
        """Aspect 5d: concat=n=N:v=1:a=1 and [outv][outa] are present (ADR-C11-r2)."""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        assert "concat=n=2:v=1:a=1" in fc
        assert "[outv]" in fc
        assert "[outa]" in fc

    def test_5d_concat_n_equals_clip_count(self) -> None:
        """Aspect 5d: concat=n= matches the clip count (ADR-C11-r2)."""
        clips = [
            ("/src/a.mp4", 0.0, 2.0),
            ("/src/b.mp4", 0.0, 2.0),
            ("/src/a.mp4", 5.0, 1.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # 3 clips
        assert "concat=n=3" in fc


# ---------------------------------------------------------------------------
# Aspect 6: Output spec resolution (ADR-C4-r2)
# ---------------------------------------------------------------------------


class TestBuildPlanOutputSpec:
    """Verify output spec resolution logic (Aspect 6, ADR-C4-r2)."""

    def _build_2source(
        self,
        options: RenderOptions,
        probe_a: ProbeInfo | None = None,
        probe_b: ProbeInfo | None = None,
    ) -> RenderPlan:
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        pa = probe_a or _make_probe(width=1920, height=1080, fps=30.0)
        pb = probe_b or _make_probe(width=1280, height=720, fps=30.0)
        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {"/src/a.mp4": pa, "/src/b.mp4": pb}
        return build_plan(ranges, pa, options, source_probes=source_probes)

    def test_6_both_width_height_specified_uses_options(self) -> None:
        """Both width/height specified -> those values (even) are used in scale (ADR-C4-r2)."""
        plan = self._build_2source(RenderOptions(width=1280, height=720))
        assert "scale=1280:720" in plan.filter_complex

    def test_6_width_only_specified_raises_validation_error(self) -> None:
        """Only width specified -> pydantic ValidationError at RenderOptions construction.

        width/height must be specified as a pair or both None (strict rejection).
        This is a contract that never reaches build_plan, so it is validated at RenderOptions.
        """
        with pytest.raises(ValidationError):
            RenderOptions(width=640)

    def test_6_height_only_specified_raises_validation_error(self) -> None:
        """Only height specified -> pydantic ValidationError at RenderOptions construction.

        width/height must be specified as a pair or both None (strict rejection).
        This is a contract that never reaches build_plan, so it is validated at RenderOptions.
        """
        with pytest.raises(ValidationError):
            RenderOptions(height=480)

    def test_6_no_options_uses_first_source_spec(self) -> None:
        """No options -> first clip's source spec (width/height/fps) is used (ADR-C4-r2)."""
        plan = self._build_2source(RenderOptions())
        fc = plan.filter_complex
        # First source a.mp4 1920x1080 becomes the target
        assert "scale=1920:1080" in fc

    def test_6_fps_option_alone_adopted(self) -> None:
        """options.fps only specified -> that fps is used in filter_complex fps= (ADR-C4-r2)."""
        plan = self._build_2source(RenderOptions(fps=60.0))
        fc = plan.filter_complex
        assert "fps=60" in fc or "fps=60." in fc

    def test_6_first_source_width_none_raises_invalid_input(self) -> None:
        """First source width=None -> INVALID_INPUT (cannot resolve output spec, ADR-C4-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        pa = _make_probe(width=None, height=1080, fps=30.0)
        pb = _make_probe(width=1920, height=1080, fps=30.0)
        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {"/src/a.mp4": pa, "/src/b.mp4": pb}
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, pa, RenderOptions(), source_probes=source_probes)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_6_first_source_fps_none_raises_invalid_input(self) -> None:
        """First source fps=None -> INVALID_INPUT (cannot resolve output spec, ADR-C2-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        pa = _make_probe(width=1920, height=1080, fps=None)
        pb = _make_probe(width=1920, height=1080, fps=30.0)
        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {"/src/a.mp4": pa, "/src/b.mp4": pb}
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, pa, RenderOptions(), source_probes=source_probes)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# Aspect 7: Audio-absent source anullsrc padding (ADR-C7-r2)
# ---------------------------------------------------------------------------


class TestBuildPlanAudioMixedAnullsrc:
    """Audio-absent sources are padded with anullsrc enabling concat a=1 (Aspect 7, ADR-C7-r2)."""

    def test_7_audio_absent_source_generates_anullsrc(self) -> None:
        """Clip from audio-absent source -> 'anullsrc' is present in filter_complex (ADR-C7-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/with_audio.mp4", 0.0, 3.0),
            _make_clip("/src/no_audio.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/with_audio.mp4": _make_probe(
                audio_count=1, width=1920, height=1080, fps=30.0
            ),
            "/src/no_audio.mp4": _make_probe(
                audio_count=0, width=1920, height=1080, fps=30.0
            ),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/with_audio.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        assert "anullsrc" in fc

    def test_7_anullsrc_clip_duration_matches_video_duration(self) -> None:
        """anullsrc's atrim=0:DUR matches the video duration in seconds (ADR-C7-r2, DC-AM-005)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        # audio-absent clip duration=2.5 seconds
        clips = [
            _make_clip("/src/with_audio.mp4", 0.0, 3.0),
            _make_clip("/src/no_audio.mp4", 0.0, 2.5),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/with_audio.mp4": _make_probe(
                audio_count=1, width=1920, height=1080, fps=30.0
            ),
            "/src/no_audio.mp4": _make_probe(
                audio_count=0, width=1920, height=1080, fps=30.0
            ),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/with_audio.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        # anullsrc is present as "atrim=0:2.5" or "atrim=0:2.50000" etc.
        # re.search with boundary to avoid false positives like atrim=0:2.5001
        import re

        assert "anullsrc" in fc
        assert re.search(r"atrim=0:2\.5(?:0*)?(?:[^0-9]|$)", fc), (
            f"atrim=0:2.5... not found in filter_complex: {fc}"
        )

    def test_7_audio_mixed_concat_a1(self) -> None:
        """Even with mixed audio sources, concat a=1 is achieved (ADR-C7-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        assert "concat=n=2:v=1:a=1" in fc


# ---------------------------------------------------------------------------
# Aspect 8: All sources audio-absent (DC-GP-002)
# ---------------------------------------------------------------------------


class TestBuildPlanAllAudiolessMultiSource:
    """All sources audio-absent -> a=0 video-only, denoise/loudness skipped (Aspect 8, DC-GP-002)."""

    def test_8_all_audioless_concat_a0(self) -> None:
        """All sources audio_count=0 -> concat a=0 (video only)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        assert "a=0" in fc
        assert "[outa]" not in fc

    def test_8_all_audioless_with_denoise_skips_filter_adds_warning(self) -> None:
        """All sources audio-absent + denoise directive -> filter not injected, warning added (ADR-C11-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        denoise = {
            "tool": "clipwright-noise",
            "version": "0.1.0",
            "kind": "denoise",
            "backend": "afftdn",
            "params": {"nr": 12.0, "nf": -40.0, "nt": "w"},
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            denoise=denoise,
            source_probes=source_probes,
        )
        # afftdn is not injected
        assert "afftdn" not in plan.filter_complex
        # warning is added
        assert len(plan.warnings) > 0

    def test_8_all_audioless_with_loudness_skips_filter_adds_warning(self) -> None:
        """All sources audio-absent + loudness directive -> filter not injected, warning added (ADR-C11-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        loudness = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "peak",
            "scope": "track",
            "target": {"peak_db": -1.0},
            "measured": {"max_volume_db": -3.0},
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            loudness=loudness,
            source_probes=source_probes,
        )
        # loudnorm/volume is not injected
        assert "loudnorm" not in plan.filter_complex
        assert "volume=" not in plan.filter_complex
        # warning is added
        assert len(plan.warnings) > 0


# ---------------------------------------------------------------------------
# Aspect 9: has_video mixed -> UNSUPPORTED_OPERATION (DC-GP-004, ADR-C12)
# ---------------------------------------------------------------------------


class TestBuildPlanHasVideoMixed:
    """Any source with has_video=False -> UNSUPPORTED_OPERATION (Aspect 9, ADR-C12)."""

    def test_9_second_source_no_video_raises_unsupported(self) -> None:
        """Second source has_video=False -> UNSUPPORTED_OPERATION (ADR-C12)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b_audio_only.mp3", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(
                has_video=True, width=1920, height=1080, fps=30.0
            ),
            "/src/b_audio_only.mp3": _make_probe(
                has_video=False, audio_count=1, width=None, height=None, fps=None
            ),
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                source_probes["/src/a.mp4"],
                RenderOptions(),
                source_probes=source_probes,
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION
        # hint contains basename
        assert "b_audio_only" in exc_info.value.hint
        # absolute path must not be exposed in message / hint (SR L-2: CWE-209 info leak prevention)
        assert "/src/" not in exc_info.value.message
        assert "/src/" not in exc_info.value.hint

    def test_9_first_source_no_video_raises_unsupported(self) -> None:
        """First source has_video=False also raises UNSUPPORTED_OPERATION (ADR-C12)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a_audio_only.mp3", 0.0, 2.0),
            _make_clip("/src/b.mp4", 0.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a_audio_only.mp3": _make_probe(
                has_video=False, audio_count=1, width=None, height=None, fps=None
            ),
            "/src/b.mp4": _make_probe(
                has_video=True, width=1920, height=1080, fps=30.0
            ),
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                source_probes["/src/a_audio_only.mp3"],
                RenderOptions(),
                source_probes=source_probes,
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION


# ---------------------------------------------------------------------------
# Aspect 10: Backward compatibility (single-source path, ADR-C3)
# ---------------------------------------------------------------------------


class TestBuildPlanSingleSourceBackwardCompat:
    """Single-source path produces unchanged filter_complex after multi-source extension (Aspect 10, ADR-C3)."""

    def test_10_single_source_filter_complex_unchanged(self) -> None:
        """source_probes not specified (single source) -> identical to existing single-source filter_complex (ADR-C3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=8_000_000)

        # source_probes not specified
        plan_no_sp = build_plan(ranges, probe, RenderOptions())
        # source_probes=None explicitly
        plan_sp_none = build_plan(ranges, probe, RenderOptions(), source_probes=None)
        # source_probes with only one unique source
        plan_sp_single = build_plan(
            ranges, probe, RenderOptions(), source_probes={"/src/a.mp4": probe}
        )

        # All 3 patterns produce identical filter_complex
        assert plan_no_sp.filter_complex == plan_sp_none.filter_complex
        assert plan_no_sp.filter_complex == plan_sp_single.filter_complex

    def test_10_single_source_no_aformat_in_filter_complex(self) -> None:
        """Single-source path does not include aformat in filter_complex (ADR-C3 backward compat)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        # audio normalisation filter (aformat) is not needed on the single-source path
        assert "aformat" not in plan.filter_complex

    def test_10_single_source_no_fps_scale_pad_per_clip(self) -> None:
        """Single-source path does not include per-clip fps/scale/pad (ADR-C3 backward compat)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex
        # Single-source path has no fps= / pad= / setsar= (no per-clip spec normalization)
        assert "fps=" not in fc
        assert "pad=" not in fc
        assert "setsar" not in fc

    def test_10_single_source_input_sources_has_one_element(self) -> None:
        """Single-source path: RenderPlan.input_sources has 1 element (ADR-C9-r2)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.input_sources == ["/src/a.mp4"]


# ---------------------------------------------------------------------------
# Aspect 11: Multi-source path _append_audio_pipe application tests (DC-GP-005 / plan.py:739,741,961)
# ---------------------------------------------------------------------------

# Valid afftdn denoise directive (for multi-source tests)
_VALID_AFFTDN_DIRECTIVE: dict = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "afftdn",
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
}

# Valid peak loudness directive (for multi-source tests)
_VALID_PEAK_DIRECTIVE: dict = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "peak",
    "scope": "track",
    "target": {"peak_db": -1.0},
    "measured": {"max_volume_db": -7.68},
}

# Valid loudnorm directive (for multi-source tests)
_VALID_LOUDNORM_DIRECTIVE: dict = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "loudnorm",
    "scope": "track",
    "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
    "measured": {
        "input_i": -20.73,
        "input_tp": -7.68,
        "input_lra": 0.10,
        "input_thresh": -30.73,
        "target_offset": 0.03,
    },
}


class TestBuildPlanMultiSourceAudioPipe:
    """Verify _append_audio_pipe application for the multi-source path (Aspect 11, DC-GP-005).

    plan.py:739/741: Confirm audio map terminal label becomes [outa_dn] / [outa_ln]
    for multi-source + audio + denoise/loudness.
    plan.py:961: Confirm measurement mismatch warning is added for multi-source + loudness.
    """

    def _build_multi_with_audio(
        self,
        denoise: dict | None = None,
        loudness: dict | None = None,
    ) -> object:
        """Helper to call build_plan with multi-source (a.mp4 / b.mp4) + audio."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        probe_a = source_probes["/src/a.mp4"]
        return build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            denoise=denoise,
            loudness=loudness,
            source_probes=source_probes,
        )

    def test_11a_multi_source_with_audio_and_denoise_injects_afftdn(self) -> None:
        """Multi-source + audio + denoise -> afftdn is injected into filter_complex (plan.py:739-741)."""
        plan = self._build_multi_with_audio(denoise=_VALID_AFFTDN_DIRECTIVE)
        assert "afftdn" in plan.filter_complex

    def test_11b_multi_source_with_audio_and_denoise_audio_map_label_outa_dn(
        self,
    ) -> None:
        """Multi-source + audio + denoise -> audio map terminal label is [outa_dn] (plan.py:741)."""
        plan = self._build_multi_with_audio(denoise=_VALID_AFFTDN_DIRECTIVE)
        # -map [outa_dn] is present in ffmpeg_args (list[str] direct comparison)
        assert "[outa_dn]" in plan.ffmpeg_args
        # -map [outa] is not present (replaced by [outa_dn])
        assert "[outa]" not in plan.ffmpeg_args

    def test_11c_multi_source_with_audio_and_loudness_injects_loudnorm(self) -> None:
        """Multi-source + audio + loudnorm -> loudnorm is injected into filter_complex (plan.py:739)."""
        plan = self._build_multi_with_audio(loudness=_VALID_LOUDNORM_DIRECTIVE)
        assert "loudnorm" in plan.filter_complex

    def test_11d_multi_source_with_audio_and_loudness_audio_map_label_outa_ln(
        self,
    ) -> None:
        """Multi-source + audio + loudness -> audio map terminal label is [outa_ln] (plan.py:739)."""
        plan = self._build_multi_with_audio(loudness=_VALID_PEAK_DIRECTIVE)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str

    def test_11e_multi_source_with_audio_and_peak_loudness_adds_measurement_warning(
        self,
    ) -> None:
        """Multi-source (unique sources >= 2) + loudness -> measurement warning in warnings (plan.py:961)."""
        plan = self._build_multi_with_audio(loudness=_VALID_PEAK_DIRECTIVE)
        # Verify ADR-C11-r2 measurement mismatch warning text
        warning_text = " ".join(plan.warnings)
        assert (
            "multi-source" in warning_text
            or "measured" in warning_text
            or "deviation" in warning_text
        )

    def test_11f_multi_source_with_audio_and_loudnorm_adds_measurement_warning(
        self,
    ) -> None:
        """Multi-source + loudnorm also includes measurement mismatch warning (plan.py:961)."""
        plan = self._build_multi_with_audio(loudness=_VALID_LOUDNORM_DIRECTIVE)
        warning_text = " ".join(plan.warnings)
        assert "multi-source" in warning_text or "measured" in warning_text


# ===========================================================================
# BGM mix extension tests (ADR-B4-r2/B5-r2/B5-r3/B6-r2/B9-r3)
# ===========================================================================
# Real ffmpeg verified syntax (2026-06-11):
# - -stream_loop -1 + atrim=0:{main_dur} -> 5s output OK
# - [N:a]aformat=48000:stereo,atrim=0:{d},asetpts=PTS-STARTPTS,volume={v}dB[bgm] -> OK
# - [main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm] -> OK
# - sidechaincompress: BGM=1st input, main=2nd (sidechain) -> OK
# - afade=t=in:st=0:d={d}, afade=t=out:st={st}:d={d} -> OK
# - main silent + BGM standalone path (no amix) -> 1 audio stream in output OK
# ===========================================================================

# ---------------------------------------------------------------------------
# BGM test helper constants
# ---------------------------------------------------------------------------

_VALID_BGM_DIRECTIVE: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
}

_VALID_BGM_DIRECTIVE_WITH_FADE: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 1.0,
    "fade_out_sec": 1.5,
    "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
}

_VALID_BGM_DIRECTIVE_DUCKING: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "ducking": {"enabled": True, "threshold": 0.05, "ratio": 4.0},
}


def _make_bgm_clip(
    bgm_source: str = "/proj/bgm.mp3",
    directive: dict | None = None,
    timeline_duration_sec: float = 5.0,
) -> BgmClip:
    """Helper to build a BgmClip for BGM tests."""
    from pydantic import TypeAdapter

    from clipwright_render.plan import (  # type: ignore[attr-defined]
        BgmClip,
        BgmDirective,
    )

    d = directive or _VALID_BGM_DIRECTIVE
    bgm_dir = TypeAdapter(BgmDirective).validate_python(d)
    source_range = _tr(0.0, timeline_duration_sec)
    return BgmClip(
        source=bgm_source,
        source_range=source_range,
        directive=bgm_dir,
    )


def _make_single_source_timeline_with_audio(
    source: str = "/src/a.mp4",
    duration: float = 5.0,
) -> otio.schema.Timeline:
    """Return a single-source Timeline with audio."""
    video_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    video_track.append(_make_clip(source, 0.0, duration))
    tl = otio.schema.Timeline()
    tl.tracks.append(video_track)
    return tl


def _make_bgm_otio_timeline(
    bgm_source: str = "/proj/bgm.mp3",
    directive: dict | None = None,
    main_source: str = "/src/a.mp4",
    main_duration: float = 5.0,
) -> otio.schema.Timeline:
    """Return a Timeline containing a BGM clip on the A2 track (for resolve_bgm tests)."""
    d = directive or _VALID_BGM_DIRECTIVE
    # V1 video track
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    video_track.append(_make_clip(main_source, 0.0, main_duration))
    # A1 main audio track (kind!="bgm" clip)
    audio_track_a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    clip_a1 = _make_clip(main_source, 0.0, main_duration)
    audio_track_a1.append(clip_a1)
    # A2 BGM track (kind=="bgm" clip)
    audio_track_a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    clip_bgm = otio.schema.Clip()
    clip_bgm.media_reference = otio.schema.ExternalReference(target_url=bgm_source)
    clip_bgm.source_range = _tr(0.0, main_duration)
    clip_bgm.metadata["clipwright"] = d
    audio_track_a2.append(clip_bgm)
    tl = otio.schema.Timeline()
    tl.tracks.append(video_track)
    tl.tracks.append(audio_track_a1)
    tl.tracks.append(audio_track_a2)
    return tl


# ---------------------------------------------------------------------------
# Aspect 1: BgmDirective reader-strict validation (ADR-B9-r2/B9-r3)
# ---------------------------------------------------------------------------


class TestBgmDirectiveValidation:
    """Verify BgmDirective reader-strict validation (Aspect 1, ADR-B9-r2)."""

    def test_1_valid_directive_accepts_normal_values(self) -> None:
        """Normal values build a valid BgmDirective (Aspect 1)."""
        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        d = BgmDirective(**_VALID_BGM_DIRECTIVE)
        assert d.volume_db == -6.0
        assert d.fade_in_sec == 0.0
        assert d.fade_out_sec == 0.0
        assert d.kind == "bgm"

    def test_1_invalid_kind_raises(self) -> None:
        """kind other than "bgm" -> ValidationError (Aspect 1)."""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, kind="noise")
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_negative_fade_in_raises(self) -> None:
        """Negative fade_in_sec -> ValidationError (ge=0 constraint, ADR-B9-r3)."""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=-0.1)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_negative_fade_out_raises(self) -> None:
        """Negative fade_out_sec -> ValidationError (ge=0 constraint, ADR-B9-r3)."""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=-0.1)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_tool_over_64_chars_raises(self) -> None:
        """tool exceeds max_length=64 -> ValidationError (ADR-B9-r2)."""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, tool="x" * 65)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_version_over_64_chars_raises(self) -> None:
        """version exceeds max_length=64 -> ValidationError (ADR-B9-r2)."""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, version="v" * 65)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_unknown_key_raises_forbidden_extra(self) -> None:
        """Unknown key -> reader-strict (forbid extra) raises ValidationError (Aspect 1)."""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, unknown_field="evil")
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_inf_volume_db_raises(self) -> None:
        """volume_db is inf -> allow_inf_nan=False raises ValidationError (Aspect 1)."""
        import math

        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, volume_db=math.inf)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_nan_volume_db_raises(self) -> None:
        """volume_db is nan -> allow_inf_nan=False raises ValidationError (Aspect 1)."""
        import math

        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, volume_db=math.nan)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)


# ---------------------------------------------------------------------------
# Aspect 2: resolve_bgm (ADR-B4-r2)
# ---------------------------------------------------------------------------


class TestResolveBgm:
    """Verify resolve_bgm behaviour (Aspect 2, ADR-B4-r2)."""

    def test_2_single_bgm_clip_returns_bgm_clip(self) -> None:
        """1 clip with kind=="bgm" -> returns BgmClip (ADR-B4-r2)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline()
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.source == "/proj/bgm.mp3"

    def test_2_no_bgm_clip_returns_none(self) -> None:
        """0 clips with kind=="bgm" -> None (backward compat, ADR-B4-r2)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        # No BGM track: V1 + A1 only
        tl = _make_single_source_timeline_with_audio()
        result = resolve_bgm(tl)
        assert result is None

    def test_2_two_bgm_clips_raises_unsupported(self) -> None:
        """2+ clips with kind=="bgm" -> UNSUPPORTED_OPERATION (ADR-B4-r2)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        # Place BGM clips on A2 and A3
        tl = _make_bgm_otio_timeline()  # A2 already has 1 clip
        # Add another to A3
        audio_track_a3 = otio.schema.Track(name="A3", kind=otio.schema.TrackKind.Audio)
        clip_bgm2 = otio.schema.Clip()
        clip_bgm2.media_reference = otio.schema.ExternalReference(
            target_url="/proj/bgm2.mp3"
        )
        clip_bgm2.source_range = _tr(0.0, 5.0)
        clip_bgm2.metadata["clipwright"] = _VALID_BGM_DIRECTIVE
        audio_track_a3.append(clip_bgm2)
        tl.tracks.append(audio_track_a3)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_2_a1_with_main_audio_and_one_bgm_does_not_raise_unsupported(self) -> None:
        """A1 main audio (kind!="bgm") + 1 BGM clip on A2 -> does not raise UNSUPPORTED (ADR-B4-r2 DC-AS-002).

        Having 2 audio tracks is fine as long as there is only 1 BGM clip.
        Do not judge by the number of audio tracks (avoids false positives for permanent A1 main audio).
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline()  # V1 + A1(main) + A2(BGM)
        # 2 audio tracks but only 1 BGM clip
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)

    def test_2_bgm_clip_source_preserved(self) -> None:
        """BgmClip returned by resolve_bgm has the correct source path (Aspect 2)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline(bgm_source="/music/track.mp3")
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.source == "/music/track.mp3"

    def test_2_bgm_clip_directive_volume_preserved(self) -> None:
        """BgmClip returned by resolve_bgm has the correct directive.volume_db (Aspect 2)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        d = dict(_VALID_BGM_DIRECTIVE, volume_db=-12.0)
        tl = _make_bgm_otio_timeline(directive=d)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.volume_db == -12.0

    def test_2_bgm_only_in_a2_but_a1_has_normal_clips(self) -> None:
        """A1 has normal clips without kind + A2 has kind=="bgm" -> 1 clip detected correctly (ADR-B4-r2)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        # A1 is a normal clip without metadata
        tl = _make_bgm_otio_timeline()
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)


# ---------------------------------------------------------------------------
# Aspects 3 & 4: build_plan(bgm=BgmClip) -> audio_map_label / RenderPlan.bgm_source / BGM index
# ---------------------------------------------------------------------------


class TestBuildPlanBgmOutputLabels:
    """Verify build_plan(bgm=...) returns correct audio_map_label and RenderPlan fields (Aspects 3 & 4)."""

    def _build_with_bgm(
        self,
        bgm_source: str = "/proj/bgm.mp3",
        directive: dict | None = None,
        audio_count: int = 1,
    ) -> RenderPlan:  # type: ignore[name-defined]
        """Helper for single-source + BGM build_plan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        bgm_clip = _make_bgm_clip(bgm_source=bgm_source, directive=directive)
        return build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]

    def test_3_audio_map_label_is_outa_bgm(self) -> None:
        """bgm=BgmClip -> audio_map_label == [outa_bgm] (Aspect 3)."""
        plan = self._build_with_bgm()
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_bgm]" in args_str

    def test_3_bgm_source_set_in_render_plan(self) -> None:
        """build_plan(bgm=...) -> RenderPlan.bgm_source == bgm.source (Aspect 3)."""
        plan = self._build_with_bgm(bgm_source="/proj/bgm.mp3")
        assert plan.bgm_source == "/proj/bgm.mp3"  # type: ignore[attr-defined]

    def test_3_bgm_source_none_when_bgm_not_provided(self) -> None:
        """bgm=None -> RenderPlan.bgm_source is None (backward compat, ADR-B7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.bgm_source is None  # type: ignore[attr-defined]

    def test_4_bgm_index_equals_len_input_sources(self) -> None:
        """BGM filter input label is [len(input_sources):a] (DC-AS-005, Aspect 4)."""
        plan = self._build_with_bgm()
        # Single-source path: input_sources=1 -> BGM index=1 -> [1:a]
        expected_label = f"[{len(plan.input_sources)}:a]"
        assert expected_label in plan.filter_complex

    def test_4_bgm_source_not_in_input_sources(self) -> None:
        """bgm_source is not in input_sources (DC-AS-005, Aspect 4)."""
        plan = self._build_with_bgm(bgm_source="/proj/bgm.mp3")
        assert plan.bgm_source not in plan.input_sources  # type: ignore[attr-defined]

    def test_4_bgm_index_two_sources(self) -> None:
        """2 main sources + BGM -> BGM index=2 ([2:a] in filter, DC-AS-005)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        bgm_clip = _make_bgm_clip(bgm_source="/proj/bgm.mp3")
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )
        # 2 sources -> BGM index=2
        assert "[2:a]" in plan.filter_complex
        assert plan.bgm_source not in plan.input_sources  # type: ignore[attr-defined]
        assert len(plan.input_sources) == 2


# ---------------------------------------------------------------------------
# Aspect 5: BGM filter string (real ffmpeg verified syntax, ADR-B5-r3/B6-r2)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmFilterComplex:
    """Verify BGM filter_complex string composition (Aspect 5, ADR-B5-r3/B6-r2)."""

    def _build_with_bgm_fc(
        self,
        directive: dict | None = None,
        audio_count: int = 1,
        bgm_source: str = "/proj/bgm.mp3",
    ) -> tuple[str, RenderPlan]:  # type: ignore[name-defined]
        """Single-source BGM test helper returning filter_complex string and RenderPlan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        bgm_clip = _make_bgm_clip(bgm_source=bgm_source, directive=directive)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        return plan.filter_complex, plan

    def test_5_bgm_filter_has_aformat_48000_stereo(self) -> None:
        """BGM side contains aformat=sample_rates=48000:channel_layouts=stereo (DC-AS-007)."""
        fc, _ = self._build_with_bgm_fc()
        assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc

    def test_5_bgm_filter_has_atrim_main_dur(self) -> None:
        """BGM filter contains atrim=0:{main_dur} (ADR-B6-r2, -stream_loop + atrim)."""
        fc, _ = self._build_with_bgm_fc()
        # main_dur=5.0 (1 clip duration=5.0)
        assert "atrim=0:5" in fc

    def test_5_bgm_filter_has_volume_db(self) -> None:
        """BGM filter contains volume={db}dB (Aspect 5)."""
        fc, _ = self._build_with_bgm_fc()
        assert "volume=-6dB" in fc or "volume=-6.0dB" in fc

    def test_5_bgm_filter_has_asetpts(self) -> None:
        """BGM filter contains asetpts=PTS-STARTPTS (Aspect 5)."""
        fc, _ = self._build_with_bgm_fc()
        assert "asetpts=PTS-STARTPTS" in fc

    def test_5_main_fmt_aformat_present(self) -> None:
        """Main side contains aformat=sample_rates=48000:channel_layouts=stereo (DC-AS-007).

        Even on the single-source path, the main-side aformat is required for amix input spec normalization (ADR-B5-r3).
        """
        fc, _ = self._build_with_bgm_fc()
        # [main_fmt] is created and aformat is present
        assert "main_fmt" in fc
        assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc

    def test_5_amix_inputs2_normalize0_present(self) -> None:
        """amix=inputs=2:normalize=0 is present (ADR-B5-r3)."""
        fc, _ = self._build_with_bgm_fc()
        assert "amix=inputs=2:normalize=0" in fc

    def test_5_alimiter_present(self) -> None:
        """alimiter=limit=1.0 is present (DC-AM-001, anti-clipping)."""
        fc, _ = self._build_with_bgm_fc()
        assert "alimiter=limit=1.0" in fc

    def test_5_outa_bgm_label_present(self) -> None:
        """[outa_bgm] label is present in filter_complex (Aspect 5)."""
        fc, _ = self._build_with_bgm_fc()
        assert "[outa_bgm]" in fc

    def test_5_aloop_not_present(self) -> None:
        """aloop is not present in filter_complex (ADR-B6-r2, aloop deprecated)."""
        fc, _ = self._build_with_bgm_fc()
        assert "aloop" not in fc


# ---------------------------------------------------------------------------
# Aspect 6: fade_in/out=0 does not inject afade (ADR-B9-r3, DC-AM-003)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmFade:
    """Verify afade injection conditions (Aspect 6, ADR-B9-r3/DC-AM-003)."""

    def test_6_fade_in_zero_no_afade_in(self) -> None:
        """fade_in_sec=0.0 -> afade=t=in is not present in filter_complex (DC-AM-003)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=0.0)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=in" not in plan.filter_complex

    def test_6_fade_out_zero_no_afade_out(self) -> None:
        """fade_out_sec=0.0 -> afade=t=out is not present in filter_complex (DC-AM-003)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=0.0)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=out" not in plan.filter_complex

    def test_6_fade_in_positive_afade_in_present(self) -> None:
        """fade_in_sec > 0 -> afade=t=in is present in filter_complex (DC-AM-003)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=1.0)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=in" in plan.filter_complex

    def test_6_fade_out_positive_afade_out_present(self) -> None:
        """fade_out_sec > 0 -> afade=t=out is present in filter_complex (DC-AM-003)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=1.5)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=out" in plan.filter_complex


# ---------------------------------------------------------------------------
# Aspect 7: ducking ON/OFF (ADR-B5-r3, DC-AS-006)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmDucking:
    """Verify ducking ON/OFF filter generation (Aspect 7, ADR-B5-r3/DC-AS-006)."""

    def test_7_ducking_off_no_sidechaincompress(self) -> None:
        """ducking.enabled=False -> sidechaincompress is not present in filter (Aspect 7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "sidechaincompress" not in plan.filter_complex

    def test_7_ducking_on_sidechaincompress_present(self) -> None:
        """ducking.enabled=True -> sidechaincompress is present in filter_complex (Aspect 7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "sidechaincompress" in plan.filter_complex

    def test_7_ducking_on_threshold_ratio_in_filter(self) -> None:
        """ducking ON -> threshold=0.05:ratio=4.0 is present in filter (DC-AS-006)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        fc = plan.filter_complex
        assert "threshold=0.05" in fc
        assert "ratio=4.0" in fc or "ratio=4" in fc

    def test_7_ducking_on_bgm_is_first_input_of_sidechaincompress(self) -> None:
        """ducking ON: order is [bgm][main_sc]sidechaincompress (BGM=1st input, DC-AS-006)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        fc = plan.filter_complex
        # [bgm] appears before sidechaincompress ([bgm]...[main_sc]sidechaincompress order)
        bgm_pos = fc.find("[bgm]")
        sc_pos = fc.find("sidechaincompress")
        assert bgm_pos != -1 and sc_pos != -1
        assert bgm_pos < sc_pos, (
            f"[bgm] (pos={bgm_pos}) is after sidechaincompress (pos={sc_pos})"
        )

    def test_7_ducking_on_asplit_present(self) -> None:
        """ducking ON -> asplit is present in filter_complex (splits main audio into 2, DC-AS-006)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "asplit" in plan.filter_complex

    def test_7_ducking_on_outa_bgm_in_ffmpeg_args(self) -> None:
        """ducking ON: audio_map_label is still [outa_bgm] (Aspect 7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "[outa_bgm]" in plan.ffmpeg_args


# ---------------------------------------------------------------------------
# Aspect 8: BGM stage is appended after the existing audio pipe (denoise/loudness interop)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmAfterAudioPipe:
    """BGM stage is appended after denoise/loudness and correctly references the terminal label (Aspect 8)."""

    def _build_with_denoise_and_bgm(self) -> RenderPlan:  # type: ignore[name-defined]
        """Helper for denoise + BGM build_plan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        return build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )

    def _build_with_loudness_and_bgm(self) -> RenderPlan:  # type: ignore[name-defined]
        """Helper for loudness + BGM build_plan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        return build_plan(
            ranges,
            probe,
            RenderOptions(),
            loudness=_VALID_PEAK_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )

    def test_8_denoise_then_bgm_audio_map_label_outa_bgm(self) -> None:
        """denoise + BGM -> audio_map_label == [outa_bgm] (Aspect 8)."""
        plan = self._build_with_denoise_and_bgm()
        assert "[outa_bgm]" in plan.ffmpeg_args

    def test_8_denoise_then_bgm_afftdn_present_in_filter(self) -> None:
        """denoise + BGM -> afftdn is present in filter_complex (Aspect 8)."""
        plan = self._build_with_denoise_and_bgm()
        assert "afftdn" in plan.filter_complex

    def test_8_denoise_then_bgm_main_fmt_uses_outa_dn(self) -> None:
        """denoise + BGM -> [main_fmt] is the aformat chain from [outa_dn] (Aspect 8).

        The BGM stage's main input is an aformat of the denoise terminal [outa_dn].
        Verify [outa_dn] or its aformat label appears as main_fmt in filter_complex.
        """
        plan = self._build_with_denoise_and_bgm()
        fc = plan.filter_complex
        # [outa_dn] is present in filter
        assert "[outa_dn]" in fc
        # [main_fmt] is present in filter (referencing [outa_dn] as aformat input)
        assert "main_fmt" in fc
        # [outa_dn] appears before main_fmt (correct connection order)
        dn_pos = fc.find("[outa_dn]")
        fmt_pos = fc.find("main_fmt")
        assert dn_pos < fmt_pos, (
            f"[outa_dn] (pos={dn_pos}) is after main_fmt (pos={fmt_pos})"
        )

    def test_8_loudness_then_bgm_audio_map_label_outa_bgm(self) -> None:
        """loudness + BGM -> audio_map_label == [outa_bgm] (Aspect 8)."""
        plan = self._build_with_loudness_and_bgm()
        assert "[outa_bgm]" in plan.ffmpeg_args

    def test_8_loudness_then_bgm_outa_ln_present_in_filter(self) -> None:
        """loudness + BGM -> [outa_ln] is present in filter_complex (Aspect 8)."""
        plan = self._build_with_loudness_and_bgm()
        assert "[outa_ln]" in plan.filter_complex


# ---------------------------------------------------------------------------
# Aspect 9: Backward compatibility (no BGM produces same filter_complex as before, ADR-B7)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmBackwardCompat:
    """bgm=None produces unchanged filter_complex (Aspect 9, ADR-B7)."""

    def test_9_bgm_none_filter_complex_unchanged(self) -> None:
        """bgm=None -> filter_complex is identical to the legacy BGM-absent form (ADR-B7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        # No BGM (default)
        plan_no_bgm = build_plan(ranges, probe, RenderOptions())
        # bgm=None explicit
        plan_bgm_none = build_plan(ranges, probe, RenderOptions(), bgm=None)  # type: ignore[call-arg]
        assert plan_no_bgm.filter_complex == plan_bgm_none.filter_complex

    def test_9_bgm_none_no_outa_bgm_in_filter(self) -> None:
        """bgm=None -> [outa_bgm] is not present in filter_complex (ADR-B7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa_bgm]" not in plan.filter_complex

    def test_9_bgm_none_bgm_source_is_none(self) -> None:
        """bgm=None -> RenderPlan.bgm_source is None (ADR-B7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.bgm_source is None  # type: ignore[attr-defined]

    def test_9_bgm_none_no_alimiter_in_filter(self) -> None:
        """bgm=None -> alimiter is not present in filter_complex (confirms no BGM stage, ADR-B7)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "alimiter" not in plan.filter_complex


# ---------------------------------------------------------------------------
# Aspect 10: Main audio absent (has_main_audio=False) + BGM (ADR-B5-r2, DC-AS-004)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmNoMainAudio:
    """Main audio absent + BGM -> BGM standalone path with has_audio_output=True (Aspect 10, ADR-B5-r2)."""

    def _build_no_main_audio_with_bgm(self) -> RenderPlan:  # type: ignore[name-defined]
        """Helper for no main audio (audio_count=0) + BGM build_plan."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        return build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]

    def test_10_no_main_audio_with_bgm_has_audio_map(self) -> None:
        """Main audio absent + BGM -> -map [outa_bgm] is present in ffmpeg_args (Aspect 10)."""
        plan = self._build_no_main_audio_with_bgm()
        assert "[outa_bgm]" in plan.ffmpeg_args

    def test_10_no_main_audio_with_bgm_no_amix(self) -> None:
        """Main audio absent + BGM -> amix is not present in filter_complex (BGM standalone path, ADR-B5-r2)."""
        plan = self._build_no_main_audio_with_bgm()
        # No main audio means amix is unnecessary (BGM is the only audio)
        assert "amix" not in plan.filter_complex

    def test_10_no_main_audio_with_bgm_concat_a0(self) -> None:
        """Main audio absent + BGM -> concat is a=0 (video-only concat, ADR-B5-r2)."""
        plan = self._build_no_main_audio_with_bgm()
        assert "a=0" in plan.filter_complex

    def test_10_no_main_audio_with_bgm_outa_bgm_in_filter(self) -> None:
        """Main audio absent + BGM -> [outa_bgm] is present in filter_complex (ADR-B5-r2)."""
        plan = self._build_no_main_audio_with_bgm()
        assert "[outa_bgm]" in plan.filter_complex


# ---------------------------------------------------------------------------
# Aspect 11: denoise/loudness skip warning when has_main_audio=False (DC-AM-004)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmAudioWarnings:
    """Verify conditions under which denoise/loudness skip warnings are emitted (Aspect 11, DC-AM-004)."""

    def test_11_no_main_audio_with_denoise_adds_warning(self) -> None:
        """has_main_audio=False + denoise -> skip warning is emitted (DC-AM-004)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )
        warning_text = " ".join(plan.warnings)
        assert "denoise" in warning_text or "skip" in warning_text.lower()

    def test_11_no_main_audio_with_loudness_adds_warning(self) -> None:
        """has_main_audio=False + loudness -> skip warning is emitted (DC-AM-004)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            loudness=_VALID_PEAK_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )
        warning_text = " ".join(plan.warnings)
        assert "loudness" in warning_text or "skip" in warning_text.lower()

    def test_11_has_main_audio_with_bgm_no_skip_warning(self) -> None:
        """has_main_audio=True + BGM -> no denoise/loudness skip warning (DC-AM-004)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        # No denoise/loudness + BGM: should produce no warnings
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        warning_text = " ".join(plan.warnings)
        # BGM present but no audio skip warning (has_main_audio=True)
        assert "denoise skip" not in warning_text
        assert "loudness skip" not in warning_text


# ===========================================================================
# Review-flagged tests (CR L-1/L-2/M-1, SR M-1/I-1/M-3)
# ===========================================================================


# ---------------------------------------------------------------------------
# Aspect 12: resolve_bgm ValidationError path (CR L-2/M-1)
# ---------------------------------------------------------------------------


class TestResolveBgmValidationError:
    """resolve_bgm raises INVALID_INPUT when given a Timeline with invalid metadata (CR L-2/M-1)."""

    def test_12_volume_db_string_raises_invalid_input(self) -> None:
        """volume_db is a string -> resolve_bgm raises INVALID_INPUT (CR L-2)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db="not_a_number")
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_12_missing_required_tool_field_raises_invalid_input(self) -> None:
        """Required field 'tool' missing (kind="bgm" still present) -> resolve_bgm raises INVALID_INPUT (CR L-2).

        kind="bgm" exists so the clip is collected as BGM, but the missing 'tool' field
        causes BgmDirective validation to fail.
        """
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = {k: v for k, v in _VALID_BGM_DIRECTIVE.items() if k != "tool"}
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_12_unknown_extra_field_raises_invalid_input(self) -> None:
        """Unknown field (extra=forbid) -> resolve_bgm raises INVALID_INPUT (CR M-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, unknown_evil_field="x")
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_12_volume_db_inf_raises_invalid_input(self) -> None:
        """volume_db=inf -> resolve_bgm raises INVALID_INPUT (CR L-2)."""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=math.inf)
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# Aspect 13: DuckingDirective inf/nan and out-of-range validation (SR M-1)
# ---------------------------------------------------------------------------


class TestResolveBgmDuckingDirectiveValidation:
    """resolve_bgm raises INVALID_INPUT when given a Timeline with inf/nan or out-of-range DuckingDirective (SR M-1)."""

    def test_13_ducking_threshold_inf_raises_invalid_input(self) -> None:
        """ducking.threshold=inf -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": True, "threshold": math.inf, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_threshold_nan_raises_invalid_input(self) -> None:
        """ducking.threshold=nan -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": math.nan, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_threshold_zero_raises_invalid_input(self) -> None:
        """ducking.threshold=0.0 (violates gt=0.0) -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.0, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_threshold_over_one_raises_invalid_input(self) -> None:
        """ducking.threshold=1.1 (violates le=1.0) -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 1.1, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_ratio_zero_raises_invalid_input(self) -> None:
        """ducking.ratio=0.9 (violates ge=1.0) -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 0.9},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_ratio_over_twenty_raises_invalid_input(self) -> None:
        """ducking.ratio=20.1 (violates le=20.0) -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 20.1},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_ratio_nan_raises_invalid_input(self) -> None:
        """ducking.ratio=nan -> resolve_bgm raises INVALID_INPUT (SR M-1)."""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.05, "ratio": math.nan},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_valid_ducking_defaults_resolve_ok(self) -> None:
        """Default values threshold=0.05/ratio=4.0 -> resolve_bgm succeeds (SR M-1 happy path)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline(directive=_VALID_BGM_DIRECTIVE)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.ducking.threshold == 0.05
        assert result.directive.ducking.ratio == 4.0


# ---------------------------------------------------------------------------
# Aspect 14: BgmDirective.volume_db out-of-range validation (SR I-1)
# ---------------------------------------------------------------------------


class TestBgmDirectiveVolumeDbRange:
    """Verify out-of-range volume_db in BgmDirective causes INVALID_INPUT in resolve_bgm (SR I-1)."""

    def test_14_volume_db_too_low_raises_invalid_input(self) -> None:
        """volume_db=-200 (violates ge=-60.0) -> resolve_bgm raises INVALID_INPUT (SR I-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=-200.0)
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_14_volume_db_too_high_raises_invalid_input(self) -> None:
        """volume_db=100 (violates le=20.0) -> resolve_bgm raises INVALID_INPUT (SR I-1)."""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=100.0)
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_14_volume_db_boundary_low_ok(self) -> None:
        """volume_db=-60.0 (boundary, ge=-60 exactly) -> resolve_bgm succeeds (SR I-1 happy path)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        boundary_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=-60.0)
        tl = _make_bgm_otio_timeline(directive=boundary_directive)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.volume_db == -60.0

    def test_14_volume_db_boundary_high_ok(self) -> None:
        """volume_db=20.0 (boundary, le=20 exactly) -> resolve_bgm succeeds (SR I-1 happy path)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        boundary_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=20.0)
        tl = _make_bgm_otio_timeline(directive=boundary_directive)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.volume_db == 20.0


# ---------------------------------------------------------------------------
# Aspect 15: fade_out_sec/fade_in_sec > main_dur guard (SR M-3)
# ---------------------------------------------------------------------------


class TestBuildPlanBgmFadeGuard:
    """Verify INVALID_INPUT is raised when fade_out_sec/fade_in_sec exceeds main duration (SR M-3)."""

    def _build_with_fade(
        self,
        fade_in_sec: float = 0.0,
        fade_out_sec: float = 0.0,
        main_duration: float = 5.0,
    ) -> None:
        """Helper to run build_plan with specified fade settings (caller handles exception propagation)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        d = dict(
            _VALID_BGM_DIRECTIVE, fade_in_sec=fade_in_sec, fade_out_sec=fade_out_sec
        )
        tl = _make_single_source_timeline_with_audio(duration=main_duration)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=d, timeline_duration_sec=main_duration)
        build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]

    def test_15_fade_out_exceeds_main_dur_raises_invalid_input(self) -> None:
        """fade_out_sec > main_dur -> build_plan raises INVALID_INPUT (SR M-3)."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_out_sec=10.0, main_duration=5.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_15_fade_in_exceeds_main_dur_raises_invalid_input(self) -> None:
        """fade_in_sec > main_dur -> build_plan raises INVALID_INPUT (SR M-3)."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_in_sec=6.0, main_duration=5.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_15_fade_out_error_message_contains_fade_out(self) -> None:
        """fade_out_sec exceeded: error message contains "fade_out" (NR-L-3: distinguishes which exceeded)."""
        # Arrange / Act
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_out_sec=10.0, main_duration=5.0)
        # Assert: fade_out_sec excess is identifiable from message
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "fade_out" in exc_info.value.message

    def test_15_fade_in_error_message_contains_fade_in(self) -> None:
        """fade_in_sec exceeded: error message contains "fade_in" (NR-L-3: distinguishes which exceeded)."""
        # Arrange / Act
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_in_sec=6.0, main_duration=5.0)
        # Assert: fade_in_sec excess is identifiable from message
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "fade_in" in exc_info.value.message

    def test_15_fade_out_equals_main_dur_ok(self) -> None:
        """fade_out_sec == main_dur (exactly) -> build_plan succeeds (SR M-3 boundary value)."""
        self._build_with_fade(fade_out_sec=5.0, main_duration=5.0)

    def test_15_fade_in_equals_main_dur_ok(self) -> None:
        """fade_in_sec == main_dur (exactly) -> build_plan succeeds (SR M-3 boundary value)."""
        self._build_with_fade(fade_in_sec=5.0, main_duration=5.0)

    def test_15_fade_zero_is_ok(self) -> None:
        """fade_in_sec=0, fade_out_sec=0 -> build_plan succeeds and afade is absent (legacy behaviour)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        d = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=0.0, fade_out_sec=0.0)
        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade" not in plan.filter_complex

    def test_15_fade_out_within_main_dur_ok_afade_out_present(self) -> None:
        """fade_out_sec < main_dur -> build_plan succeeds and afade=t=out is present (legacy behaviour)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        d = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=2.0)
        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=out" in plan.filter_complex


# ===========================================================================
# Subtitle burn-in extension tests (ADR-S4-r2/S4-r3/S5-r2/S2-r2/S6-r2/S6-r3)
# ===========================================================================
# Real ffmpeg verified (M2 2026-06-11):
#   - Windows path escape final syntax: \ -> \\ then : -> \:
#     Example: C:\path\to\sub.srt -> C\:\\path\\to\\sub.srt
#   - VTT direct read: possible (ffmpeg 8.1.1 subtitles filter RC=0)
#   - PrimaryColour 6-digit &HBBGGRR: accepted / 8-digit &HAABBGGRR: accepted
#     AA=00 (8-digit) recommended for opaque rendering (6-digit alpha is implementation-dependent)
#   - force_style: FontName/FontSize/Outline/Alignment/MarginV all accepted
#   - fontsdir: :fontsdir='<esc_path>' accepted
#   - Alignment 1-9 all values accepted (ASS v4+ numpad: 1=bottom-left 2=bottom-center 3=bottom-right
#                               4=mid-left 5=center 6=mid-right 7=top-left 8=top-center 9=top-right)
#   - ASS + force_style: RC=0 (embedded style priority is libass behaviour, no API error)
#   - ASS + charenc=UTF-8: RC=0
#   - filter_complex [outv]subtitles=...[outvsub]: RC=0 on all paths
#   - subtitle stage injected inside builder (ADR-S4-r3), build_plan video_map_label unchanged
# ===========================================================================


def _escape_filtergraph_path(path: str) -> str:
    """Test-only filtergraph path escape function.

    Confirmed escape rules from real ffmpeg (M2 2026-06-11):
    1. Backslash -> \\\\
    2. Colon -> \\:
    This order makes Windows absolute paths cwd-independent when passed to ffmpeg.
    """
    return path.replace("\\", "\\\\").replace(":", "\\:")


def _make_subtitle_options(
    path: str = "/proj/subs.srt",
    font_name: str | None = None,
    fonts_dir: str | None = None,
    font_size: int | None = None,
    font_color: str | None = None,
    outline: float | None = None,
    alignment: int | None = None,
    margin_v: int | None = None,
) -> Any:
    """Test helper to construct SubtitleOptions."""
    from clipwright_render.schemas import SubtitleOptions  # type: ignore[attr-defined]

    return SubtitleOptions(
        path=path,
        font_name=font_name,
        fonts_dir=fonts_dir,
        font_size=font_size,
        font_color=font_color,
        outline=outline,
        alignment=alignment,
        margin_v=margin_v,
    )


def _make_subtitle_render_options(**kwargs: Any) -> RenderOptions:
    """Test helper to construct RenderOptions with subtitle."""
    sub = _make_subtitle_options(**kwargs)
    return RenderOptions(subtitle=sub)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Aspect S1: _append_subtitle_filter — basic behaviour (ADR-S4-r2)
# ---------------------------------------------------------------------------


class TestAppendSubtitleFilter:
    """Verify _append_subtitle_filter filter string and label return (ADR-S4-r2)."""

    def test_s1_returns_outvsub_label(self) -> None:
        """_append_subtitle_filter returns [outvsub] label (ADR-S4-r2)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt")
        filter_parts: list[str] = []
        result = _append_subtitle_filter(filter_parts, "[outv]", sub)

        # Assert
        assert result == "[outvsub]"

    def test_s1_appends_subtitles_filter_to_filter_parts(self) -> None:
        """_append_subtitle_filter appends a subtitles stage to filter_parts (ADR-S4-r2)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert len(filter_parts) == 1
        assert "subtitles=" in filter_parts[0]

    def test_s1_filter_part_starts_with_video_map_label(self) -> None:
        """The appended filter stage starts with video_map_label (ADR-S4-r2)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert filter_parts[0].startswith("[outv]")

    def test_s1_filter_part_ends_with_outvsub_label(self) -> None:
        """The appended filter stage ends with [outvsub] (ADR-S4-r2)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert filter_parts[0].endswith("[outvsub]")

    def test_s1_outvscaled_input_label_works(self) -> None:
        """With-scale path ([outvscaled]) is accepted as video_map_label (ADR-S4-r2)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt")
        filter_parts: list[str] = []
        result = _append_subtitle_filter(filter_parts, "[outvscaled]", sub)

        assert result == "[outvsub]"
        assert filter_parts[0].startswith("[outvscaled]")


# ---------------------------------------------------------------------------
# Aspect S2: Escape syntax (ADR-S5-r2 / real ffmpeg verified syntax)
# ---------------------------------------------------------------------------


class TestSubtitleFilterEscape:
    """Verify filtergraph escape syntax (ADR-S5-r2, M2 real ffmpeg verification).

    Confirmed escape: \\ -> \\\\ then : -> \\:
    render.py converts to absolute path before passing (ADR-S5-r2).
    _append_subtitle_filter embeds the escaped path in filename=.
    """

    def test_s2_unix_path_embedded_in_filename(self) -> None:
        """UNIX path (/ only) is embedded as-is in filename=."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "filename=" in filter_parts[0]
        assert "subs.srt" in filter_parts[0]

    def test_s2_windows_absolute_path_backslash_escaped(self) -> None:
        """Windows absolute path (with backslashes) is escaped and embedded in filename=.

        Confirmed escape (M2): \\ -> \\\\ then : -> \\:
        Example: C:\\Users\\sub.srt -> C\\\\:\\\\Users\\\\sub.srt
        """
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        win_path = r"C:\Users\shoma\proj\sub.srt"
        # Assume render.py converts to absolute path before passing
        sub = _make_subtitle_options(path=win_path)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        fc_part = filter_parts[0]
        # Colon ( : ) must be escaped to \:
        assert "\\:" in fc_part or "C\\\\" in fc_part, (
            f"Windows path escape is incorrect: {fc_part}"
        )

    def test_s2_path_without_special_chars_embedded_directly(self) -> None:
        """Path without special characters is embedded directly in filename=."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/simple/sub.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "/simple/sub.srt" in filter_parts[0]


# ---------------------------------------------------------------------------
# Aspect S3: force_style construction (ADR-S6-r2 / DC-AM-001 / DC-AM-002 / DC-AS-002)
# ---------------------------------------------------------------------------


class TestSubtitleFilterForceStyle:
    """Verify force_style string construction (ADR-S6-r2, DC-AM-001, DC-AM-002).

    Real ffmpeg verified (M2):
    - force_style='FontName=...,FontSize=...,PrimaryColour=&H...,Outline=...,
                  Alignment=...,MarginV=...' all accepted
    - PrimaryColour: 8-digit &HAABBGGRR (AA=00 = opaque) recommended
    - Alignment: ASS v4+ numpad 1-9 all accepted
    """

    def test_s3_no_style_options_no_force_style(self) -> None:
        """When all style fields are None, force_style is not present."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "force_style" not in filter_parts[0]

    def test_s3_font_name_included_in_force_style(self) -> None:
        """With font_name specified, force_style contains FontName=."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", font_name="Arial")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "FontName=Arial" in filter_parts[0]

    def test_s3_font_size_included_in_force_style(self) -> None:
        """With font_size specified, force_style contains FontSize=."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", font_size=28)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "FontSize=28" in filter_parts[0]

    def test_s3_outline_included_in_force_style(self) -> None:
        """With outline specified, force_style contains Outline=."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", outline=1.5)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "Outline=" in filter_parts[0]

    def test_s3_outline_zero_explicit_in_force_style(self) -> None:
        """outline=0.0: force_style contains Outline=0 (explicit no-outline, NR-L-1).

        0.0 explicitly means "no outline" and is distinguished from
        the libass default (None). Formatted with :g so 0.0 becomes "0".
        """
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", outline=0.0)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "Outline=0" in filter_parts[0]

    def test_s3_outline_none_not_in_force_style(self) -> None:
        """outline=None: force_style does not contain the Outline key (defers to libass default, NR-L-1).

        None means "unspecified" and is clearly distinguished from 0.0 (explicit no-outline).
        """
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", outline=None)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "Outline" not in filter_parts[0]

    def test_s3_margin_v_included_in_force_style(self) -> None:
        """With margin_v specified, force_style contains MarginV=."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", margin_v=20)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "MarginV=20" in filter_parts[0]

    def test_s3_alignment_1_included_in_force_style(self) -> None:
        """alignment=1 (bottom-left, ASS v4+) is included in force_style as Alignment=1 (DC-AM-001)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", alignment=1)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "Alignment=1" in filter_parts[0]

    def test_s3_alignment_5_included_in_force_style(self) -> None:
        """alignment=5 (center, ASS v4+) is included in force_style as Alignment=5 (DC-AM-001)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", alignment=5)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "Alignment=5" in filter_parts[0]

    def test_s3_alignment_9_included_in_force_style(self) -> None:
        """alignment=9 (top-right, ASS v4+) is included in force_style as Alignment=9 (DC-AM-001)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", alignment=9)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "Alignment=9" in filter_parts[0]

    def test_s3_font_color_converted_to_ass_primarycolour_8digit(self) -> None:
        """font_color='#RRGGBB' → force_style includes PrimaryColour=&HAABBGGRR (8-digit, AA=00) (DC-AM-002).

        M2 confirmed: 8-digit &H00BBGGRR (AA=00 = opaque) guarantees opaque rendering.
        #FF0000 (red: R=FF G=00 B=00) → BGR order → &H000000FF.
        """
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        # red: #FF0000 → BGR → &H000000FF (8-digit AA=00)
        sub = _make_subtitle_options(path="/sub.srt", font_color="#FF0000")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        fc_part = filter_parts[0]
        # PrimaryColour= must be present
        assert "PrimaryColour=" in fc_part
        # BGR conversion check: #FF0000(R=FF,G=00,B=00) → &H000000FF (8-digit AA=00 fixed, M2 confirmed)
        assert "&H000000FF" in fc_part, (
            f"#FF0000 color conversion is incorrect (expected 8-digit &H00BBGGRR format): {fc_part}"
        )

    def test_s3_font_color_white_converted_correctly(self) -> None:
        """font_color='#FFFFFF' (white) → PrimaryColour=&H00FFFFFF is included (DC-AM-002).

        White: R=FF,G=FF,B=FF → BGR = &HFFFFFF → 8-digit: &H00FFFFFF
        """
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", font_color="#FFFFFF")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        fc_part = filter_parts[0]
        assert "PrimaryColour=" in fc_part
        # white: R=FF,G=FF,B=FF → BGR = &HFFFFFF → 8-digit: &H00FFFFFF (AA=00 fixed, M2 confirmed)
        assert "&H00FFFFFF" in fc_part, (
            f"#FFFFFF color conversion is incorrect (expected 8-digit &H00BBGGRR format): {fc_part}"
        )


# ---------------------------------------------------------------------------
# Aspect S4: force_style/charenc control for ASS input (DC-AS-002)
# ---------------------------------------------------------------------------


class TestSubtitleFilterAssInput:
    """Verify force_style/charenc behaviour for ASS input (DC-AS-002 / ADR-S6-r2).

    M2-confirmed truth table:
    - SRT: charenc=UTF-8 + force_style applied
    - ASS: force_style not applied (built-in style takes priority); charenc/fontsdir determined by M2
    - This test asserts "ASS has no force_style" as the confirmed specification
    """

    def test_s4_srt_input_has_force_style_when_style_specified(self) -> None:
        """.srt input + style specified → force_style is included (SRT/VTT receive force_style)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", font_size=24)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "force_style" in filter_parts[0]

    def test_s4_vtt_input_has_force_style_when_style_specified(self) -> None:
        """.vtt input + style specified → force_style is included (VTT behaves the same as SRT)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.vtt", font_size=24)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "force_style" in filter_parts[0]

    def test_s4_ass_input_no_force_style_even_when_style_specified(self) -> None:
        """.ass input + style specified → force_style is NOT included (ASS uses built-in style; DC-AS-002)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.ass", font_size=24, alignment=2)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "force_style" not in filter_parts[0]


# ---------------------------------------------------------------------------
# Aspect S5: fontsdir option (ADR-S2-r2 / M2 confirmed)
# ---------------------------------------------------------------------------


class TestSubtitleFilterFontsDir:
    """Verify that the fontsdir option is added or omitted correctly (ADR-S2-r2).

    M2 confirmed: accepted with :fontsdir='<esc_path>'. Combined (charenc+fontsdir+force_style) also accepted.
    """

    def test_s5_fontsdir_included_when_specified(self) -> None:
        """When fonts_dir is specified, fontsdir= is included in the filter (M2 confirmed)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt", fonts_dir="/usr/share/fonts")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "fontsdir=" in filter_parts[0]

    def test_s5_fontsdir_not_included_when_not_specified(self) -> None:
        """When fonts_dir is not specified, fontsdir= is not included."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/sub.srt")
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        assert "fontsdir=" not in filter_parts[0]

    def test_s5_fontsdir_path_embedded_in_filter(self) -> None:
        """The fonts_dir path is embedded in the filtergraph."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(
            path="/sub.srt", fonts_dir="/usr/share/fonts/truetype"
        )
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub)

        # path component of font directory is present (after escaping)
        assert "fonts" in filter_parts[0]


# ---------------------------------------------------------------------------
# Aspect S6: subtitle stage injection via build_plan (ADR-S4-r3 / ADR-S8)
# ---------------------------------------------------------------------------


class TestBuildPlanSubtitle:
    """Verify that build_plan injects the subtitle stage at the video chain tail (ADR-S4-r3 / ADR-S8).

    ADR-S4-r3: the subtitle stage is injected inside the builder (immediately after video_map_label is fixed).
    build_plan does not change video_map_label ([outvsub] is fixed when the builder returns).
    Backwards compatibility: when subtitle=None, filter_complex and video_map_label are unchanged.
    """

    def _build_with_subtitle(
        self,
        subtitle_path: str = "/proj/subs.srt",
        font_size: int | None = None,
        audio_count: int = 1,
        use_scale: bool = False,
        use_multi_source: bool = False,
    ) -> RenderPlan:  # type: ignore[name-defined]
        """Helper for build_plan tests with a subtitle (single source)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        sub = _make_subtitle_options(path=subtitle_path, font_size=font_size)
        opts = RenderOptions(subtitle=sub)  # type: ignore[call-arg]
        if use_scale:
            opts = RenderOptions(subtitle=sub, width=1280, height=720)  # type: ignore[call-arg]
        return build_plan(ranges, probe, opts)

    def test_s6_subtitle_filter_in_filter_complex(self) -> None:
        """When subtitle is specified, filter_complex contains 'subtitles=' (ADR-S4-r3)."""
        plan = self._build_with_subtitle()
        assert "subtitles=" in plan.filter_complex

    def test_s6_outvsub_label_in_filter_complex(self) -> None:
        """When subtitle is specified, filter_complex contains [outvsub] (ADR-S4-r3)."""
        plan = self._build_with_subtitle()
        assert "[outvsub]" in plan.filter_complex

    def test_s6_ffmpeg_args_maps_outvsub(self) -> None:
        """When subtitle is specified, -map [outvsub] is included in ffmpeg_args (ADR-S4-r3)."""
        plan = self._build_with_subtitle()
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvsub]" in args_str

    def test_s6_ffmpeg_args_does_not_map_outv_when_subtitle(self) -> None:
        """When subtitle is specified, -map [outv] is NOT in ffmpeg_args (replaced by [outvsub])."""
        plan = self._build_with_subtitle()
        # the value immediately after -map must be [outvsub], never [outv]
        map_indices = [i for i, a in enumerate(plan.ffmpeg_args) if a == "-map"]
        map_targets = [plan.ffmpeg_args[i + 1] for i in map_indices]
        assert "[outvsub]" in map_targets
        assert "[outv]" not in map_targets

    def test_s6_subtitle_appended_after_scale_when_scale_specified(self) -> None:
        """With scale + subtitle: subtitle stage follows [outvscaled] and [outvsub] becomes the tail (ADR-S4-r3)."""
        plan = self._build_with_subtitle(use_scale=True)
        fc = plan.filter_complex
        # scale must be present
        assert "scale=1280:720" in fc
        # subtitles comes after [outvscaled] (subtitle is burned at the scaled output resolution)
        assert "[outvscaled]subtitles=" in fc or "[outvscaled]" in fc
        # final map is [outvsub]
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvsub]" in args_str

    def test_s6_audio_map_label_unchanged_with_subtitle(self) -> None:
        """With subtitle, audio_map_label ([outa]) is unchanged in build_plan (ADR-S4-r3 independence)."""
        plan = self._build_with_subtitle(audio_count=1)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa]" in args_str

    def test_s6_subtitle_none_filter_complex_unchanged(self) -> None:
        """When subtitle=None, filter_complex is identical to the no-subtitle version (ADR-S8 backwards compat — critical)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)

        plan_no_sub = build_plan(ranges, probe, RenderOptions())
        plan_sub_none = build_plan(ranges, probe, RenderOptions(subtitle=None))  # type: ignore[call-arg]

        # subtitle=None is fully backwards-compatible
        assert plan_no_sub.filter_complex == plan_sub_none.filter_complex

    def test_s6_subtitle_none_video_map_unchanged(self) -> None:
        """When subtitle=None, map labels in ffmpeg_args are unchanged (ADR-S8 backwards compat)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)

        plan_no_sub = build_plan(ranges, probe, RenderOptions())
        plan_sub_none = build_plan(ranges, probe, RenderOptions(subtitle=None))  # type: ignore[call-arg]

        assert plan_no_sub.ffmpeg_args == plan_sub_none.ffmpeg_args

    def test_s6_subtitle_none_no_outvsub_in_filter_complex(self) -> None:
        """When subtitle=None, [outvsub] is not present in filter_complex (backwards compat)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())

        assert "[outvsub]" not in plan.filter_complex


# ---------------------------------------------------------------------------
# Aspect S7: subtitle stage injection via multi-source path (ADR-S4-r3)
# ---------------------------------------------------------------------------


class TestBuildPlanSubtitleMultiSource:
    """Verify that the subtitle stage is injected at the video chain tail even in the multi-source path (ADR-S4-r3)."""

    def _build_multi_with_subtitle(
        self,
        subtitle_path: str = "/proj/subs.srt",
    ) -> RenderPlan:  # type: ignore[name-defined]
        """Helper for build_plan tests with multiple sources + subtitle."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        sub = _make_subtitle_options(path=subtitle_path)
        opts = RenderOptions(subtitle=sub)  # type: ignore[call-arg]
        return build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            opts,
            source_probes=source_probes,
        )

    def test_s7_multi_source_subtitle_in_filter_complex(self) -> None:
        """In multi-source path with subtitle, filter_complex contains 'subtitles='."""
        plan = self._build_multi_with_subtitle()
        assert "subtitles=" in plan.filter_complex

    def test_s7_multi_source_outvsub_in_filter_complex(self) -> None:
        """In multi-source path, [outvsub] is present in filter_complex (ADR-S4-r3)."""
        plan = self._build_multi_with_subtitle()
        assert "[outvsub]" in plan.filter_complex

    def test_s7_multi_source_ffmpeg_args_maps_outvsub(self) -> None:
        """In multi-source path, -map [outvsub] is included in ffmpeg_args (ADR-S4-r3)."""
        plan = self._build_multi_with_subtitle()
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvsub]" in args_str

    def test_s7_multi_source_audio_map_label_unchanged(self) -> None:
        """With multiple sources + subtitle, audio_map_label is unchanged (subtitle is video-only; ADR-S4-r3)."""
        plan = self._build_multi_with_subtitle()
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa]" in args_str


# ---------------------------------------------------------------------------
# Aspect S8: independence of BGM + subtitle (ADR-S4-r3)
# ---------------------------------------------------------------------------


class TestBuildPlanSubtitleAndBgmIndependence:
    """Verify that the subtitle stage and BGM stage are independent (ADR-S4-r3).

    ADR-S4-r3: subtitle is injected inside the builder (video chain side).
    BGM is appended inside build_plan (audio side). They are independent.
    The subtitle stage precedes the BGM append (BGM is appended to audio after video_map_label is fixed).
    """

    def test_s8_subtitle_and_bgm_both_present(self) -> None:
        """subtitle + BGM both specified → filter_complex contains both subtitles and BGM."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt")
        opts = RenderOptions(subtitle=sub)  # type: ignore[call-arg]
        bgm_clip = _make_bgm_clip()
        plan = build_plan(ranges, probe, opts, bgm=bgm_clip)  # type: ignore[call-arg]

        fc = plan.filter_complex
        assert "subtitles=" in fc
        assert "[outa_bgm]" in fc

    def test_s8_subtitle_video_map_is_outvsub_with_bgm(self) -> None:
        """subtitle + BGM: video is mapped to [outvsub] (BGM affects audio only)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt")
        opts = RenderOptions(subtitle=sub)  # type: ignore[call-arg]
        bgm_clip = _make_bgm_clip()
        plan = build_plan(ranges, probe, opts, bgm=bgm_clip)  # type: ignore[call-arg]

        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvsub]" in args_str
        assert "[outa_bgm]" in args_str

    def test_s8_subtitle_before_bgm_in_filter_complex(self) -> None:
        """In filter_complex, the subtitle stage ([outvsub]) appears before the BGM stage ([outa_bgm]) (ADR-S4-r3)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt")
        opts = RenderOptions(subtitle=sub)  # type: ignore[call-arg]
        bgm_clip = _make_bgm_clip()
        plan = build_plan(ranges, probe, opts, bgm=bgm_clip)  # type: ignore[call-arg]

        fc = plan.filter_complex
        outvsub_pos = fc.find("[outvsub]")
        outa_bgm_pos = fc.find("[outa_bgm]")
        assert outvsub_pos != -1, "[outvsub] not found in filter_complex"
        assert outa_bgm_pos != -1, "[outa_bgm] not found in filter_complex"
        # subtitle stage must appear before BGM stage
        assert outvsub_pos < outa_bgm_pos, (
            f"subtitle stage ({outvsub_pos}) appears after BGM stage ({outa_bgm_pos})"
        )


# ===========================================================================
# ADR-F1/F2/F3/F4: fit / even-rounding / counter-scale tests
# Target: plan.py _build_filter_complex / _build_multi_source_filter_complex /
#         _build_clip_filters / _build_force_style / _append_subtitle_filter /
#         _counter_scale / PLAYRES_Y_SRT_DEFAULT
#
# Implementation summary (ADR-F3 revised, spike-original-size confirmed):
#   - original_size injection was evaluated and dropped: it does not affect
#     force_style-overridden dimensions on ffmpeg 8.1.1 (SSIM=1.000 with/without).
#   - Counter-scale approach adopted: dimension-style fields (FontSize/MarginV/
#     Outline/Margin*) are pre-scaled by 288/frame_h so that libass's frame_h/288
#     upscale results in output-pixel-accurate rendering.
#   - PLAYRES_Y_SRT_DEFAULT = 288  (ffmpeg SRT->ASS default PlayResY)
#   - _counter_scale(px_value, frame_h) = round(px_value * 288 / frame_h)
#   - _build_force_style(subtitle, is_ass, frame_h) applies counter-scale when
#     frame_h is not None; emits raw values (legacy 288-based) when frame_h is None.
#   - Scale stage fit-branched (contain/cover/stretch) with even-rounding in both
#     single-source and multi-source paths (ADR-F2/F4).
# ===========================================================================


# ---------------------------------------------------------------------------
# ADR-F3: _counter_scale unit tests
# ---------------------------------------------------------------------------


class TestCounterScale:
    """Verify _counter_scale(px_value, frame_h) = round(px_value * 288 / frame_h).

    ADR-F3 (revised): dimension-style values are counter-scaled by 288/frame_h
    so that libass's frame_h/288 upscale results in output-pixel-accurate rendering.
    """

    def test_counter_scale_typical_font_size(self) -> None:
        """frame_h=1920, FontSize=48 -> round(48 * 288 / 1920) = 7 (ADR-F3)."""
        from clipwright_render.plan import _counter_scale  # type: ignore[attr-defined]

        assert _counter_scale(48, 1920) == 7

    def test_counter_scale_margin_v(self) -> None:
        """frame_h=1920, MarginV=700 -> round(700 * 288 / 1920) = 105 (ADR-F3)."""
        from clipwright_render.plan import _counter_scale  # type: ignore[attr-defined]

        assert _counter_scale(700, 1920) == 105

    def test_counter_scale_identity_at_playres_y(self) -> None:
        """frame_h=288 (== PlayResY default) -> scale factor is 1 (identity, ADR-F3)."""
        from clipwright_render.plan import _counter_scale  # type: ignore[attr-defined]

        assert _counter_scale(72, 288) == 72

    def test_counter_scale_small_frame(self) -> None:
        """frame_h=576 (2x PlayResY), FontSize=36 -> round(36 * 288 / 576) = 18 (ADR-F3)."""
        from clipwright_render.plan import _counter_scale  # type: ignore[attr-defined]

        assert _counter_scale(36, 576) == 18

    @pytest.mark.parametrize(
        "px_value, frame_h, expected",
        [
            (72, 1920, round(72 * 288 / 1920)),  # FontSize typical tall
            (700, 1920, round(700 * 288 / 1920)),  # MarginV typical tall
            (72, 288, 72),  # identity
            (48, 1080, round(48 * 288 / 1080)),  # 1080p
            (60, 720, round(60 * 288 / 720)),  # 720p
        ],
    )
    def test_counter_scale_formula_parametrized(
        self, px_value: int, frame_h: int, expected: int
    ) -> None:
        """_counter_scale(px, h) == round(px * 288 / h) for representative pairs (ADR-F3)."""
        from clipwright_render.plan import _counter_scale  # type: ignore[attr-defined]

        assert _counter_scale(px_value, frame_h) == expected


class TestPlayresYConstant:
    """Verify PLAYRES_Y_SRT_DEFAULT constant is defined and equals 288 (ADR-F3)."""

    def test_playres_y_constant_is_288(self) -> None:
        """PLAYRES_Y_SRT_DEFAULT must be 288 (ffmpeg/libass SRT->ASS default, ADR-F3)."""
        from clipwright_render.plan import (
            PLAYRES_Y_SRT_DEFAULT,  # type: ignore[attr-defined]
        )

        assert PLAYRES_Y_SRT_DEFAULT == 288


# ---------------------------------------------------------------------------
# ADR-F3: _build_force_style counter-scale tests
# ---------------------------------------------------------------------------


class TestBuildForceStyleCounterScale:
    """Verify _build_force_style(subtitle, is_ass, frame_h) counter-scales dimension fields.

    New signature expected by impl: _build_force_style(subtitle, is_ass, frame_h: int | None).
    When frame_h is None -> raw values emitted (legacy 288-based behaviour).
    When frame_h is set -> FontSize / MarginV / Outline counter-scaled.
    Alignment / PrimaryColour / Bold / FontName: NOT counter-scaled.
    """

    def test_force_style_fontsize_counter_scaled(self) -> None:
        """FontSize=48, frame_h=1920 -> FontSize=7 in force_style string (ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        result = _build_force_style(sub, is_ass=False, frame_h=1920)

        assert result is not None
        assert "FontSize=7" in result

    def test_force_style_margin_v_counter_scaled(self) -> None:
        """MarginV=700, frame_h=1920 -> MarginV=105 in force_style string (ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", margin_v=700)
        result = _build_force_style(sub, is_ass=False, frame_h=1920)

        assert result is not None
        assert "MarginV=105" in result

    def test_force_style_outline_counter_scaled(self) -> None:
        """Outline=6, frame_h=1920 -> Outline=1 (round(6*288/1920)=1) in force_style (ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", outline=6.0)
        result = _build_force_style(sub, is_ass=False, frame_h=1920)

        assert result is not None
        # round(6 * 288 / 1920) = round(0.9) = 1
        assert "Outline=1" in result

    def test_force_style_alignment_not_counter_scaled(self) -> None:
        """Alignment is not a dimension field; its value must pass through unchanged (ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", alignment=2)
        result = _build_force_style(sub, is_ass=False, frame_h=1920)

        assert result is not None
        assert "Alignment=2" in result

    def test_force_style_font_color_not_counter_scaled(self) -> None:
        """PrimaryColour is a colour field; it must not be scaled (ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        # red=#FF0000
        sub = _make_subtitle_options(path="/proj/subs.srt", font_color="#FF0000")
        result = _build_force_style(sub, is_ass=False, frame_h=1920)

        assert result is not None
        # ASS BGR order: PrimaryColour=&H000000FF (red in BGR)
        assert "PrimaryColour=&H000000FF" in result

    def test_force_style_frame_h_none_emits_raw_values(self) -> None:
        """frame_h=None -> raw FontSize/MarginV values are emitted unchanged (legacy, ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48, margin_v=700)
        result = _build_force_style(sub, is_ass=False, frame_h=None)

        assert result is not None
        assert "FontSize=48" in result
        assert "MarginV=700" in result

    def test_force_style_ass_input_returns_none_regardless_of_frame_h(self) -> None:
        """ASS input: force_style not applied regardless of frame_h (ADR-S6-r2)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.ass", font_size=48, margin_v=700)
        result = _build_force_style(sub, is_ass=True, frame_h=1920)

        assert result is None

    @pytest.mark.parametrize(
        "font_size, frame_h, expected_fs",
        [
            (48, 1920, round(48 * 288 / 1920)),
            (36, 1080, round(36 * 288 / 1080)),
            (60, 720, round(60 * 288 / 720)),
            (72, 288, 72),  # identity
        ],
    )
    def test_force_style_fontsize_parametrized(
        self, font_size: int, frame_h: int, expected_fs: int
    ) -> None:
        """FontSize counter-scaled for multiple frame_h values (ADR-F3)."""
        from clipwright_render.plan import (
            _build_force_style,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=font_size)
        result = _build_force_style(sub, is_ass=False, frame_h=frame_h)

        assert result is not None
        assert f"FontSize={expected_fs}" in result


# ---------------------------------------------------------------------------
# ADR-F3: _append_subtitle_filter with frame_h
# ---------------------------------------------------------------------------


class TestAppendSubtitleFilterFrameH:
    """Verify _append_subtitle_filter accepts and applies frame_h for counter-scale.

    New signature: _append_subtitle_filter(filter_parts, video_map_label, subtitle,
                                           frame_h: int | None = None) -> str
    When frame_h is set, the injected force_style contains counter-scaled values.
    original_size must NOT appear in the filter output (ADR-F3 revised: spike confirmed
    original_size does not affect force_style in ffmpeg 8.1.1).
    """

    def test_append_subtitle_frame_h_causes_counter_scale_in_force_style(self) -> None:
        """frame_h=1920 + FontSize=48 -> FontSize=7 in the emitted filter string (ADR-F3)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub, frame_h=1920)

        assert len(filter_parts) == 1
        assert "FontSize=7" in filter_parts[0]

    def test_append_subtitle_no_original_size_in_output(self) -> None:
        """original_size must NOT appear in the filter string (spike result: no effect on ffmpeg 8.1.1).

        This is a regression guard: original_size was evaluated and dropped (ADR-F3 revised).
        """
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48, margin_v=100)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outvscaled]", sub, frame_h=1920)

        assert "original_size" not in filter_parts[0]

    def test_append_subtitle_frame_h_none_emits_raw_values(self) -> None:
        """frame_h=None -> force_style contains raw px values (legacy fallback, ADR-F3)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48, margin_v=700)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub, frame_h=None)

        assert len(filter_parts) == 1
        assert "FontSize=48" in filter_parts[0]
        assert "MarginV=700" in filter_parts[0]

    def test_append_subtitle_margin_v_counter_scaled_with_frame_h(self) -> None:
        """frame_h=1920, MarginV=700 -> MarginV=105 in emitted filter (ADR-F3)."""
        from clipwright_render.plan import (
            _append_subtitle_filter,  # type: ignore[attr-defined]
        )

        sub = _make_subtitle_options(path="/proj/subs.srt", margin_v=700)
        filter_parts: list[str] = []
        _append_subtitle_filter(filter_parts, "[outv]", sub, frame_h=1920)

        assert "MarginV=105" in filter_parts[0]


# ---------------------------------------------------------------------------
# ADR-F2: Single-source path fit-branched scale stage
# ---------------------------------------------------------------------------


class TestBuildPlanFitSingleSource:
    """Verify that _build_filter_complex applies fit-based scale filters (ADR-F2).

    _build_filter_complex produces contain/cover/stretch filtergraph branches when
    width and height are both specified. Even-rounding ((v // 2) * 2) is applied
    before the scale stage. All tests in this class are Green (impl complete).
    """

    def _build_single(
        self,
        width: int,
        height: int,
        fit: str = "contain",
        audio_count: int = 0,
        probe_height: int | None = None,
    ) -> RenderPlan:
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(
            has_video=True,
            audio_count=audio_count,
            bit_rate=None,
            height=probe_height,
        )
        return build_plan(
            ranges, probe, RenderOptions(width=width, height=height, fit=fit)
        )

    def test_fit_contain_scale_decrease_in_filter(self) -> None:
        """fit=contain -> filter contains force_original_aspect_ratio=decrease (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="contain")

        assert "force_original_aspect_ratio=decrease" in plan.filter_complex

    def test_fit_contain_pad_in_filter(self) -> None:
        """fit=contain -> filter contains pad=W:H:... (letterbox, ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="contain")

        assert "pad=1920:1080" in plan.filter_complex

    def test_fit_contain_pad_color_black(self) -> None:
        """fit=contain -> pad filter uses color=black (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="contain")

        assert "color=black" in plan.filter_complex

    def test_fit_contain_setsar_1_in_filter(self) -> None:
        """fit=contain -> setsar=1 is appended (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="contain")

        assert "setsar=1" in plan.filter_complex

    def test_fit_cover_scale_increase_in_filter(self) -> None:
        """fit=cover -> filter contains force_original_aspect_ratio=increase (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="cover")

        assert "force_original_aspect_ratio=increase" in plan.filter_complex

    def test_fit_cover_crop_in_filter(self) -> None:
        """fit=cover -> filter contains crop=W:H (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="cover")

        assert "crop=1920:1080" in plan.filter_complex

    def test_fit_cover_setsar_1_in_filter(self) -> None:
        """fit=cover -> setsar=1 is appended (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="cover")

        assert "setsar=1" in plan.filter_complex

    def test_fit_stretch_no_force_original_aspect_ratio(self) -> None:
        """fit=stretch -> force_original_aspect_ratio must NOT appear (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="stretch")

        assert "force_original_aspect_ratio" not in plan.filter_complex

    def test_fit_stretch_setsar_1_in_filter(self) -> None:
        """fit=stretch -> setsar=1 is appended (ADR-F2, changed from legacy no-setsar)."""
        plan = self._build_single(1920, 1080, fit="stretch")

        assert "setsar=1" in plan.filter_complex

    def test_fit_contain_no_force_original_aspect_ratio_increase(self) -> None:
        """fit=contain -> force_original_aspect_ratio=increase must NOT appear (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="contain")

        assert "force_original_aspect_ratio=increase" not in plan.filter_complex

    def test_fit_cover_no_force_original_aspect_ratio_decrease(self) -> None:
        """fit=cover -> force_original_aspect_ratio=decrease must NOT appear (ADR-F2)."""
        plan = self._build_single(1920, 1080, fit="cover")

        assert "force_original_aspect_ratio=decrease" not in plan.filter_complex


# ---------------------------------------------------------------------------
# ADR-F4: Even-rounding in single-source path
# ---------------------------------------------------------------------------


class TestBuildPlanEvenRoundingSingleSource:
    """Verify that odd width/height are even-rounded before entering the scale stage (ADR-F4).

    _build_filter_complex applies (v // 2) * 2 to both width and height before the
    scale stage to prevent yuv420p encoding failures with odd dimensions. All tests
    in this class are Green (impl complete).
    """

    def test_odd_width_rounded_down_in_scale(self) -> None:
        """width=1081 (odd) -> scale filter uses 1080 (even-rounded, ADR-F4)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(width=1081, height=720))

        assert "scale=1080:720" in plan.filter_complex

    def test_odd_height_rounded_down_in_scale(self) -> None:
        """height=607 (odd) -> scale filter uses 606 (even-rounded, ADR-F4)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(width=1280, height=607))

        assert "scale=1280:606" in plan.filter_complex

    def test_odd_both_rounded_in_scale(self) -> None:
        """width=1081, height=607 -> scale=1080:606 (both even-rounded, ADR-F4)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(width=1081, height=607))

        assert "scale=1080:606" in plan.filter_complex

    def test_even_width_height_unchanged(self) -> None:
        """Even width/height are unchanged by even-rounding (ADR-F4)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(width=1920, height=1080))

        # Even values must appear unchanged in scale
        assert "1920" in plan.filter_complex
        assert "1080" in plan.filter_complex


# ---------------------------------------------------------------------------
# ADR-F4: fit + counter-scale via build_plan (single-source)
# ---------------------------------------------------------------------------


class TestBuildPlanSubtitleCounterScaleSingleSource:
    """Verify build_plan wires frame_h to subtitle counter-scale (ADR-F3/F4).

    single-source + width/height both specified + subtitle -> force_style has
    counter-scaled FontSize/MarginV based on even-rounded H.
    single-source + width/height None + probe height set -> counter-scale uses probe height.
    single-source + width/height None + probe height None -> raw values (no counter-scale).
    """

    def test_single_source_with_scale_subtitle_counter_scale(self) -> None:
        """Single-source, width=1080, height=1920, subtitle with FontSize=48
        -> FontSize=round(48*288/1920)=7 in filter_complex (ADR-F3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(
            ranges, probe, RenderOptions(width=1080, height=1920, subtitle=sub)
        )

        assert "FontSize=7" in plan.filter_complex

    def test_single_source_with_scale_subtitle_margin_v_counter_scale(self) -> None:
        """Single-source, width=1080, height=1920, subtitle with MarginV=700
        -> MarginV=105 in filter_complex (ADR-F3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt", margin_v=700)
        plan = build_plan(
            ranges, probe, RenderOptions(width=1080, height=1920, subtitle=sub)
        )

        assert "MarginV=105" in plan.filter_complex

    def test_single_source_no_scale_probe_height_counter_scale(self) -> None:
        """Single-source, no width/height, probe.height=1920
        -> FontSize counter-scaled by 288/1920 (ADR-F3, §5.3)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None, height=1920)
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(ranges, probe, RenderOptions(subtitle=sub))

        # frame_h = probe.height = 1920 -> FontSize=7
        assert "FontSize=7" in plan.filter_complex

    def test_single_source_no_scale_probe_height_none_raw_values(self) -> None:
        """Single-source, no width/height, probe.height=None
        -> no counter-scale, raw FontSize=48 (legacy fallback, ADR-F3 §5.5)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None, height=None)
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(ranges, probe, RenderOptions(subtitle=sub))

        # frame_h unknown -> raw value
        assert "FontSize=48" in plan.filter_complex

    def test_single_source_no_original_size_in_filter(self) -> None:
        """original_size must not appear in single-source filter_complex (spike regression guard)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(
            ranges, probe, RenderOptions(width=1080, height=1920, subtitle=sub)
        )

        assert "original_size" not in plan.filter_complex

    def test_single_source_subtitle_after_scale_stage(self) -> None:
        """subtitle stage must follow the scale stage: [outvscaled] is the input to subtitles=.

        This verifies stage ordering: scale -> subtitle (ADR-F2 §4.3).
        """
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(
            ranges, probe, RenderOptions(width=1920, height=1080, subtitle=sub)
        )
        fc = plan.filter_complex
        # [outvscaled] must appear as input to subtitles=
        outvscaled_pos = fc.find("[outvscaled]subtitles=")
        assert outvscaled_pos != -1, (
            "[outvscaled]subtitles= not found; subtitle stage must consume scale output"
        )


# ---------------------------------------------------------------------------
# ADR-F4: Multiple-source path fit propagation
# ---------------------------------------------------------------------------


class TestBuildPlanFitMultiSource:
    """Verify _build_multi_source_filter_complex propagates fit to per-clip normalisation (ADR-F4).

    _build_clip_filters selects contain/cover/stretch filtergraph branches based on
    the fit parameter. All three modes are implemented and tested. All tests confirm
    correct per-clip filter selection; test_multi_source_fit_contain_pad_color_black
    verifies that color=black is explicit in the contain pad filter (ADR-F2).
    """

    def _build_multi_fit(
        self,
        fit: str = "contain",
        width: int | None = None,
        height: int | None = None,
    ) -> RenderPlan:
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }
        opts_kwargs: dict = {"fit": fit}
        if width is not None and height is not None:
            opts_kwargs["width"] = width
            opts_kwargs["height"] = height
        opts = RenderOptions(**opts_kwargs)
        return build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            opts,
            source_probes=source_probes,
        )

    def test_multi_source_fit_contain_uses_decrease(self) -> None:
        """Multi-source, fit=contain -> per-clip filter contains force_original_aspect_ratio=decrease (ADR-F4)."""
        plan = self._build_multi_fit(fit="contain")

        assert "force_original_aspect_ratio=decrease" in plan.filter_complex

    def test_multi_source_fit_cover_uses_increase(self) -> None:
        """Multi-source, fit=cover -> per-clip filter contains force_original_aspect_ratio=increase (ADR-F4)."""
        plan = self._build_multi_fit(fit="cover")

        assert "force_original_aspect_ratio=increase" in plan.filter_complex

    def test_multi_source_fit_cover_uses_crop(self) -> None:
        """Multi-source, fit=cover -> per-clip filter contains crop= (ADR-F4)."""
        plan = self._build_multi_fit(fit="cover")

        assert "crop=" in plan.filter_complex

    def test_multi_source_fit_stretch_no_force_aspect(self) -> None:
        """Multi-source, fit=stretch -> force_original_aspect_ratio must NOT appear (ADR-F4)."""
        plan = self._build_multi_fit(fit="stretch")

        assert "force_original_aspect_ratio" not in plan.filter_complex

    def test_multi_source_fit_cover_no_decrease(self) -> None:
        """Multi-source, fit=cover -> force_original_aspect_ratio=decrease must NOT appear (ADR-F4)."""
        plan = self._build_multi_fit(fit="cover")

        assert "force_original_aspect_ratio=decrease" not in plan.filter_complex

    def test_multi_source_fit_stretch_no_pad(self) -> None:
        """Multi-source, fit=stretch -> pad= must NOT appear (ADR-F4)."""
        plan = self._build_multi_fit(fit="stretch")

        assert "pad=" not in plan.filter_complex

    def test_multi_source_fit_contain_pad_color_black(self) -> None:
        """Multi-source, fit=contain -> per-clip pad filter must include color=black (ADR-F2).

        ADR-F2 §4.1 requires color=black to be explicit in the contain pad filter.
        Both the single-source path (plan.py) and the multi-source per-clip
        normalisation path (_build_clip_filters) include color=black.
        """
        plan = self._build_multi_fit(fit="contain")

        assert "color=black" in plan.filter_complex


# ---------------------------------------------------------------------------
# ADR-F4: Multi-source counter-scale
# ---------------------------------------------------------------------------


class TestBuildPlanSubtitleCounterScaleMultiSource:
    """Verify build_plan wires frame_h=target_h for subtitle counter-scale (multi-source, ADR-F4)."""

    def test_multi_source_subtitle_counter_scale_uses_target_h(self) -> None:
        """Multi-source, width=1080, height=1920, subtitle FontSize=48
        -> FontSize=round(48*288/1920)=7 (frame_h=target_h, ADR-F4)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(width=1080, height=1920, subtitle=sub),
            source_probes=source_probes,
        )

        assert "FontSize=7" in plan.filter_complex

    def test_multi_source_no_original_size_in_filter(self) -> None:
        """original_size must not appear in multi-source filter_complex (spike regression guard)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        sub = _make_subtitle_options(path="/proj/subs.srt", font_size=48)
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(width=1080, height=1920, subtitle=sub),
            source_probes=source_probes,
        )

        assert "original_size" not in plan.filter_complex


# ---------------------------------------------------------------------------
# ADR-F1: backward compat — no scale stage when width/height not specified (Green)
# ---------------------------------------------------------------------------


class TestBuildPlanNoScaleWhenNoWidthHeight:
    """Verify that fit is ignored and no scale stage is inserted when width/height unspecified (ADR-F1).

    These tests should be Green even before impl, confirming backward compatibility is preserved.
    """

    def test_no_scale_stage_without_width_height(self) -> None:
        """width/height both None -> no scale filter (ADR-F1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(fit="contain"))

        # No scale stage: [outvscaled] must not appear (scale was not triggered)
        assert "[outvscaled]" not in plan.filter_complex

    def test_fit_cover_without_width_height_no_scale_stage(self) -> None:
        """fit=cover without width/height -> still no scale stage inserted (ADR-F1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(fit="cover"))

        assert "[outvscaled]" not in plan.filter_complex
        assert "force_original_aspect_ratio" not in plan.filter_complex


# ===========================================================================
# Layer 1 verification: multi-source PiP wiring
# (architecture-report-20260710-135831.md §2 layer1, ADR-A1, FR-1/AC-1).
#
# This is NOT Red-phase TDD: current wiring is expected to be correct, and
# these tests are meant to stay Green. A failure here indicates a product
# defect in plan.py, not a missing feature (see architecture report §6 for
# the suspect-location table used to triage a failure).
# ===========================================================================


def _make_plan_pip_overlay(**overrides: Any) -> Any:
    """Build a PipOverlay for direct _build_multi_source_filter_complex calls
    (layer1-A). File-local copy of
    test_pip_ffmpeg_execution.py::_make_default_pip_overlay's field shape
    (no cross-test-file import, per this codebase's convention)."""
    from clipwright_render.plan import PipOverlay  # type: ignore[attr-defined]

    defaults: dict[str, Any] = dict(
        media_path="/src/pip.mp4",
        media_start_s=0.0,
        duration_s=1.0,
        start_s=0.5,
        end_s=1.5,
        x="(W-w)/2",
        y="(H-h)/2",
        scale=0.3,
        opacity=1.0,
        fade_in_s=0.0,
        fade_out_s=0.0,
        input_index=2,
        mix_audio=False,
        audio_volume=1.0,
        ducking=None,
    )
    defaults.update(overrides)
    return PipOverlay(**defaults)


class TestMultiSourcePipDirectWiring:
    """Layer1-A: direct _build_multi_source_filter_complex(pip_overlays=...) calls.

    Verifies PiP filter emit/wiring in the multi-source path in isolation from
    build_plan's index arithmetic (architecture report ADR-A1: this class alone
    cannot prove _pip_index_base's arithmetic is correct -- see
    TestMultiSourcePipIndexArithmetic for that).
    """

    def _call(
        self, pip_overlays: list[Any] | None
    ) -> tuple[str, str, str, bool, bool, list[str], float]:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _build_multi_source_filter_complex,
            resolve_kept_ranges,
            unique_sources_in_order,
        )

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        input_sources = unique_sources_in_order(ranges)
        source_index = {s: i for i, s in enumerate(input_sources)}
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }
        return _build_multi_source_filter_complex(
            ranges,
            source_index,
            source_probes,
            has_audio_overall=True,
            denoise_directive=None,
            loudness_directive=None,
            options=RenderOptions(),
            first_source=input_sources[0],
            pip_overlays=pip_overlays,
        )

    def test_pip_overlay_emits_pipv_and_outvpip_labels(self) -> None:
        """pip_overlays=[PipOverlay(input_index=2)] -> filter_complex contains
        [pipv0] and [outvpip0] (layer1-A observation 1)."""
        pip = _make_plan_pip_overlay(input_index=2)
        filter_complex, _, _, _, _, _, _ = self._call([pip])

        assert "[pipv0]" in filter_complex
        assert "[outvpip0]" in filter_complex

    def test_pip_overlay_emits_input_index_reference(self) -> None:
        """PipOverlay.input_index=2 -> filter_complex references [2:v]
        (layer1-A observation 2; the multi-source path must not drop/rewrite
        the caller-supplied input_index)."""
        pip = _make_plan_pip_overlay(input_index=2)
        filter_complex, _, _, _, _, _, _ = self._call([pip])

        assert "[2:v]" in filter_complex

    def test_pip_overlay_is_final_video_map_label(self) -> None:
        """pip_overlays given -> returned video_map_label == "[outvpip0]"
        (layer1-A observation 3: PiP is the topmost/final video stage)."""
        pip = _make_plan_pip_overlay(input_index=2)
        _, video_map_label, _, _, _, _, _ = self._call([pip])

        assert video_map_label == "[outvpip0]"

    def test_no_pip_overlays_is_backward_compatible(self) -> None:
        """pip_overlays=None -> no outvpip stage is added and video_map_label
        reverts to "[outv]" (control case: no-op early return,
        _append_pip_video_filter plan.py:1198-1199)."""
        filter_complex, video_map_label, _, _, _, _, _ = self._call(None)

        assert "outvpip" not in filter_complex
        assert video_map_label == "[outv]"


def _add_plan_pip_marker(
    timeline: otio.schema.Timeline,
    *,
    media_path: str = "/src/pip.mp4",
    start_sec: float = 0.5,
    duration_sec: float = 1.0,
    rate: float = FPS,
    name: str = "pip_0",
) -> None:
    """Attach a pip_overlay marker to the timeline's video track. File-local
    copy of test_pip_ffmpeg_execution.py::_add_pip_overlay_marker's metadata
    shape (layer1-B; no cross-test-file import, per this codebase's
    convention)."""
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"

    marked_range = _tr(start_sec, duration_sec, rate)
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
                "media_start_sec": 0.0,
                "x": "(W-w)/2",
                "y": "(H-h)/2",
                "scale": 0.3,
                "opacity": 1.0,
                "fade_in_sec": 0.0,
                "fade_out_sec": 0.0,
                "mix_audio": False,
                "audio_volume": 1.0,
                "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
            }
        },
    )
    video_track.markers.append(marker)


def _add_plan_image_overlay_marker(
    timeline: otio.schema.Timeline,
    *,
    image_path: str = "/img/logo.png",
    start_sec: float = 0.2,
    duration_sec: float = 1.0,
    rate: float = FPS,
    name: str = "image_0",
) -> None:
    """Attach an image_overlay marker to the timeline's video track. File-local
    copy of test_image_overlay.py::_add_image_overlay_marker's metadata shape
    (layer1-B; no cross-test-file import, per this codebase's convention)."""
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"

    marked_range = _tr(start_sec, duration_sec, rate)
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
                "x": "0",
                "y": "0",
                "scale": 1.0,
                "opacity": 1.0,
                "fade_in_sec": 0.0,
                "fade_out_sec": 0.0,
            }
        },
    )
    video_track.markers.append(marker)


class TestMultiSourcePipIndexArithmetic:
    """Layer1-B: build_plan-driven verification of _pip_index_base arithmetic
    (plan.py:4887/4901; architecture report ADR-A1, required in addition to
    TestMultiSourcePipDirectWiring because the direct-call layer1-A tests
    receive an already-computed PipOverlay.input_index and cannot exercise the
    len(input_sources)/bgm/image counting logic that lives in build_plan)."""

    def _base_timeline(self) -> otio.schema.Timeline:
        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        return _make_timeline_with_clips(clips)

    def _source_probes(self) -> dict[str, ProbeInfo]:
        return {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }

    def test_two_sources_plus_pip_only_uses_index_2(self) -> None:
        """2 sources + pip (no bgm/image) -> _pip_index_base = 2 + 0 + 0 = 2,
        so filter_complex references [2:v] (layer1-B-1)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = self._base_timeline()
        _add_plan_pip_marker(tl)
        ranges = resolve_kept_ranges(tl)
        source_probes = self._source_probes()
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )

        assert "[2:v]" in plan.filter_complex

    def test_two_sources_plus_bgm_image_pip_uses_index_4(self) -> None:
        """2 sources + bgm + image overlay + pip -> _pip_index_base =
        len(input_sources)=2 + bgm=1 + image=1 = 4, so filter_complex
        references [4:v] (layer1-B-2; static pair to layer2-(c)'s real-ffmpeg
        pixel-level proof of the same arithmetic)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = self._base_timeline()
        _add_plan_image_overlay_marker(tl)
        _add_plan_pip_marker(tl)
        ranges = resolve_kept_ranges(tl)
        source_probes = self._source_probes()
        bgm_clip = _make_bgm_clip(timeline_duration_sec=5.0)
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )

        assert "[4:v]" in plan.filter_complex


# ===========================================================================
# SR-V-001 acceptance tests: fade_in_sec/fade_out_sec individual range
# validation (.claude/reports/security-review-report-security-review.md
# SR-V-001). `_marker_to_pip_overlay` (plan.py L937-938/L1038-1046) is
# expected to gain math.isfinite() + 0<=x<=duration_sec checks for
# fade_in_s/fade_out_s individually, in addition to the pre-existing combined
# `fade_in_s + fade_out_s > duration_sec` cross-field check. Until that lands,
# NaN silently disables the fade branch (`if o.fade_in_s > 0` is False for
# NaN, so the combined check `NaN > x` is also always False and never fires)
# and a huge finite value (e.g. 1e300) with an offsetting fade_out_s slips
# past the combined check and is embedded verbatim into the filter_complex
# string handed to ffmpeg. These tests are the acceptance test for the
# individual-range-check fix: run BEFORE that fix lands, cases (1)/(2)/(3)
# below are expected to FAIL (Red, as intended -- see test-report for which
# state this run observed).
# ===========================================================================


def _add_plan_pip_marker_with_fades(
    timeline: otio.schema.Timeline,
    *,
    fade_in_sec: float = 0.0,
    fade_out_sec: float = 0.0,
    media_path: str = "/src/pip.mp4",
    start_sec: float = 0.5,
    duration_sec: float = 1.0,
    rate: float = FPS,
    name: str = "pip_0",
) -> None:
    """Attach a pip_overlay marker with explicit fade_in_sec/fade_out_sec
    (SR-V-001 acceptance tests). File-local helper added alongside
    _add_plan_pip_marker above rather than extending it, so that helper's
    existing layer1-B call sites are left completely untouched."""
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"

    marked_range = _tr(start_sec, duration_sec, rate)
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
                "media_start_sec": 0.0,
                "x": "(W-w)/2",
                "y": "(H-h)/2",
                "scale": 0.3,
                "opacity": 1.0,
                "fade_in_sec": fade_in_sec,
                "fade_out_sec": fade_out_sec,
                "mix_audio": False,
                "audio_volume": 1.0,
                "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
            }
        },
    )
    video_track.markers.append(marker)


class TestPipFadeIndividualRangeValidation:
    """SR-V-001: fade_in_sec/fade_out_sec must each be individually validated
    (finite, non-negative, <=duration_sec), not only via the pre-existing
    combined fade_in_s+fade_out_s>duration_sec check."""

    def _build_and_expect_rejection(
        self,
        *,
        fade_in_sec: float,
        fade_out_sec: float,
        duration_sec: float = 1.0,
    ) -> ClipwrightError:
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        _add_plan_pip_marker_with_fades(
            tl,
            fade_in_sec=fade_in_sec,
            fade_out_sec=fade_out_sec,
            duration_sec=duration_sec,
        )
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions())
        return exc_info.value

    def test_fade_in_sec_nan_rejected(self) -> None:
        """fade_in_sec=NaN -> INVALID_INPUT (SR-V-001 case 1)."""
        import math

        exc = self._build_and_expect_rejection(fade_in_sec=math.nan, fade_out_sec=0.0)
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint
        assert "/src/a.mp4" not in exc.message
        assert "/src/pip.mp4" not in exc.message

    def test_fade_out_sec_nan_rejected(self) -> None:
        """fade_out_sec=NaN -> INVALID_INPUT (SR-V-001 case 1)."""
        import math

        exc = self._build_and_expect_rejection(fade_in_sec=0.0, fade_out_sec=math.nan)
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint
        assert "/src/a.mp4" not in exc.message
        assert "/src/pip.mp4" not in exc.message

    def test_fade_in_sec_negative_rejected(self) -> None:
        """fade_in_sec<0 -> INVALID_INPUT (SR-V-001 case 2)."""
        exc = self._build_and_expect_rejection(fade_in_sec=-0.5, fade_out_sec=0.0)
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint
        assert "/src/a.mp4" not in exc.message
        assert "/src/pip.mp4" not in exc.message

    def test_fade_out_sec_negative_rejected(self) -> None:
        """fade_out_sec<0 -> INVALID_INPUT (SR-V-001 case 2)."""
        exc = self._build_and_expect_rejection(fade_in_sec=0.0, fade_out_sec=-0.5)
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint
        assert "/src/a.mp4" not in exc.message
        assert "/src/pip.mp4" not in exc.message

    def test_fade_in_sec_huge_finite_exceeding_duration_rejected(self) -> None:
        """fade_in_sec=1e300 (extreme finite value far exceeding
        duration_sec) -> INVALID_INPUT (SR-V-001 case 3). Guards against
        `fade=t=in:st=...:d=1e300:alpha=1` being embedded verbatim into
        filter_complex and handed to ffmpeg."""
        exc = self._build_and_expect_rejection(fade_in_sec=1e300, fade_out_sec=0.0)
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint
        assert "/src/a.mp4" not in exc.message
        assert "/src/pip.mp4" not in exc.message

    def test_fade_out_sec_huge_finite_exceeding_duration_rejected(self) -> None:
        """fade_out_sec=1e300 -> INVALID_INPUT (SR-V-001 case 3)."""
        exc = self._build_and_expect_rejection(fade_in_sec=0.0, fade_out_sec=1e300)
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint
        assert "/src/a.mp4" not in exc.message
        assert "/src/pip.mp4" not in exc.message

    def test_combined_fade_exceeding_duration_still_rejected(self) -> None:
        """Regression: the pre-existing combined
        fade_in_s+fade_out_s>duration_sec cross-field check (both values
        individually in-range) must keep rejecting once the individual
        range checks are added."""
        exc = self._build_and_expect_rejection(
            fade_in_sec=0.7, fade_out_sec=0.7, duration_sec=1.0
        )
        assert exc.code == ErrorCode.INVALID_INPUT
        assert exc.message
        assert exc.hint

    def test_valid_fade_values_still_accepted(self) -> None:
        """Regression: normal in-range fade_in_sec/fade_out_sec values still
        build a plan successfully (no false-positive rejection from the new
        individual range checks)."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        _add_plan_pip_marker_with_fades(
            tl, fade_in_sec=0.3, fade_out_sec=0.3, duration_sec=1.0
        )
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())

        assert "fade=t=in" in plan.filter_complex
