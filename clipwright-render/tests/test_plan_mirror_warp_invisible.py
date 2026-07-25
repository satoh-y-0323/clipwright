"""test_plan_mirror_warp_invisible.py — Regression PIN for render's blindness
to NLE-mirror-clip speed effects (ADR-MS-6, architecture-report-20260725-100022.md
§2).

Scope (requirements-report-20260725-095859.md / ADR-MS-2 "render 不可視" claim):

  clipwright-speed's NLE mirror-sync feature (ADR-MS-1/ADR-MS-2) appends a
  clipwright-authored LinearTimeWarp effect to Resolve-mirrored Audio clips
  (A1..AN, produced by clipwright.nle_interop.conform_timeline_for_nle) so
  that Resolve's own playback stays in sync. render must remain completely
  unaffected by this effect:

    - resolve_kept_ranges only ever scans the first Video track (V1); it
      never visits Audio tracks, so a mirror clip's LinearTimeWarp (whatever
      its time_scalar) cannot reach a KeptRange no matter what.
    - resolve_bgm scans all Audio-track Clips but only ever picks up clips
      whose clip-level metadata["clipwright"]["kind"] == "bgm" (ADR-B4-r2).
      Mirror clips never carry clip-level clipwright metadata (ADR-MS-3;
      only the effect itself does), so they can never be misdetected as BGM
      regardless of the effect they carry.

  This is NOT a Red/TDD test: no render source under test is changed by this
  batch (ADR-MS-6 is render-side-inert by construction — the two functions
  above only read V1 / kind=="bgm" data structures that a mirror clip never
  populates). Both assertions below are expected to PASS unmodified against
  the current render 0.19.x implementation; this file exists to pin that
  invariant so a future change to resolve_kept_ranges/resolve_bgm (e.g. one
  that starts scanning "all tracks" for warps) trips a test failure instead
  of silently breaking the "render is NLE-mirror-sync agnostic" contract.

  In-process OTIO-level tests only — no ffmpeg/ffprobe dependency. Times are
  compared as opentime values (RationalTime/TimeRange), never as float
  seconds (per coding-standards.md "OTIO のテスト").

  Fixture-construction conventions (self-contained module, helpers mirror the
  _rt/_tr/_make_tc_clip pattern in test_plan_nle_relativize.py) build a
  conform()-shaped timeline by hand:
    - V1 (Video track): one Clip with a real clipwright-speed LinearTimeWarp
      (time_scalar=2.0) — this is the *visible* control case (observation 3
      below): render must still read this warp normally.
    - A1 (Audio track): a Resolve-style mirror Clip of the same source,
      stamped with Resolve_OTIO Channels/"Link Group ID" metadata (mirrors
      clipwright.nle_interop._channel_metadata's shape) and, in the "with
      warp" timeline variant only, an *additional* clipwright-speed
      LinearTimeWarp effect (ADR-MS-2's "strip old, append new" shape) with
      no clip-level metadata["clipwright"] (ADR-MS-3).
    - A2 (Audio track): a genuine kind=="bgm" Clip, present in both timeline
      variants, to pin that real BGM detection is unaffected by the mirror
      clip sharing the Audio-track scan space.
"""

from __future__ import annotations

import opentimelineio as otio
from clipwright.otio_utils import set_clipwright_metadata

from clipwright_render.plan import BgmClip, KeptRange, resolve_bgm, resolve_kept_ranges

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FPS = 25.0

# Resolve_OTIO wire-format key (clipwright.nle_interop.RESOLVE_OTIO_KEY);
# hardcoded literal to keep this module self-contained (module docstring
# convention shared with test_plan_nle_relativize.py) -- render's own logic
# never reads this key, it is fixture realism only.
_RESOLVE_OTIO_KEY = "Resolve_OTIO"

_SPEED_TIME_SCALAR = 2.0

_VALID_BGM_DIRECTIVE: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    """Convert seconds to RationalTime."""
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    """Return a TimeRange of start seconds and duration seconds."""
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _speed_warp(time_scalar: float) -> otio.schema.LinearTimeWarp:
    """Build a clipwright-speed LinearTimeWarp effect (mirrors
    clipwright_speed.speed._set_speed_inner Step 9's shape)."""
    warp = otio.schema.LinearTimeWarp(
        name="clipwright_speed",
        time_scalar=time_scalar,
    )
    set_clipwright_metadata(
        warp,
        {
            "tool": "clipwright-speed",
            "version": "0.3.0",
            "kind": "speed",
            "speed": time_scalar,
        },
    )
    return warp


def _make_v1_clip(
    source: str, source_start: float, duration: float, *, link_group_id: int
) -> otio.schema.Clip:
    """Build the V1 (Video) Clip: real clipwright-speed warp + clip-level
    clipwright metadata (as clipwright-speed writes today) + Resolve_OTIO
    Link Group ID (as conform_timeline_for_nle stamps it)."""
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(source_start, duration)
    clip.metadata[_RESOLVE_OTIO_KEY] = {"Link Group ID": link_group_id}

    clip.effects.append(_speed_warp(_SPEED_TIME_SCALAR))
    set_clipwright_metadata(
        clip,
        {
            "tool": "clipwright-speed",
            "version": "0.3.0",
            "kind": "speed",
            "speed": _SPEED_TIME_SCALAR,
        },
    )
    return clip


