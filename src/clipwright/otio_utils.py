"""otio_utils.py — OTIO helpers (clip/gap/marker/metadata/summary).

Thin wrapper layer responsible for creating OTIO objects, I/O, and metadata operations.
Time conversions use to_otio_time / from_otio_time imported from schemas.py.
"""

from __future__ import annotations

import collections.abc
import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

import opentimelineio as otio

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.pathpolicy import validate_source_or_basename
from clipwright.schemas import (
    MediaRef,
    RationalTimeModel,
    TimeRangeModel,
    from_otio_time,
    to_otio_time,
)

# ===========================================================================
# Timeline creation and I/O
# ===========================================================================


def new_timeline(name: str) -> otio.schema.Timeline:
    """Create a new Timeline.

    Tracks are created in [V1(Video), A1(Audio)] order per §13.5 DC-AS-001.
    Flat index: 0=V1, 1=A1.
    """
    timeline = otio.schema.Timeline(name=name)

    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)

    timeline.tracks.append(v1)
    timeline.tracks.append(a1)

    return timeline


def load_timeline(path: str) -> otio.schema.Timeline:
    """Load an OTIO file and return a Timeline.

    Validates the path first (symlink rejection + existence, ADR-PP-2 / CWE-59).
    Converts file-system and OTIO parsing errors into ClipwrightError (L-3).
    Catches FileNotFoundError / (OTIOError, ValueError) / OSError from read_from_file
    and re-raises as ClipwrightError with appropriate error codes.
    Non-ClipwrightError exceptions (e.g. RuntimeError) are left unconverted
    (contract boundary L-1). Type mismatches (non-Timeline results) are also wrapped
    as OTIO_ERROR. Raw OTIO exceptions must not reach the caller (L-1 / F-07).
    """
    validate_source_or_basename(
        path,
        message=f"Timeline file not found: {Path(path).name}",
        hint="Verify the timeline path and ensure the file exists.",
    )
    try:
        result = otio.adapters.read_from_file(path)
    except FileNotFoundError as exc:
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Timeline file not found: {Path(path).name}",
            hint="Verify the timeline path and ensure the file exists.",
        ) from exc
    except (otio.exceptions.OTIOError, ValueError) as exc:
        raise ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message=f"Failed to load OTIO file: {Path(path).name}",
            hint=(
                "The file exists but its contents are not valid OTIO JSON."
                " Regenerate the timeline with clipwright tools"
                " (e.g. clipwright_init_project + clipwright_write_timeline),"
                " or specify a different .otio file."
            ),
        ) from exc
    except OSError as exc:
        raise ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message=f"Failed to read OTIO file: {Path(path).name}",
            hint="Check that the file exists and is readable, then try again.",
        ) from exc

    if not isinstance(result, otio.schema.Timeline):
        result_type = type(result).__name__
        raise ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message=f"OTIO file is not a Timeline: {result_type}",
            hint="Specify a valid .otio timeline file.",
        )
    return result


def save_timeline(timeline: otio.schema.Timeline, path: str) -> None:
    """Atomically save a Timeline (temp → os.replace).

    Existing files are not corrupted even if the write is interrupted mid-way.
    A temp file with the .otio extension is created in the same directory,
    then replaced atomically with os.replace once writing completes.

    OTIO selects its adapter by file extension, so the temp file must also use .otio.
    """
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".otio")
    try:
        os.close(fd)
        otio.adapters.write_to_file(timeline, tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        # Broad catch only to clean up the temp file; always re-raise (NL-2).
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# ===========================================================================
# Adding clips, gaps, and markers
# ===========================================================================


def add_clip(
    track: otio.schema.Track,
    media: MediaRef,
    source_range: TimeRangeModel,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> otio.schema.Clip:
    """Append a clip to a Track.

    Sets the media target_url as an ExternalReference. When media.available_range
    is set, it is also wired into ExternalReference.available_range (ADR-4);
    when None, available_range is left unset (backward compatible).
    Returns the added Clip object.
    """
    ref = otio.schema.ExternalReference(target_url=media.target_url)
    if media.available_range is not None:
        ref.available_range = otio.opentime.TimeRange(
            start_time=to_otio_time(media.available_range.start_time),
            duration=to_otio_time(media.available_range.duration),
        )
    sr = otio.opentime.TimeRange(
        start_time=to_otio_time(source_range.start_time),
        duration=to_otio_time(source_range.duration),
    )
    clip = otio.schema.Clip(
        name=name or "",
        media_reference=ref,
        source_range=sr,
    )
    if metadata is not None:
        clip.metadata["clipwright"] = metadata
    track.append(clip)
    return clip


def add_gap(
    track: otio.schema.Track,
    duration: RationalTimeModel,
) -> otio.schema.Gap:
    """Append a gap to a Track.

    Constructs a source_range from the given duration and creates a Gap.
    Returns the added Gap object.
    """
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, duration.rate),
        duration=to_otio_time(duration),
    )
    gap = otio.schema.Gap(source_range=sr)
    track.append(gap)
    return gap


