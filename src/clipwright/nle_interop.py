"""nle_interop.py -- Resolve NLE interop conform helpers (Issue #2).

Post-processing layer that stamps DaVinci Resolve's ``Resolve_OTIO`` wire
format onto an in-memory OTIO Timeline right before it is saved.  This is the
one deliberate exception to the ``metadata["clipwright"]`` namespace
convention (ADR-NI-5): ``Resolve_OTIO`` is an external interop namespace whose
key names and value shapes are dictated by Resolve itself, transcribed
verbatim from Issue #2's own verified sample code so a third-party NLE can
read them without any clipwright-specific knowledge.

Input contract (DC-GP-003): ``conform_timeline_for_nle`` expects a
well-formed timeline as produced by a clipwright *create* tool: a V1 (video)
track exists, and clips normally carry an ``ExternalReference`` with a
``source_range``.  Any item that falls outside that contract (a
``MissingReference`` clip, a clip with no ``source_range``, a V1-less
timeline) is skipped with a warning rather than raising -- conform must never
throw (it always runs inside a tool's outer exception boundary).

Idempotency: the presence of ``timeline.metadata["Resolve_OTIO"]`` is used as
the single idempotency marker.  A second call on an already-conformed
timeline is a pure no-op (returns ``[]`` immediately), which also protects
against double-shifting timecode on repeated saves.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import opentimelineio as otio

from clipwright.schemas import MediaInfo, StreamInfo

RESOLVE_OTIO_KEY = "Resolve_OTIO"
RESOLVE_OTIO_META_VERSION = "1.0"

# ffprobe reports this sentinel duration.rate for audio-only media (no video
# stream), see media.py.  Timecode-origin shifting only makes sense for
# video-carrying clips, so this rate is never a valid frame rate to shift by.
_AUDIO_ONLY_SENTINEL_RATE = 1000.0


def resolve_start_time(media_info: MediaInfo) -> otio.opentime.RationalTime | None:
    """Resolve MediaInfo.start_timecode into a RationalTime, or None.

    Never raises: any unsupported or invalid input (missing timecode, missing
    duration, the audio-only sentinel rate, or a timecode string/rate that
    ``from_timecode`` rejects) resolves to None so callers can fall back to
    unmodified (0-origin) behavior (ADR-NI-6).

    ADR-NI-12: for video-carrying media, ``media_info.duration.rate`` is
    guaranteed (by media.py's ffprobe parsing) to be the first video stream's
    avg_frame_rate, so it is always the correct rate to interpret the
    timecode string against -- no separate rate source is needed here.

    ADR-NI-13a: ``RationalTime.nearest_valid_timecode_rate`` is applied to the
    rate *before* calling ``from_timecode`` so that a rounded broadcast rate
    (e.g. 29.97 as a decimal) still resolves via its nearest exact rational
    rate (e.g. 30000/1001), rather than being rejected outright. This is a
    no-op for rates that are already exact/valid.
    """
    if media_info.start_timecode is None:
        return None
    if media_info.duration is None:
        return None

    rate = media_info.duration.rate
    if rate == _AUDIO_ONLY_SENTINEL_RATE:
        return None

    snapped_rate = otio.opentime.RationalTime.nearest_valid_timecode_rate(rate)
    try:
        return otio.opentime.from_timecode(media_info.start_timecode, snapped_rate)
    except ValueError:
        return None


def conform_timeline_for_nle(
    timeline: otio.schema.Timeline,
    media_infos: Mapping[str, MediaInfo],
) -> list[str]:
    """Conform a freshly built Timeline to Resolve's NLE interop wire format.

    ``media_infos`` keys must be the exact ``target_url`` string written onto
    each Clip's ``ExternalReference`` (i.e. the same value produced by
    ``pathpolicy.media_ref_for_otio`` that the caller used when building the
    clip). Matching is a literal string comparison; no normalization is
    performed.

    Mutates *timeline* in place and returns a list of warning strings meant
    to be merged into the caller's ToolResult.warnings. Never raises.

    Steps (ADR-NI-3/9/10/11):
      0. Idempotency guard: if already conformed, return [] immediately.
      1. Shift every Clip on every track (not just V1, ADR-NI-10) whose
         target_url resolves via resolve_start_time.
      2. Set timeline.global_start_time from the first V1 Clip (Gaps
         skipped) that was actually shifted (ADR-NI-11).
      3. Mirror V1's item sequence onto N audio tracks derived from the
         source's audio stream layout (_mirror_audio_tracks).
      4. Stamp Resolve_OTIO metadata: Link Group ID on V1 clips (and their
         audio mirrors) and the timeline-level version marker -- applied
         unconditionally, even when audio mirroring degenerates, so the
         idempotency marker is always present after a first successful call
         (ADR-NI-10).
    """
    if RESOLVE_OTIO_KEY in timeline.metadata:
        return []

    warnings: list[str] = []

    v1 = _find_video_track(timeline)
    if v1 is None:
        warnings.append(
            "timeline has no V1 (video) track; NLE conform skipped for this timeline"
        )
        return warnings

    used_keys: set[str] = set()
    shifted_ids: set[int] = set()
    for track in timeline.tracks:
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            shifted, warn = _shift_clip_times(item, media_infos, used_keys)
            if shifted:
                shifted_ids.add(id(item))
            if warn is not None:
                warnings.append(warn)

    first_clip = next((it for it in v1 if isinstance(it, otio.schema.Clip)), None)
    if first_clip is None:
        warnings.append("V1 track has no clips; global_start_time left unset")
    elif id(first_clip) in shifted_ids:
        ref = first_clip.media_reference
        if (
            isinstance(ref, otio.schema.ExternalReference)
            and ref.available_range is not None
        ):
            timeline.global_start_time = ref.available_range.start_time

    ordinals = _clip_ordinals(v1)
    warnings.extend(_mirror_audio_tracks(timeline, v1, media_infos, ordinals))
    _apply_resolve_metadata(timeline, v1, ordinals)

    for key in media_infos:
        if key not in used_keys:
            warnings.append(
                "media_infos entry was not referenced by any clip; unused key ignored"
            )

    return warnings


# ===========================================================================
# Internal helpers
# ===========================================================================


def _find_video_track(timeline: otio.schema.Timeline) -> otio.schema.Track | None:
    """Return the first Video-kind track (V1), or None if absent."""
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            return track
    return None


def _shift_clip_times(
    clip: otio.schema.Clip,
    media_infos: Mapping[str, MediaInfo],
    used_keys: set[str],
) -> tuple[bool, str | None]:
    """Shift a single clip's source_range/available_range in place.

    Returns (shifted, warning). ``used_keys`` is updated whenever a matching
    MediaInfo is found, independent of whether the timecode itself resolves,
    so ADR-NI-9's unused-key detection stays accurate regardless of shift
    outcome. Warning text never includes the raw timecode string or the
    target_url path (CWE-209).
    """
    ref = clip.media_reference
    if not isinstance(ref, otio.schema.ExternalReference):
        return (
            False,
            "clip has no external media reference; NLE conform skipped for this clip",
        )
    if clip.source_range is None:
        return False, "clip has no source_range; NLE conform skipped for this clip"

    media_info = media_infos.get(ref.target_url)
    if media_info is None:
        return (
            False,
            "clip media not found in media_infos; NLE conform skipped for this clip",
        )
    used_keys.add(ref.target_url)

    if media_info.start_timecode is None:
        # No timecode present at all is normal (non-TC material, NFR-1), not a failure.
        return False, None

    start = resolve_start_time(media_info)
    if start is None:
        return (
            False,
            "clip start timecode could not be resolved; NLE conform skipped for clip",
        )

    clip.source_range = otio.opentime.TimeRange(
        start_time=clip.source_range.start_time + start,
        duration=clip.source_range.duration,
    )
    if ref.available_range is not None:
        ref.available_range = otio.opentime.TimeRange(
            start_time=ref.available_range.start_time + start,
            duration=ref.available_range.duration,
        )
    return True, None


def _clip_ordinals(v1: otio.schema.Track) -> dict[int, int]:
    """Map id(clip) -> 1-based ordinal among V1's Clip items (Gaps excluded).

    This single counter is the source of truth for Resolve's Link Group ID,
    shared between the V1 clip itself and its audio mirrors, so the two
    cannot disagree about clip numbering (ADR-NI-11).
    """
    ordinals: dict[int, int] = {}
    counter = 0
    for item in v1:
        if isinstance(item, otio.schema.Clip):
            counter += 1
            ordinals[id(item)] = counter
    return ordinals


def _audio_type(channels: int) -> tuple[str, str | None]:
    """Map a channel count to Resolve's "Audio Type" plus an optional warning.

    Only 1ch (Mono) and 2ch (Stereo) are verified against Issue #2's real
    layouts. Anything else falls back to Mono with a warning (ADR-NI-6).
    """
    if channels == 1:
        return "Mono", None
    if channels == 2:
        return "Stereo", None
    return (
        "Mono",
        "audio channel count outside the supported 1/2 range; "
        "falling back to Mono audio type",
    )


def _audio_streams(
    item: otio.core.Item, media_infos: Mapping[str, MediaInfo]
) -> list[StreamInfo]:
    """Return the audio StreamInfo entries for a V1 item's resolved MediaInfo.

    Returns an empty list for Gaps, clips without an ExternalReference, and
    clips whose target_url has no matching MediaInfo (already warned about
    during the shift pass).
    """
    if not isinstance(item, otio.schema.Clip):
        return []
    ref = item.media_reference
    if not isinstance(ref, otio.schema.ExternalReference):
        return []
    info = media_infos.get(ref.target_url)
    if info is None:
        return []
    return [s for s in info.streams if s.codec_type == "audio"]


def _clone_time_range(time_range: otio.opentime.TimeRange) -> otio.opentime.TimeRange:
    """Return a value copy of a TimeRange (avoids aliasing across mirrors)."""
    return otio.opentime.TimeRange(
        start_time=time_range.start_time,
        duration=time_range.duration,
    )


def _channel_metadata(
    channels: int, stream_idx: int, ordinal: int | None
) -> dict[str, Any]:
    """Build a Resolve_OTIO clip metadata dict for one mirrored audio stream.

    ``stream_idx`` becomes each channel's "Source Track ID" and ``ordinal``
    (the V1 clip ordinal, shared with the V1 clip itself) its "Link Group ID".
    """
    meta: dict[str, Any] = {
        "Channels": [
            {"Source Channel ID": c, "Source Track ID": stream_idx}
            for c in range(channels)
        ]
    }
    if ordinal is not None:
        meta["Link Group ID"] = ordinal
    return meta


def _fill_mirror_track(
    track: otio.schema.Track,
    stream_idx: int,
    v1_items: list[otio.core.Item],
    item_streams: list[list[StreamInfo]],
    ordinals: dict[int, int],
) -> int | None:
    """Append mirror items for a single audio stream onto an empty track.

    Each V1 Clip that carries a ``stream_idx``-th audio stream becomes a new
    mirror Clip (stamped with Resolve_OTIO Channels / Link Group ID); Gaps and
    clips lacking that stream become Gaps of the mirrored item's duration.
    Returns the channel count of the first real mirrored clip (used to derive
    the track's Audio Type), or None if the track ended up all Gaps.
    """
    track_channels: int | None = None
    for item, streams in zip(v1_items, item_streams, strict=True):
        if isinstance(item, otio.schema.Gap):
            track.append(
                otio.schema.Gap(source_range=_clone_time_range(item.source_range))
            )
            continue

        if stream_idx >= len(streams) or item.source_range is None:
            gap_source = item.source_range or otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 1.0),
                duration=otio.opentime.RationalTime(0.0, 1.0),
            )
            track.append(otio.schema.Gap(source_range=_clone_time_range(gap_source)))
            continue

        ref = item.media_reference
        if not isinstance(ref, otio.schema.ExternalReference):
            continue  # pragma: no cover -- excluded already via _audio_streams

        stream = streams[stream_idx]
        channels = stream.channels if stream.channels is not None else 1
        if track_channels is None:
            track_channels = channels

        mirror_ref = otio.schema.ExternalReference(target_url=ref.target_url)
        if ref.available_range is not None:
            mirror_ref.available_range = _clone_time_range(ref.available_range)
        mirror_clip = otio.schema.Clip(
            name=item.name,
            media_reference=mirror_ref,
            source_range=_clone_time_range(item.source_range),
        )
        mirror_clip.metadata[RESOLVE_OTIO_KEY] = _channel_metadata(
            channels, stream_idx, ordinals.get(id(item))
        )
        track.append(mirror_clip)

    return track_channels


def _same_source_range(
    a: otio.opentime.TimeRange | None, b: otio.opentime.TimeRange | None
) -> bool:
    """Value-compare two optional source ranges (start_time and duration)."""
    if a is None or b is None:
        return a is b
    return bool(a.start_time == b.start_time and a.duration == b.duration)


def _a1_mirrors_v1(a1: otio.schema.Track, v1_items: list[otio.core.Item]) -> bool:
    """Return True if A1's items mirror V1's item-for-item (ADR-NI-10 rev.2).

    Mirror match = same item count, same Clip/Gap kind at each position, and
    for Clips the same ExternalReference target_url and same source_range.
    This is the shape produced by the create tools' ``_add_full_clip`` (V1 and
    A1 receive the same source clip); a non-matching A1 (e.g. an unrelated bgm
    track) degenerates to skip+warning instead. The comparison runs after the
    all-track timecode shift, which shifts a V1 clip and its A1 mirror
    identically, so matching clips still compare equal here.
    """
    a1_items = list(a1)
    if len(a1_items) != len(v1_items):
        return False
    for a_item, v_item in zip(a1_items, v1_items, strict=True):
        v_is_gap = isinstance(v_item, otio.schema.Gap)
        a_is_gap = isinstance(a_item, otio.schema.Gap)
        if v_is_gap != a_is_gap:
            return False
        if v_is_gap:
            continue
        if not (
            isinstance(v_item, otio.schema.Clip)
            and isinstance(a_item, otio.schema.Clip)
        ):
            return False
        v_ref = v_item.media_reference
        a_ref = a_item.media_reference
        if not (
            isinstance(v_ref, otio.schema.ExternalReference)
            and isinstance(a_ref, otio.schema.ExternalReference)
        ):
            return False
        if v_ref.target_url != a_ref.target_url:
            return False
        if not _same_source_range(v_item.source_range, a_item.source_range):
            return False
    return True


def _augment_adopted_track(
    a1: otio.schema.Track,
    v1_items: list[otio.core.Item],
    item_streams: list[list[StreamInfo]],
    ordinals: dict[int, int],
) -> int | None:
    """Stamp stream#0 Resolve_OTIO metadata onto an adopted A1's existing clips.

    A1 is an item-for-item mirror of V1 (verified by ``_a1_mirrors_v1``), so
    each A1 clip is matched to the V1 clip at the same position to inherit its
    Link Group ID and stream#0 channel layout -- no clip is created or replaced
    (ADR-NI-10 rev.2 adoption). A1's clips were already timecode-shifted by
    conform's all-track shift pass, so they are not re-shifted here. A V1 item
    with no stream#0 audio (a Gap, or a video-only source) leaves the mirrored
    A1 item untouched. Returns the adopted track's channel count, or None.
    """
    track_channels: int | None = None
    for a1_item, v1_item, streams in zip(list(a1), v1_items, item_streams, strict=True):
        if isinstance(v1_item, otio.schema.Gap) or not streams:
            continue
        stream = streams[0]
        channels = stream.channels if stream.channels is not None else 1
        if track_channels is None:
            track_channels = channels
        a1_item.metadata[RESOLVE_OTIO_KEY] = _channel_metadata(
            channels, 0, ordinals.get(id(v1_item))
        )
    return track_channels


def _stamp_audio_type(track: otio.schema.Track, channels: int | None) -> str | None:
    """Set a track's Resolve_OTIO Audio Type from its channel count.

    Returns any warning raised while mapping the channel count (e.g. a channel
    count outside the supported 1/2 range), or None.
    """
    audio_type, warn = _audio_type(channels if channels is not None else 1)
    track.metadata[RESOLVE_OTIO_KEY] = {"Audio Type": audio_type}
    return warn


def _mirror_audio_tracks(
    timeline: otio.schema.Timeline,
    v1: otio.schema.Track,
    media_infos: Mapping[str, MediaInfo],
    ordinals: dict[int, int],
) -> list[str]:
    """Mirror V1's item sequence onto N audio tracks (N = max audio streams).

    N is the maximum number of audio streams found across all V1 clips'
    resolved MediaInfo. Each resulting audio track corresponds to a single
    audio *stream* (which may itself carry multiple channels, e.g. stereo),
    not to a single channel.

    A1 rule (ADR-NI-10 rev.2): if an existing (Audio-kind) track is already
    present and non-empty, its behavior depends on whether it mirrors V1
    item-for-item (``_a1_mirrors_v1``):
      * Mirror match (the create tools' ``_add_full_clip`` layout, where V1 and
        A1 carry the same source clip): the existing A1 is *adopted* as stream
        #0 -- its clips are stamped with Resolve_OTIO metadata in place and
        A2..AN are appended for any further audio streams. No warning.
      * Non-match (e.g. an unrelated pre-existing bgm track): mirror expansion
        is skipped entirely (no new tracks, no Resolve_OTIO audio metadata) and
        a warning is returned. The all-track timecode shift already applied to
        that track's clips regardless.
    An existing *empty* Audio track is reused for stream #0; further streams
    append new tracks.

    For a source with fewer audio streams than N, that position is filled
    with a Gap of the same duration as the V1 item (clip or Gap) it mirrors.
    """
    warnings: list[str] = []

    v1_items = list(v1)
    item_streams = [_audio_streams(item, media_infos) for item in v1_items]
    max_streams = max((len(streams) for streams in item_streams), default=0)
    if max_streams == 0:
        return warnings

    existing_audio = [
        t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio
    ]
    a1 = existing_audio[0] if existing_audio else None

    if a1 is not None and len(a1) > 0:
        if not _a1_mirrors_v1(a1, v1_items):
            warnings.append(
                "existing non-empty audio track found (e.g. a pre-existing bgm "
                "track); audio mirroring skipped for this timeline"
            )
            return warnings
        # Mirror-matching A1 (create tools' _add_full_clip layout): adopt it as
        # stream #0 in place, then append A2..AN for any further audio streams.
        adopted_channels = _augment_adopted_track(a1, v1_items, item_streams, ordinals)
        warn = _stamp_audio_type(a1, adopted_channels)
        if warn is not None:
            warnings.append(warn)
        for stream_idx in range(1, max_streams):
            new_track = otio.schema.Track(name="", kind=otio.schema.TrackKind.Audio)
            timeline.tracks.append(new_track)
            channels = _fill_mirror_track(
                new_track, stream_idx, v1_items, item_streams, ordinals
            )
            warn = _stamp_audio_type(new_track, channels)
            if warn is not None:
                warnings.append(warn)
        return warnings

    tracks: list[otio.schema.Track] = []
    remaining = max_streams
    if a1 is not None:
        tracks.append(a1)
        remaining -= 1
    for _ in range(remaining):
        new_track = otio.schema.Track(name="", kind=otio.schema.TrackKind.Audio)
        timeline.tracks.append(new_track)
        tracks.append(new_track)

    for stream_idx, track in enumerate(tracks):
        channels = _fill_mirror_track(
            track, stream_idx, v1_items, item_streams, ordinals
        )
        warn = _stamp_audio_type(track, channels)
        if warn is not None:
            warnings.append(warn)

    return warnings


def _apply_resolve_metadata(
    timeline: otio.schema.Timeline,
    v1: otio.schema.Track,
    ordinals: dict[int, int],
) -> None:
    """Stamp Link Group ID onto V1 clips and the idempotency marker onto timeline.

    Runs unconditionally (even when audio mirroring above degenerated), so
    that a second conform_timeline_for_nle call is always a guaranteed no-op
    once a timeline has been through this function (ADR-NI-10).
    """
    for item in v1:
        if isinstance(item, otio.schema.Clip):
            ordinal = ordinals.get(id(item))
            if ordinal is not None:
                item.metadata[RESOLVE_OTIO_KEY] = {"Link Group ID": ordinal}

    timeline.metadata[RESOLVE_OTIO_KEY] = {
        "Resolve OTIO Meta Version": RESOLVE_OTIO_META_VERSION
    }
