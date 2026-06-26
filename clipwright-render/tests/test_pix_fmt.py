"""test_pix_fmt.py — Tests for the -pix_fmt yuv420p output option (D1 fix).

Verifies that build_plan always places exactly one adjacent "-pix_fmt" / "yuv420p"
pair in ffmpeg_args across every render path:
  - single-source (concat)
  - multi-source (concat without transition)
  - multi-source with transition (xfade)
  - hw encoder (resolved_encoder != None)

Plan A note: in Plan A, _build_ffmpeg_args() inserts -pix_fmt yuv420p once after the
-map group and before the sw/hw codec branch.  filter_complex is unchanged by this
fix, so existing filter_complex assertion tests remain valid without modification.
"""

from __future__ import annotations

from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.encoders import ResolvedEncoder
from clipwright_render.plan import KeptRange, ProbeInfo
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Constants / helpers (mirrors test_plan.py / test_transition_chain.py style)
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


def _make_timeline_with_clips(
    clips: list[otio.schema.Clip | otio.schema.Gap | otio.schema.Transition],
    track_kind: str = otio.schema.TrackKind.Video,
) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=track_kind)
    for item in clips:
        track.append(item)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


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


def _make_kept_range(
    source: str,
    start: float,
    duration: float,
    rate: float = FPS,
) -> KeptRange:
    return KeptRange(
        source=source,
        source_range=_tr(start, duration, rate),
    )


def _uniform_transition(
    type_: str = "dissolve",
    duration_sec: float = 0.5,
    n_clips: int = 2,
) -> dict[str, Any]:
    """Build a minimal uniform transition directive (all internal boundaries)."""
    transitions = [
        {"after_clip_index": i, "type": type_, "duration_sec": duration_sec}
        for i in range(n_clips - 1)
    ]
    return {
        "tool": "clipwright_add_transition",
        "version": "0.1.0",
        "kind": "transition",
        "transitions": transitions,
    }


def _assert_pix_fmt_adjacent(args: list[str]) -> None:
    """Assert that '-pix_fmt' is present exactly once and immediately followed by
    'yuv420p' (adjacency contract).

    Using index-adjacency rather than subset membership ensures there is no
    stray '-pix_fmt <other>' token and no orphaned 'yuv420p' elsewhere.
    """
    assert "-pix_fmt" in args, f"-pix_fmt not found in ffmpeg_args: {args}"
    i = args.index("-pix_fmt")
    assert args[i + 1] == "yuv420p", (
        f"Expected 'yuv420p' immediately after '-pix_fmt', "
        f"got {args[i + 1]!r}. Full args: {args}"
    )
    # Exactly one occurrence — no duplicate insertion
    assert args.count("-pix_fmt") == 1, (
        f"-pix_fmt appears {args.count('-pix_fmt')} times (expected 1). Full args: {args}"
    )


# ---------------------------------------------------------------------------
# 1. Single-source path (concat)
# ---------------------------------------------------------------------------


class TestPixFmtSingleSource:
    """Plan A: single-source (concat) path includes -pix_fmt yuv420p once."""

    def test_single_clip_pix_fmt_adjacent(self) -> None:
        """Single clip, no audio: ffmpeg_args contains adjacent -pix_fmt yuv420p."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0, width=None, height=None, fps=None)
        plan = build_plan(ranges, probe, RenderOptions())
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_multi_clip_single_source_pix_fmt_adjacent(self) -> None:
        """2 clips from the same source (concat n=2), with audio: -pix_fmt yuv420p once."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips(
            [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/a.mp4", 5.0, 2.0)]
        )
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=1, width=None, height=None, fps=None)
        plan = build_plan(ranges, probe, RenderOptions())
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_single_source_with_codec_option_pix_fmt_adjacent(self) -> None:
        """Single source + explicit video_codec: -pix_fmt yuv420p still present once."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0, width=None, height=None, fps=None)
        plan = build_plan(ranges, probe, RenderOptions(video_codec="libx264"))
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)


# ---------------------------------------------------------------------------
# 2. Multi-source path without transition (concat)
# ---------------------------------------------------------------------------


class TestPixFmtMultiSourceConcat:
    """Plan A: multi-source concat path includes -pix_fmt yuv420p once."""

    def _build_2source(
        self,
        has_audio: bool = True,
        options: RenderOptions | None = None,
    ) -> Any:
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=1 if has_audio else 0)
        probe_b = _make_probe(audio_count=1 if has_audio else 0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        return build_plan(
            ranges,
            probe_a,
            options or RenderOptions(),
            source_probes=source_probes,
        )

    def test_2source_with_audio_pix_fmt_adjacent(self) -> None:
        """Multi-source concat (2 sources, audio): -pix_fmt yuv420p present once."""
        plan = self._build_2source(has_audio=True)
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_2source_no_audio_pix_fmt_adjacent(self) -> None:
        """Multi-source concat (2 sources, no audio): -pix_fmt yuv420p present once."""
        plan = self._build_2source(has_audio=False)
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_2source_with_scale_option_pix_fmt_adjacent(self) -> None:
        """Multi-source concat + scale option: -pix_fmt yuv420p still present once."""
        plan = self._build_2source(options=RenderOptions(width=1280, height=720))
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_3source_clips_pix_fmt_adjacent(self) -> None:
        """3-clip multi-source timeline (concat): -pix_fmt yuv420p present once."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 2.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
            _make_clip("/src/a.mp4", 5.0, 1.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe()
        probe_b = _make_probe()
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
        )
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)


