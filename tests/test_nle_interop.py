"""test_nle_interop.py -- Tests for clipwright.nle_interop (Issue #2 Resolve interop).

Target (FR-2~FR-6 / ADR-NI-3, ADR-NI-5, ADR-NI-6, ADR-NI-9, ADR-NI-10 rev.2 (A1
mirror adoption), ADR-NI-11, ADR-NI-12, ADR-NI-13a / DC-GP-003):
- resolve_start_time: MediaInfo -> RationalTime | None, safe fallback on any
  unsupported/invalid input (never raises).
- conform_timeline_for_nle: post-processing helper that, in-place, shifts
  timecode-origin clips on *all* tracks, sets timeline.global_start_time from
  the first V1 clip, mirrors V1 items onto N audio tracks derived from the
  source's audio stream layout, and stamps Resolve_OTIO wire-format metadata.

MediaInfo/StreamInfo objects are hand-built (no ffmpeg needed). OTIO objects
are constructed directly via opentimelineio.schema to keep fixtures minimal
and fully under test control.

clipwright.nle_interop is implemented in src/clipwright/nle_interop.py; this
suite is the regression guard for resolve_start_time / conform_timeline_for_nle
behavior described above.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import opentimelineio as otio

from clipwright.nle_interop import (
    RESOLVE_OTIO_KEY,
    RESOLVE_OTIO_META_VERSION,
    conform_timeline_for_nle,
    resolve_start_time,
)
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "golden" / "issue2_sample.otio"


# ===========================================================================
# Helpers
# ===========================================================================


def _media_info(
    *,
    path: str = "clip.mov",
    rate: float = 25.0,
    frames: float = 50.0,
    start_timecode: str | None = None,
    channels_per_stream: list[int] | None = None,
    has_video: bool = True,
    no_duration: bool = False,
) -> MediaInfo:
    """Hand-build a MediaInfo without touching ffprobe/ffmpeg."""
    streams: list[StreamInfo] = []
    idx = 0
    if has_video:
        streams.append(StreamInfo(index=idx, codec_type="video"))
        idx += 1
    for channels in channels_per_stream or []:
        streams.append(StreamInfo(index=idx, codec_type="audio", channels=channels))
        idx += 1

    duration = (
        None if no_duration else RationalTimeModel(value=float(frames), rate=rate)
    )
    return MediaInfo(
        path=path,
        container="mov",
        duration=duration,
        streams=streams,
        start_timecode=start_timecode,
    )


def _time_range(start: float, duration: float, rate: float) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start, rate),
        duration=otio.opentime.RationalTime(duration, rate),
    )


def _clip(
    target_url: str,
    start: float,
    duration: float,
    rate: float,
    *,
    available_start: float = 0.0,
    available_duration: float | None = None,
    name: str | None = None,
) -> otio.schema.Clip:
    ref = otio.schema.ExternalReference(target_url=target_url)
    ref.available_range = _time_range(
        available_start,
        available_duration if available_duration is not None else duration,
        rate,
    )
    return otio.schema.Clip(
        name=name or Path(target_url).name,
        media_reference=ref,
        source_range=_time_range(start, duration, rate),
    )


def _gap(duration: float, rate: float) -> otio.schema.Gap:
    return otio.schema.Gap(source_range=_time_range(0.0, duration, rate))


def _timeline(
    v1_items: list[otio.schema.Clip | otio.schema.Gap],
    *,
    extra_audio_tracks: list[list[otio.schema.Clip | otio.schema.Gap]] | None = None,
) -> otio.schema.Timeline:
    """Build a bare [V1, A1, A2, ...] timeline with the given items already placed.

    extra_audio_tracks simulates a pre-existing (non-empty) audio track, e.g.
    reframe's `_add_full_clip` A1 layout (ADR-NI-10).
    """
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    for item in v1_items:
        v1.append(item)
    timeline = otio.schema.Timeline(name="test", tracks=[v1])
    for items in extra_audio_tracks or []:
        track = otio.schema.Track(name="", kind=otio.schema.TrackKind.Audio)
        for item in items:
            track.append(item)
        timeline.tracks.append(track)
    return timeline


def _normalize_otio_value(value: object) -> object:
    """Recursively convert OTIO AnyDictionary/AnyVector metadata to plain
    dict/list so equality checks compare content instead of identity.

    opentimelineio 0.18.1 boxes any list assigned into metadata as AnyVector,
    whose __eq__ falls back to object identity (structurally identical
    AnyVector instances compare unequal). AnyDictionary.__eq__ only does a
    shallow dict(...) conversion, so nested AnyVector values stay boxed and
    still fail comparison one level down. Recursing through both Mapping and
    Sequence here restores plain-value equality at every nesting level.
    """
    if isinstance(value, Mapping):
        return {k: _normalize_otio_value(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_normalize_otio_value(v) for v in value]
    return value


def _normalize_for_golden(timeline: otio.schema.Timeline) -> dict[str, object]:
    """Reduce a Timeline to the subset of structure covered by FR-5 wire format.

    Deliberately ignores keys outside our scope (e.g. "Locked"/"SoloOn" that
    Issue #2's own sample script sets but architecture ADR-NI-5 does not
    require us to reproduce) so that golden comparison (AC-13) checks
    Resolve_OTIO subtree / global_start_time / track structure without being
    coupled to incidental extra metadata.
    """

    def clip_meta(item: otio.core.Item) -> dict[str, object]:
        meta = item.metadata.get(RESOLVE_OTIO_KEY, {})
        return {
            k: _normalize_otio_value(meta[k])
            for k in ("Link Group ID", "Channels")
            if k in meta
        }

    def track_summary(track: otio.schema.Track) -> dict[str, object]:
        meta = track.metadata.get(RESOLVE_OTIO_KEY, {})
        summary: dict[str, object] = {"kind": str(track.kind)}
        if track.kind == otio.schema.TrackKind.Audio and "Audio Type" in meta:
            summary["audio_type"] = meta["Audio Type"]
        summary["items"] = [
            {"gap": True}
            if isinstance(item, otio.schema.Gap)
            else {"gap": False, **clip_meta(item)}
            for item in track
        ]
        return summary

    gst = timeline.global_start_time
    return {
        "global_start_time": (gst.value, gst.rate) if gst is not None else None,
        "timeline_meta_version": timeline.metadata.get(RESOLVE_OTIO_KEY, {}).get(
            "Resolve OTIO Meta Version"
        ),
        "tracks": [track_summary(t) for t in timeline.tracks],
    }


# ===========================================================================
# 1. resolve_start_time
# ===========================================================================


class TestResolveStartTime:
    """MediaInfo -> RationalTime | None (ADR-NI-2/6/13a)."""

    def test_valid_timecode_returns_rational_time_value_and_rate(self) -> None:
        media_info = _media_info(start_timecode="01:00:00:00", rate=25.0, frames=50)
        result = resolve_start_time(media_info)
        assert result is not None
        assert result.value == 90000.0
        assert result.rate == 25.0

    def test_drop_frame_with_exact_fraction_rate_is_accepted(self) -> None:
        rate = 30000 / 1001
        media_info = _media_info(start_timecode="01:00:00;00", rate=rate, frames=50)
        expected = otio.opentime.from_timecode("01:00:00;00", rate=rate)

        result = resolve_start_time(media_info)

        assert result is not None
        assert result.value == expected.value
        assert result.rate == expected.rate

    def test_rounded_broadcast_rate_is_snapped_before_from_timecode(self) -> None:
        """ADR-NI-13a: 29.97 (rounded decimal) is rejected by from_timecode
        directly but must be snapped via RationalTime.nearest_valid_timecode_rate
        before conversion so drop-frame-adjacent rates still resolve."""
        media_info = _media_info(start_timecode="01:00:00:00", rate=29.97, frames=50)
        snapped_rate = otio.opentime.RationalTime.nearest_valid_timecode_rate(29.97)
        expected = otio.opentime.from_timecode("01:00:00:00", rate=snapped_rate)

        result = resolve_start_time(media_info)

        assert result is not None
        assert result.value == expected.value
        assert result.rate == expected.rate

    def test_invalid_timecode_returns_none(self) -> None:
        media_info = _media_info(start_timecode="not-a-timecode", rate=25.0, frames=50)
        assert resolve_start_time(media_info) is None

    def test_missing_timecode_returns_none(self) -> None:
        media_info = _media_info(start_timecode=None, rate=25.0, frames=50)
        assert resolve_start_time(media_info) is None

    def test_audio_only_sentinel_rate_returns_none(self) -> None:
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=1000.0, frames=50, has_video=False
        )
        assert resolve_start_time(media_info) is None

    def test_duration_none_returns_none(self) -> None:
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=25.0, frames=50, no_duration=True
        )
        assert resolve_start_time(media_info) is None


# ===========================================================================
# 2. TC shift across all tracks + global_start_time (ADR-NI-1/10/11)
# ===========================================================================


class TestVideoClipShiftAndGlobalStart:
    def test_v1_clip_source_and_available_range_are_shifted(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[2]
        )

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert clip.source_range.start_time.value == 90000.0
        assert clip.source_range.start_time.rate == 25.0
        assert clip.media_reference.available_range.start_time.value == 90000.0

    def test_global_start_time_is_first_clip_shifted_start(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[]
        )

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert timeline.global_start_time is not None
        assert timeline.global_start_time.value == 90000.0
        assert timeline.global_start_time.rate == 25.0

    def test_leading_gap_global_start_uses_first_clip_timecode(self) -> None:
        rate = 25.0
        gap = _gap(10, rate)
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([gap, clip])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[]
        )

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert timeline.global_start_time is not None
        assert timeline.global_start_time.value == 90000.0

    def test_v1_with_zero_clips_leaves_global_start_time_unset_and_warns(self) -> None:
        rate = 25.0
        gap = _gap(10, rate)
        timeline = _timeline([gap])

        warnings = conform_timeline_for_nle(timeline, {})

        assert timeline.global_start_time is None
        assert len(warnings) >= 1


# ===========================================================================
# 3. Audio track expansion (FR-4)
# ===========================================================================


class TestAudioTrackMirroring:
    def test_stereo_source_creates_single_audio_track(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(rate=rate, frames=50, channels_per_stream=[2])

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert len(timeline.tracks) == 2
        a1 = timeline.tracks[1]
        assert a1.kind == otio.schema.TrackKind.Audio
        assert a1.metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Stereo"
        assert len(a1) == 1

    def test_eight_mono_streams_create_eight_audio_tracks(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(rate=rate, frames=50, channels_per_stream=[1] * 8)

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert len(timeline.tracks) == 9  # V1 + A1..A8
        for i in range(1, 9):
            track = timeline.tracks[i]
            assert track.kind == otio.schema.TrackKind.Audio
            assert track.metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Mono"
            clip_meta = track[0].metadata[RESOLVE_OTIO_KEY]
            assert _normalize_otio_value(clip_meta["Channels"]) == [
                {"Source Channel ID": 0, "Source Track ID": i - 1}
            ]

    def test_no_audio_streams_adds_no_audio_track(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(rate=rate, frames=50, channels_per_stream=[])

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert len(timeline.tracks) == 1


# ===========================================================================
# 4. V1 mirroring: Link Group numbering, Gap handling, multi-source gap fill
#    (ADR-NI-11)
# ===========================================================================


class TestV1MirrorLinkGroupAndGaps:
    def test_link_group_ids_use_clip_ordinal_skipping_gaps(self) -> None:
        """Two KEEP-style clips separated by a Gap must get Link Group ID 1/2
        (clip ordinal), not item index 1/3 (ADR-NI-11 index-vs-ordinal guard)."""
        rate = 25.0
        clip1 = _clip(
            "media.mov", start=0, duration=25, rate=rate, available_duration=60
        )
        gap = _gap(5, rate)
        clip2 = _clip(
            "media.mov", start=30, duration=25, rate=rate, available_duration=60
        )
        timeline = _timeline([clip1, gap, clip2])
        media_info = _media_info(rate=rate, frames=60, channels_per_stream=[1])

        conform_timeline_for_nle(timeline, {"media.mov": media_info})

        assert clip1.metadata[RESOLVE_OTIO_KEY]["Link Group ID"] == 1
        assert clip2.metadata[RESOLVE_OTIO_KEY]["Link Group ID"] == 2

        a1_items = list(timeline.tracks[1])
        assert len(a1_items) == 3
        mirrored_clip1, mirrored_gap, mirrored_clip2 = a1_items
        assert isinstance(mirrored_gap, otio.schema.Gap)
        assert (
            mirrored_gap.source_range.duration.value == gap.source_range.duration.value
        )
        assert mirrored_clip1.metadata[RESOLVE_OTIO_KEY]["Link Group ID"] == 1
        assert mirrored_clip2.metadata[RESOLVE_OTIO_KEY]["Link Group ID"] == 2

    def test_multi_source_missing_audio_stream_is_filled_with_gap(self) -> None:
        rate = 25.0
        clip_a = _clip("a.mov", start=0, duration=25, rate=rate)
        clip_b = _clip("b.mov", start=25, duration=30, rate=rate)
        timeline = _timeline([clip_a, clip_b])
        media_infos = {
            "a.mov": _media_info(
                path="a.mov", rate=rate, frames=25, channels_per_stream=[2]
            ),
            "b.mov": _media_info(
                path="b.mov", rate=rate, frames=30, channels_per_stream=[]
            ),
        }

        conform_timeline_for_nle(timeline, media_infos)

        assert len(timeline.tracks) == 2  # V1 + single A1 (max stream count == 1)
        a1_items = list(timeline.tracks[1])
        assert len(a1_items) == 2
        assert not isinstance(a1_items[0], otio.schema.Gap)
        assert isinstance(a1_items[1], otio.schema.Gap)
        assert (
            a1_items[1].source_range.duration.value
            == clip_b.source_range.duration.value
        )


# ===========================================================================
# 5. Resolve_OTIO wire format (ADR-NI-5) + golden structural comparison (AC-13)
# ===========================================================================


class TestResolveOtioWireFormat:
    def test_wire_format_matches_issue2_dict_shape_exactly(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[2]
        )

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert _normalize_otio_value(timeline.metadata[RESOLVE_OTIO_KEY]) == {
            "Resolve OTIO Meta Version": RESOLVE_OTIO_META_VERSION
        }
        assert _normalize_otio_value(timeline.tracks[1].metadata[RESOLVE_OTIO_KEY]) == {
            "Audio Type": "Stereo"
        }
        assert _normalize_otio_value(
            timeline.tracks[1][0].metadata[RESOLVE_OTIO_KEY]
        ) == {
            "Channels": [
                {"Source Channel ID": 0, "Source Track ID": 0},
                {"Source Channel ID": 1, "Source Track ID": 0},
            ],
            "Link Group ID": 1,
        }
        assert _normalize_otio_value(clip.metadata[RESOLVE_OTIO_KEY]) == {
            "Link Group ID": 1
        }

    def test_conform_output_matches_golden_structure(self) -> None:
        """AC-13: compare against tests/fixtures/golden/issue2_sample.otio, an
        .otio produced by *executing* Issue #2's own sample code (independent
        of our conform implementation) so this is not a transcription
        self-check."""
        rate = 25.0
        clip = _clip("test_media.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[1]
        )

        conform_timeline_for_nle(timeline, {"test_media.mov": media_info})

        golden = otio.adapters.read_from_file(str(GOLDEN_PATH))
        assert _normalize_for_golden(timeline) == _normalize_for_golden(golden)


# ===========================================================================
# 6/7. Idempotency, including reframe-type degeneration (ADR-NI-10, AC-5)
# ===========================================================================


class TestIdempotency:
    def test_double_apply_is_noop(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_infos = {
            "a.mov": _media_info(
                start_timecode="01:00:00:00",
                rate=rate,
                frames=50,
                channels_per_stream=[2],
            )
        }

        conform_timeline_for_nle(timeline, media_infos)
        before = otio.adapters.write_to_string(timeline, "otio_json")

        second_warnings = conform_timeline_for_nle(timeline, media_infos)
        after = otio.adapters.write_to_string(timeline, "otio_json")

        assert second_warnings == []
        assert before == after


class TestAudioMirrorAdoption:
    """ADR-NI-10 rev.2: when A1 already has a clip (e.g. reframe's, or any of
    the 5 create tools' ``_add_full_clip`` layout) and its item sequence is a
    *mirror match* of V1 (same item count, same Clip/Gap kind at each
    position, same target_url and source_range for Clips), conform *adopts*
    that A1 as stream#0 instead of skipping: it stamps Resolve_OTIO metadata
    (Audio Type / Channels / Link Group ID) onto the existing track/clip in
    place and appends A2..AN for any additional audio streams -- with no
    warning. These tests are the regression guard for that adoption path:
    before ADR-NI-10 rev.2 the code treated any non-empty A1 as a skip case
    regardless of whether it mirrored V1, which these assertions now forbid.
    """

    def _build(
        self, media_info: MediaInfo
    ) -> tuple[
        otio.schema.Timeline, dict[str, MediaInfo], otio.schema.Clip, otio.schema.Clip
    ]:
        rate = 25.0
        v1_clip = _clip("a.mov", start=0, duration=50, rate=rate)
        a1_clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([v1_clip], extra_audio_tracks=[[a1_clip]])
        media_infos = {"a.mov": media_info}
        return timeline, media_infos, v1_clip, a1_clip

    def test_mirror_matching_a1_with_stereo_source_is_adopted_as_stream_zero(
        self,
    ) -> None:
        rate = 25.0
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[2]
        )
        timeline, media_infos, v1_clip, a1_clip = self._build(media_info)

        warnings = conform_timeline_for_nle(timeline, media_infos)

        assert warnings == []
        # No new track created for a single audio stream: A1 is reused.
        assert len(timeline.tracks) == 2
        a1 = timeline.tracks[1]
        assert a1.kind == otio.schema.TrackKind.Audio
        assert a1.metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Stereo"
        assert len(a1) == 1
        assert a1[0] is a1_clip  # existing clip object is augmented, not replaced

        # TC shift still applies to the adopted A1 clip (ADR-NI-10 all-track shift).
        assert a1_clip.source_range.start_time.value == 90000.0
        assert a1_clip.media_reference.available_range.start_time.value == 90000.0

        assert _normalize_otio_value(a1_clip.metadata[RESOLVE_OTIO_KEY]) == {
            "Channels": [
                {"Source Channel ID": 0, "Source Track ID": 0},
                {"Source Channel ID": 1, "Source Track ID": 0},
            ],
            "Link Group ID": 1,
        }
        assert v1_clip.metadata[RESOLVE_OTIO_KEY] == {"Link Group ID": 1}
        assert timeline.metadata[RESOLVE_OTIO_KEY] == {
            "Resolve OTIO Meta Version": RESOLVE_OTIO_META_VERSION
        }

    def test_mirror_matching_a1_with_eight_mono_streams_adopts_and_adds_a2_through_a8(
        self,
    ) -> None:
        rate = 25.0
        media_info = _media_info(rate=rate, frames=50, channels_per_stream=[1] * 8)
        timeline, media_infos, _v1_clip, a1_clip = self._build(media_info)

        warnings = conform_timeline_for_nle(timeline, media_infos)

        assert warnings == []
        assert len(timeline.tracks) == 9  # V1 + adopted A1 + A2..A8
        a1 = timeline.tracks[1]
        assert a1.metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Mono"
        assert a1[0] is a1_clip
        for i in range(1, 9):
            track = timeline.tracks[i]
            assert track.kind == otio.schema.TrackKind.Audio
            assert track.metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Mono"
            clip_meta = track[0].metadata[RESOLVE_OTIO_KEY]
            assert _normalize_otio_value(clip_meta["Channels"]) == [
                {"Source Channel ID": 0, "Source Track ID": i - 1}
            ]

    def test_double_apply_on_adopted_a1_is_noop(self) -> None:
        rate = 25.0
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[2]
        )
        timeline, media_infos, v1_clip, _a1_clip = self._build(media_info)

        conform_timeline_for_nle(timeline, media_infos)
        before = otio.adapters.write_to_string(timeline, "otio_json")
        shifted_once = v1_clip.source_range.start_time.value

        second_warnings = conform_timeline_for_nle(timeline, media_infos)
        after = otio.adapters.write_to_string(timeline, "otio_json")

        assert second_warnings == []
        assert before == after
        assert v1_clip.source_range.start_time.value == shifted_once


class TestAudioMirrorMismatchDegeneration:
    """A1 already has a clip whose item sequence does *not* mirror V1 (e.g. a
    pre-existing narration/bgm track unrelated to V1's source, ADR-NI-10
    rev.2): TC shift still applies to every track, but audio mirror expansion
    is skipped -- and the idempotent Resolve_OTIO marker must still be
    stamped on the timeline so a second conform call cannot double-shift.
    This degenerate branch is unchanged from the original ADR-NI-10 design."""

    def _build(
        self,
    ) -> tuple[
        otio.schema.Timeline, dict[str, MediaInfo], otio.schema.Clip, otio.schema.Clip
    ]:
        rate = 25.0
        v1_clip = _clip("a.mov", start=0, duration=50, rate=rate)
        # Mismatched A1: an unrelated pre-existing clip (different
        # target_url), so the item-for-item mirror comparison against V1
        # fails and this degenerates to skip+warning.
        a1_clip = _clip("narration.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([v1_clip], extra_audio_tracks=[[a1_clip]])
        media_infos = {
            "a.mov": _media_info(
                start_timecode="01:00:00:00",
                rate=rate,
                frames=50,
                channels_per_stream=[2],
            ),
            "narration.mov": _media_info(
                path="narration.mov",
                start_timecode="01:00:00:00",
                rate=rate,
                frames=50,
                channels_per_stream=[1],
            ),
        }
        return timeline, media_infos, v1_clip, a1_clip

    def test_all_tracks_shifted_mirror_skipped_marker_stamped(self) -> None:
        timeline, media_infos, v1_clip, a1_clip = self._build()

        warnings = conform_timeline_for_nle(timeline, media_infos)

        assert v1_clip.source_range.start_time.value == 90000.0
        assert (
            a1_clip.source_range.start_time.value == 90000.0
        )  # ADR-NI-10 all-track shift
        assert a1_clip.media_reference.available_range.start_time.value == 90000.0

        # Mirror expansion skipped: still exactly V1 + the original (untouched) A1.
        assert len(timeline.tracks) == 2
        assert len(timeline.tracks[1]) == 1

        # Idempotent marker must be present even though mirroring degenerated.
        assert timeline.metadata[RESOLVE_OTIO_KEY] == {
            "Resolve OTIO Meta Version": RESOLVE_OTIO_META_VERSION
        }
        assert any("skip" in w.lower() for w in warnings)

    def test_second_conform_is_noop_no_double_shift(self) -> None:
        timeline, media_infos, v1_clip, _a1_clip = self._build()

        conform_timeline_for_nle(timeline, media_infos)
        shifted_once = v1_clip.source_range.start_time.value

        second_warnings = conform_timeline_for_nle(timeline, media_infos)

        assert second_warnings == []
        assert v1_clip.source_range.start_time.value == shifted_once


# ===========================================================================
# 8. Fallback behavior (ADR-NI-6)
# ===========================================================================


class TestFallbackBehavior:
    def test_invalid_timecode_warns_no_shift_but_audio_still_expanded(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode="garbage-tc", rate=rate, frames=50, channels_per_stream=[2]
        )

        warnings = conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert clip.source_range.start_time.value == 0.0
        assert timeline.global_start_time is None
        assert len(timeline.tracks) == 2  # audio expansion continues regardless
        assert len(warnings) >= 1

    def test_channels_over_two_falls_back_to_mono_with_warning(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(rate=rate, frames=50, channels_per_stream=[6])

        warnings = conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert timeline.tracks[1].metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Mono"
        assert len(warnings) >= 1


# ===========================================================================
# 9. TC-less material: behavior-unchanged side (NFR-1)
# ===========================================================================


class TestNoTimecodeMaterial:
    def test_no_timecode_skips_shift_but_still_expands_audio(self) -> None:
        rate = 25.0
        clip = _clip("a.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode=None, rate=rate, frames=50, channels_per_stream=[2]
        )

        conform_timeline_for_nle(timeline, {"a.mov": media_info})

        assert clip.source_range.start_time.value == 0.0
        assert timeline.global_start_time is None
        assert len(timeline.tracks) == 2
        assert timeline.tracks[1].metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Stereo"


# ===========================================================================
# 10. Warning message safety (CWE-209)
# ===========================================================================


class TestWarningMessageSafety:
    def test_warnings_do_not_leak_raw_timecode_or_paths(self) -> None:
        rate = 25.0
        secret_path = "C:/very/secret/leaky_media_name.mov"
        bad_tc = "definitely-not-a-tc-99:99:99"
        clip = _clip(secret_path, start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        media_info = _media_info(
            start_timecode=bad_tc, rate=rate, frames=50, channels_per_stream=[2]
        )

        warnings = conform_timeline_for_nle(timeline, {secret_path: media_info})

        joined = " ".join(warnings)
        assert bad_tc not in joined
        assert "leaky_media_name" not in joined
        assert "secret" not in joined


# ===========================================================================
# 11. media_infos key matching warnings (ADR-NI-9)
# ===========================================================================


class TestMediaInfoKeyMatching:
    def test_clip_missing_from_media_infos_is_skipped_with_warning(self) -> None:
        rate = 25.0
        known = _clip("known.mov", start=0, duration=50, rate=rate)
        unknown = _clip("unknown.mov", start=50, duration=25, rate=rate)
        timeline = _timeline([known, unknown])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=50, channels_per_stream=[]
        )

        warnings = conform_timeline_for_nle(timeline, {"known.mov": media_info})

        assert unknown.source_range.start_time.value == 50.0  # left untouched
        assert known.source_range.start_time.value == 90000.0  # normally processed
        assert any("skip" in w.lower() for w in warnings)

    def test_unused_media_info_key_warns(self) -> None:
        rate = 25.0
        clip = _clip("used.mov", start=0, duration=50, rate=rate)
        timeline = _timeline([clip])
        used_info = _media_info(
            path="used.mov", start_timecode="01:00:00:00", rate=rate, frames=50
        )
        unused_info = _media_info(
            path="unused.mov", start_timecode="01:00:00:00", rate=rate, frames=50
        )

        warnings = conform_timeline_for_nle(
            timeline, {"used.mov": used_info, "unused.mov": unused_info}
        )

        assert any("unused" in w.lower() for w in warnings)


# ===========================================================================
# 12. Input contract guards (DC-GP-003)
# ===========================================================================


class TestInputContractGuards:
    def test_missing_reference_clip_is_skipped_without_exception(self) -> None:
        rate = 25.0
        bad_clip = otio.schema.Clip(
            name="bad",
            media_reference=otio.schema.MissingReference(),
            source_range=_time_range(0, 50, rate),
        )
        good_clip = _clip("good.mov", start=50, duration=25, rate=rate)
        timeline = _timeline([bad_clip, good_clip])
        media_info = _media_info(
            start_timecode="01:00:00:00", rate=rate, frames=25, channels_per_stream=[]
        )

        warnings = conform_timeline_for_nle(timeline, {"good.mov": media_info})

        # ADR-NI-1: shift is additive (source_range.start += TC), so good_clip's
        # existing trim position (50) must be preserved, not overwritten by the
        # timecode value alone: 50 + 90000 = 90050.
        assert good_clip.source_range.start_time.value == 90050.0
        assert any("skip" in w.lower() or "missing" in w.lower() for w in warnings)

    def test_timeline_without_v1_track_is_noop_with_warning(self) -> None:
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        timeline = otio.schema.Timeline(name="no_video", tracks=[a1])

        warnings = conform_timeline_for_nle(timeline, {})

        assert len(timeline.tracks) == 1
        assert timeline.metadata.get(RESOLVE_OTIO_KEY) is None
        assert len(warnings) >= 1