def add_marker(
    item: otio.core.Item,
    marked_range: TimeRangeModel,
    name: str,
    color: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> otio.schema.Marker:
    """Attach a Marker to an item (Track, Clip, etc.).

    Per §13.5 DC-GP-001: AddMarkerOp attaches to the track itself (item=Track).
    No clip needs to exist; an empty track is valid.
    Returns the added Marker object.
    """
    mr = otio.opentime.TimeRange(
        start_time=to_otio_time(marked_range.start_time),
        duration=to_otio_time(marked_range.duration),
    )
    marker_kwargs: dict[str, Any] = {"name": name, "marked_range": mr}
    if color is not None:
        marker_kwargs["color"] = color
    marker = otio.schema.Marker(**marker_kwargs)
    if metadata is not None:
        marker.metadata["clipwright"] = metadata
    item.markers.append(marker)
    return marker


# ===========================================================================
# Clipwright metadata (under metadata["clipwright"])
# ===========================================================================


def set_clipwright_metadata(obj: Any, data: dict[str, Any]) -> None:
    """Set data under metadata["clipwright"] on an OTIO object (convention §4.3).

    Does not pollute other keys. On overwrite, the entire clipwright key is replaced.

    For partial updates, retrieve the dict with get_clipwright_metadata, modify it,
    then call set again (L-5). Example:

        existing = get_clipwright_metadata(obj)
        existing["confidence"] = 0.95
        set_clipwright_metadata(obj, existing)
    """
    obj.metadata["clipwright"] = data


def get_clipwright_metadata(obj: Any) -> dict[str, Any]:
    """Return data stored under metadata["clipwright"] on an OTIO object.

    Returns an empty dict if no metadata has been set.
    """
    return dict(obj.metadata.get("clipwright", {}))


# ===========================================================================
# Marker queries
# ===========================================================================


def get_markers(
    timeline: otio.schema.Timeline,
    kind: str | None = None,
) -> list[otio.schema.Marker]:
    """Collect Marker objects from all tracks and clips in a Timeline.

    Traversal order: tracks in track order → track markers first, then clip
    markers in item order → individual markers in marker list order.
    Time-based sorting is intentionally omitted so that the caller controls
    ordering and round-trip stability is preserved even when two markers share
    the same timestamp.

    When *kind* is None all markers are returned.  When *kind* is specified,
    only markers whose ``metadata["clipwright"]["kind"]`` equals *kind* are
    returned.  Markers that have no ``metadata["clipwright"]`` dict, or where
    that value is not a dict, are excluded when *kind* is given (defensive
    pattern matching ``_marker_to_dict``).
    """
    result: list[otio.schema.Marker] = []
    for track in timeline.tracks:
        # Track-level markers first
        for marker in track.markers:
            if _marker_matches_kind(marker, kind):
                result.append(marker)
        # Clip-level markers in item order
        for item in track:
            if isinstance(item, otio.schema.Clip):
                for marker in item.markers:
                    if _marker_matches_kind(marker, kind):
                        result.append(marker)
    return result


def _marker_matches_kind(marker: otio.schema.Marker, kind: str | None) -> bool:
    """Return True when *marker* matches the given kind filter.

    When *kind* is None every marker matches.
    When *kind* is set, the marker must have metadata["clipwright"]["kind"] == kind.
    Missing or non-dict clipwright metadata is treated as no-match (exclusive).
    """
    if kind is None:
        return True
    cw_meta = marker.metadata.get("clipwright", {})
    # OTIO stores metadata values as AnyDictionary, not a plain dict,
    # so use Mapping to accept both types.
    if not isinstance(cw_meta, collections.abc.Mapping):
        return False
    return cw_meta.get("kind") == kind


# ===========================================================================
# Timeline summary (§13.5 DC-AM-001 re: return all items, no truncation)
# ===========================================================================


def summarize_timeline(timeline: otio.schema.Timeline) -> dict[str, Any]:
    """Return statistics and the full marker list for a Timeline.

    §13.5 DC-AM-001 re: always returns all items (no truncation).
    The threshold-50 truncation is the responsibility of server.read_timeline;
    this function does not truncate.

    Return value keys:
      - clip_count: int
      - gap_count: int
      - marker_count: int
      - total_duration: RationalTimeModel (§13.5 DC-AM-002 re)
      - markers: list[dict] — [{name, time, kind}] full list
      - warnings: list[str] — non-fatal warnings (e.g. duration failures) (M-4)

    total_duration computation rules (§13.5 DC-AM-002 re):
      - Maximum of all track lengths (not the sum)
      - rate = rate of the V1 track (kind=Video) if it has clips, otherwise 1000.0
      - Returns RationalTime(0, global rate) when there are no clips
    """
    clip_count = 0
    gap_count = 0
    markers: list[dict[str, Any]] = []
    warnings: list[str] = []

    # Determine global rate: read rate from the first clip in V1
    global_rate = _resolve_global_rate(timeline)

    # Iterate all tracks to count items and collect markers
    track_durations_sec: list[float] = []
    for track in timeline.tracks:
        for item in track:
            if isinstance(item, otio.schema.Clip):
                clip_count += 1
            elif isinstance(item, otio.schema.Gap):
                gap_count += 1

        # Compute track duration in seconds (record to warnings on OTIO exception)
        track_dur, warn = _track_duration_sec(track)
        track_durations_sec.append(track_dur)
        if warn is not None:
            warnings.append(warn)

        # Collect markers attached to the track itself
        for marker in track.markers:
            markers.append(_marker_to_dict(marker))

    # Collect markers on clips (tracks already processed in the loop above)
    for track in timeline.tracks:
        for item in track:
            if isinstance(item, otio.schema.Clip):
                for marker in item.markers:
                    markers.append(_marker_to_dict(marker))

    # marker_count is the total number of markers (track + clip markers)
    marker_count = len(markers)

    # total_duration: maximum of all track lengths
    max_sec = max(track_durations_sec) if track_durations_sec else 0.0

    if max_sec == 0.0:
        total_duration = RationalTimeModel(value=0.0, rate=global_rate)
    else:
        # Express the maximum in global_rate units
        total_value = max_sec * global_rate
        total_duration = RationalTimeModel(value=total_value, rate=global_rate)

    return {
        "clip_count": clip_count,
        "gap_count": gap_count,
        "marker_count": marker_count,
        "total_duration": total_duration,
        "markers": markers,
        "warnings": warnings,
    }


def _resolve_global_rate(timeline: otio.schema.Timeline) -> float:
    """Determine the global rate.

    If the V1 (kind=Video) track has at least one clip, use the rate of its first clip.
    Otherwise (V1 is empty or absent), return 1000.0.
    """
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            for item in track:
                if isinstance(item, otio.schema.Clip) and item.source_range is not None:
                    return float(item.source_range.duration.rate)
            # V1 exists but has no clips
            break
    return 1000.0


def _track_duration_sec(track: otio.schema.Track) -> tuple[float, str | None]:
    """Return the total duration of a track in seconds.

    Uses OTIO Track.duration(). Returns 0.0 when the track has no clips.
    On OTIO exception, returns (0.0, warning_message) (M-4).
    On success, returns (seconds, None).
    """
    try:
        dur = track.duration()
        return float(dur.to_seconds()), None
    except otio.exceptions.OTIOError:
        # Do not include the OTIO error string to avoid leaking internals (NF-01).
        warn_msg = f"Failed to get duration for track '{track.name}'."
        return 0.0, warn_msg


def _marker_to_dict(marker: otio.schema.Marker) -> dict[str, Any]:
    """Convert a Marker object to a dictionary."""
    time_model = from_otio_time(marker.marked_range.start_time)
    cw_meta = marker.metadata.get("clipwright", {})
    kind = cw_meta.get("kind", "") if isinstance(cw_meta, dict) else ""
    return {
        "name": marker.name,
        "time": time_model,
        "kind": kind,
    }