# ---------------------------------------------------------------------------
# 3. Multi-source path with transition (xfade)
# ---------------------------------------------------------------------------


class TestPixFmtMultiSourceTransition:
    """Plan A: multi-source xfade path includes -pix_fmt yuv420p once."""

    def test_2source_dissolve_transition_pix_fmt_adjacent(self) -> None:
        """Multi-source + dissolve xfade: -pix_fmt yuv420p present once in ffmpeg_args."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=0)
        probe_b = _make_probe(audio_count=0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
            transition=_uniform_transition(n_clips=2),  # type: ignore[call-arg]
        )
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_2source_with_audio_transition_pix_fmt_adjacent(self) -> None:
        """Multi-source + xfade + audio (acrossfade): -pix_fmt yuv420p present once."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=1)
        probe_b = _make_probe(audio_count=1)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
            transition=_uniform_transition(n_clips=2),  # type: ignore[call-arg]
        )
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_single_source_transition_pix_fmt_adjacent(self) -> None:
        """Single-source + xfade (2 clips): -pix_fmt yuv420p present once."""
        from clipwright_render.plan import build_plan

        ranges = [
            _make_kept_range("/src/a.mp4", 0.0, 3.0),
            _make_kept_range("/src/a.mp4", 10.0, 3.0),
        ]
        probe = _make_probe(audio_count=0)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            transition=_uniform_transition(n_clips=2),  # type: ignore[call-arg]
        )
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)


# ---------------------------------------------------------------------------
# 4. HW encoder path (resolved_encoder != None) — NFR-4 lock
# ---------------------------------------------------------------------------


class TestPixFmtHwEncoder:
    """Plan A: HW encoder (e.g. h264_nvenc) path includes -pix_fmt yuv420p once (NFR-4)."""

    def _make_nvenc_encoder(self) -> ResolvedEncoder:
        """Construct a stub ResolvedEncoder for h264_nvenc without querying ffmpeg."""
        return ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=["-cq", "19"],
            hwaccel_value="cuda",
            warnings=[],
        )

    def test_hw_single_source_pix_fmt_adjacent(self) -> None:
        """HW encoder, single source: -pix_fmt yuv420p present once after map group."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0, width=None, height=None, fps=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            resolved_encoder=self._make_nvenc_encoder(),  # type: ignore[call-arg]
        )
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_hw_multi_source_pix_fmt_adjacent(self) -> None:
        """HW encoder, multi-source concat: -pix_fmt yuv420p present once."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe_a = _make_probe(audio_count=0)
        probe_b = _make_probe(audio_count=0)
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
            resolved_encoder=self._make_nvenc_encoder(),  # type: ignore[call-arg]
        )
        _assert_pix_fmt_adjacent(plan.ffmpeg_args)

    def test_hw_encoder_name_still_present_alongside_pix_fmt(self) -> None:
        """HW path: encoder_name (-c:v h264_nvenc) co-exists with -pix_fmt yuv420p."""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe(audio_count=0, width=None, height=None, fps=None)
        enc = self._make_nvenc_encoder()
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            resolved_encoder=enc,  # type: ignore[call-arg]
        )
        args = plan.ffmpeg_args
        _assert_pix_fmt_adjacent(args)
        # HW encoder name must still appear
        assert "-c:v" in args
        idx_cv = args.index("-c:v")
        assert args[idx_cv + 1] == "h264_nvenc"
