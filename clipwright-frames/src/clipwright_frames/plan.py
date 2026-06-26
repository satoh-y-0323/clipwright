"""plan.py — Pure planning logic for frame extraction.

All functions are IO-free and subprocess-free; they build data structures only.
"""

from __future__ import annotations

from typing import Literal

import opentimelineio as otio

from clipwright_frames.schemas import ExtractFramesOptions


def compute_interval_timestamps(
    duration_sec: float, interval_sec: float
) -> list[float]:
    """Compute timestamps at i*interval_sec where i*interval_sec < duration_sec.

    Returns an empty list when interval_sec strictly exceeds duration_sec.
    The timestamp at i=0 (0.0) is included only when interval_sec <= duration_sec.
    """
    if interval_sec > duration_sec:
        return []
    result: list[float] = []
    i = 0
    while True:
        ts = i * interval_sec
        if ts >= duration_sec:
            break
        result.append(ts)
        i += 1
    return result


def compute_timestamps_mode(
    timestamps: list[float],
    duration_sec: float,
) -> tuple[list[float], list[float]]:
    """Partition timestamps into (kept, skipped) by range [0, duration_sec).

    Deduplicates timestamps before partitioning. Both output lists are sorted.
    """
    unique = sorted(set(timestamps))
    kept: list[float] = []
    skipped: list[float] = []
    for ts in unique:
        if 0.0 <= ts < duration_sec:
            kept.append(ts)
        else:
            skipped.append(ts)
    return kept, skipped


def scene_marker_seconds(markers: list[otio.schema.Marker]) -> list[float]:
    """Extract sorted seconds from OTIO marker list.

    Converts each marker's start time to seconds via value / rate.
    """
    seconds: list[float] = []
    for marker in markers:
        start = marker.marked_range.start_time
        seconds.append(start.value / start.rate)
    return sorted(seconds)


def build_fps_command(
    ffmpeg: str,
    media: str,
    out_pattern: str,
    options: ExtractFramesOptions,
) -> list[str]:
    """Build ffmpeg command for interval mode (fps filter).

    When max_width is set, fps and scale filters are combined into a single -vf
    to avoid the ffmpeg "multiple -vf" error.
    JPEG output appends -q:v {quality}; PNG does not use -q:v.
    """
    interval_sec = options.interval_sec
    # strip trailing .0 so the ffmpeg fps filter reads e.g. '1/10' not '1/10.0'
    if interval_sec == int(interval_sec):
        interval_str = str(int(interval_sec))
    else:
        interval_str = str(interval_sec)

    fps_filter = f"fps=1/{interval_str}"

    if options.max_width is not None:
        vf = f"{fps_filter},scale='min({options.max_width},iw)':-2"
    else:
        vf = fps_filter

    cmd: list[str] = [ffmpeg, "-i", media, "-vf", vf]

    if options.format == "jpeg":
        cmd += ["-q:v", str(options.quality)]

    # -start_number 0 aligns ffmpeg's 1-based default frame numbering to 0-based,
    # so frame_00000.jpg matches frame_filename(0) and avoids path mismatch.
    cmd += ["-start_number", "0"]
    cmd.append(out_pattern)
    return cmd


def build_single_frame_command(
    ffmpeg: str,
    media: str,
    ts: float,
    out_path: str,
    options: ExtractFramesOptions,
) -> list[str]:
    """Build ffmpeg command for scene/timestamps mode (single frame extraction).

    -ss is placed before -i for fast input seeking. The timestamp is converted to
    str so the caller can pass the list directly to subprocess without re-casting.
    max_width adds a scale-only -vf filter (no fps filter).
    JPEG output appends -q:v {quality}; PNG does not.
    """
    cmd: list[str] = [ffmpeg, "-ss", str(ts), "-i", media, "-frames:v", "1"]

    if options.max_width is not None:
        cmd += ["-vf", f"scale='min({options.max_width},iw)':-2"]

    if options.format == "jpeg":
        cmd += ["-q:v", str(options.quality)]

    cmd.append(out_path)
    return cmd


def compute_scene_segment_timestamps(
    boundaries: list[float],
    duration_sec: float,
    anchor: Literal["midpoint", "start"],
) -> list[float]:
    """Compute representative frame timestamps for scene segments.

    Derives shot segments from scene boundary positions and returns one
    representative timestamp per segment according to the anchor mode.
    Always yields at least one entry: when no valid boundaries exist, the
    entire media is treated as a single segment.

    Args:
        boundaries: Scene boundary positions in seconds. Duplicates and values
            outside (0, duration_sec) are ignored internally.
        duration_sec: Total media duration in seconds.
        anchor: Sampling position within each segment. "start" returns the
            segment's first second; "midpoint" returns the segment midpoint.

    Returns:
        Sorted list of timestamps, each in [0, duration_sec).
    """
    # Deduplicate and keep only boundaries strictly inside (0, duration_sec).
    filtered = [b for b in sorted(set(boundaries)) if 0.0 < b < duration_sec]
    edges = [0.0] + filtered + [duration_sec]
    result: list[float] = []
    for i in range(len(edges) - 1):
        s, e = edges[i], edges[i + 1]
        if e <= s:
            continue  # skip zero-length segments (defensive)
        if anchor == "start":
            result.append(s)
        else:  # "midpoint"
            result.append((s + e) / 2)
    return result


def frame_filename(index: int, fmt: str) -> str:
    """Generate zero-padded frame filename: frame_%05d.{ext}.

    Maps format 'jpeg' -> 'jpg'; 'png' -> 'png'.
    """
    ext_map: dict[str, str] = {"jpeg": "jpg", "png": "png"}
    ext = ext_map.get(fmt, fmt)
    return f"frame_{index:05d}.{ext}"