def _make_mirror_clip(
    source: str,
    source_start: float,
    duration: float,
    *,
    link_group_id: int,
    channels: int = 2,
    stream_idx: int = 0,
    with_warp: bool,
) -> otio.schema.Clip:
    """Build an A1 mirror Clip of the V1 clip above (mirrors
    clipwright.nle_interop._fill_mirror_track's shape): same source_range,
    Resolve_OTIO Channels/Link Group ID metadata, and -- only when
    ``with_warp`` -- an *additional* clipwright-speed LinearTimeWarp effect
    (ADR-MS-2). No clip-level metadata["clipwright"] is ever set (ADR-MS-3):
    only the effect itself carries clipwright metadata.
    """
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(source_start, duration)
    clip.metadata[_RESOLVE_OTIO_KEY] = {
        "Channels": [
            {"Source Channel ID": c, "Source Track ID": stream_idx}
            for c in range(channels)
        ],
        "Link Group ID": link_group_id,
    }
    if with_warp:
        clip.effects.append(_speed_warp(_SPEED_TIME_SCALAR))
    return clip


def _build_timeline(*, mirror_has_warp: bool) -> otio.schema.Timeline:
    """Build a 3-track conform()-shaped timeline:
    V1 (Video): one warped Clip.
    A1 (Audio): a mirror of the V1 clip (warp presence controlled by
      ``mirror_has_warp``).
    A2 (Audio): a genuine kind=="bgm" Clip (present in both variants, to
      pin real BGM detection is unaffected by the mirror scan).
    """
    v1_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    v1_track.append(
        _make_v1_clip("/src/a.mov", source_start=0.0, duration=4.0, link_group_id=1)
    )

    a1_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
    a1_track.append(
        _make_mirror_clip(
            "/src/a.mov",
            source_start=0.0,
            duration=4.0,
            link_group_id=1,
            with_warp=mirror_has_warp,
        )
    )

    a2_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
    bgm_clip = otio.schema.Clip()
    bgm_clip.media_reference = otio.schema.ExternalReference(target_url="/proj/bgm.mp3")
    bgm_clip.source_range = _tr(0.0, 10.0)
    bgm_clip.metadata["clipwright"] = dict(_VALID_BGM_DIRECTIVE)
    a2_track.append(bgm_clip)

    timeline = otio.schema.Timeline()
    timeline.tracks.append(v1_track)
    timeline.tracks.append(a1_track)
    timeline.tracks.append(a2_track)
    timeline.metadata[_RESOLVE_OTIO_KEY] = {"Resolve OTIO Meta Version": 1}
    return timeline


# ===========================================================================
# Observation 1: resolve_kept_ranges only ever scans V1 -- a mirror clip's
# LinearTimeWarp effect (present or not) cannot change its output.
# ===========================================================================


class TestResolveKeptRangesInvisibleToMirrorWarp:
    def test_kept_ranges_identical_with_and_without_mirror_warp(self) -> None:
        """resolve_kept_ranges(timeline) is byte-for-byte identical whether or
        not the A1 mirror clip carries a clipwright-speed LinearTimeWarp
        (ADR-MS-6 pin: resolve_kept_ranges never visits Audio tracks)."""
        tl_with_mirror_warp = _build_timeline(mirror_has_warp=True)
        tl_without_mirror_warp = _build_timeline(mirror_has_warp=False)

        ranges_with = resolve_kept_ranges(tl_with_mirror_warp)
        ranges_without = resolve_kept_ranges(tl_without_mirror_warp)

        assert list(ranges_with) == list(ranges_without)

    def test_v1_warp_still_read_normally_control_group(self) -> None:
        """Control group (observation 3): the V1 clip's own LinearTimeWarp is
        still reflected in KeptRange.time_scalar as before, regardless of the
        A1 mirror's warp -- render's V1-only scan is unaffected either way."""
        tl_with_mirror_warp = _build_timeline(mirror_has_warp=True)
        tl_without_mirror_warp = _build_timeline(mirror_has_warp=False)

        ranges_with = resolve_kept_ranges(tl_with_mirror_warp)
        ranges_without = resolve_kept_ranges(tl_without_mirror_warp)

        assert len(ranges_with) == 1
        assert len(ranges_without) == 1
        expected = KeptRange(
            source="/src/a.mov",
            source_range=_tr(0.0, 4.0),
            time_scalar=_SPEED_TIME_SCALAR,
        )
        assert ranges_with[0] == expected
        assert ranges_without[0] == expected


# ===========================================================================
# Observation 2: resolve_bgm only ever detects clip-level
# metadata["clipwright"]["kind"] == "bgm" -- a mirror clip (which never
# carries clip-level clipwright metadata, ADR-MS-3) is always invisible to it,
# regardless of any LinearTimeWarp effect it carries.
# ===========================================================================


class TestResolveBgmInvisibleToMirrorWarp:
    def test_bgm_result_identical_with_and_without_mirror_warp(self) -> None:
        """resolve_bgm(timeline) returns the same real BgmClip (from A2)
        whether or not the unrelated A1 mirror clip carries a
        clipwright-speed LinearTimeWarp (ADR-MS-6 pin: mirror clips never
        carry clip-level metadata["clipwright"], so they can never be
        misdetected as -- or interfere with detection of -- BGM)."""
        tl_with_mirror_warp = _build_timeline(mirror_has_warp=True)
        tl_without_mirror_warp = _build_timeline(mirror_has_warp=False)

        result_with = resolve_bgm(tl_with_mirror_warp)
        result_without = resolve_bgm(tl_without_mirror_warp)

        assert isinstance(result_with, BgmClip)
        assert isinstance(result_without, BgmClip)
        assert result_with == result_without
        assert result_with.source == "/proj/bgm.mp3"
        assert result_with.source_range == _tr(0.0, 10.0)
