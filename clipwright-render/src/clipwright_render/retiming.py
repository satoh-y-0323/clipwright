"""retiming.py — Pure-logic layer for source-time → program-time remapping.

Responsibilities (no side effects):
  - Build a ProgramTimeMap from a list of KeptRanges.
  - Remap a source-time window [src_start, src_end) onto the program timeline.
  - Minimal SRT read/write using RationalTime (ADR-2).

This module contains NO ffmpeg calls, NO OTIO timeline reads, and NO path
validation.  All arithmetic stays in RationalTime until the caller converts
to float seconds at the ffmpeg edge (NFR-2 / D7).

SRT I/O note (ADR-2):
  SrtCue differs from wrap.Cue by holding RationalTime values (not timecode
  strings).  retiming needs numeric RationalTime to feed remap_window; keeping
  string timecodes would require an extra conversion that could introduce float
  drift.  serialize_srt byte structure is identical to transcribe.to_srt /
  wrap._serialize_srt (1-based index, blank-line separated, single trailing
  newline).

  Timecode quantisation (SRT vs drawtext):
    SRT   -> millisecond integer: int(round(sec * 1000))
             [round-half-even / banker's rounding]
    drawtext -> second 6-decimal:  round(float(rt.to_seconds()), 6)
  Both conventions are documented in callers; this module implements only the
  SRT path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import opentimelineio as otio

from clipwright_render.plan import KeptRange, _is_warp_identity

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgramSegment:
    """One kept range mapped onto the program timeline (RationalTime-based).

    source_start / source_end define the half-open source interval [start, end).
    program_start is the cumulative program offset at the beginning of this segment.
    time_scalar is the playback speed multiplier (>0; 1.0 = no warp).
    """

    source_start: otio.opentime.RationalTime
    source_end: otio.opentime.RationalTime
    program_start: otio.opentime.RationalTime
    time_scalar: float


@dataclass(frozen=True)
class ProgramTimeMap:
    """Ordered program segments and their source-to-program mapping.

    Built from a KeptRangeList via build_program_time_map.
    has_cut  — True when kept ranges are non-contiguous in source (gap/cut exists).
    has_warp — True when any segment has a non-identity time_scalar.
    """

    segments: list[ProgramSegment]
    has_cut: bool
    has_warp: bool


@dataclass(frozen=True)
class ProgramWindow:
    """A remapped interval on the program timeline (half-open [start, end))."""

    program_start: otio.opentime.RationalTime
    program_end: otio.opentime.RationalTime


@dataclass(frozen=True)
class RemapResult:
    """Result of remap_window: program windows and disposition flags.

    windows  — list of program-time windows; empty means fully dropped.
    dropped  — True when the source window fell entirely in a removed region.
    split    — True when >= 2 output windows were produced (crossed a cut).
    clipped  — True when the input window was partially outside kept ranges.
    shifted  — True when any program_start != corresponding source_start.

    Warning strings are NOT generated here (ADR-4); callers compose them from
    these flags.
    """

    windows: list[ProgramWindow]
    dropped: bool
    split: bool
    clipped: bool
    shifted: bool


@dataclass(frozen=True)
class SrtCue:
    """A single SRT cue with RationalTime start/end.

    Differs from wrap.Cue which keeps timecode strings; retiming needs numeric
    RationalTime to feed remap_window without intermediate float conversions.
    """

    start: otio.opentime.RationalTime
    end: otio.opentime.RationalTime
    text: str  # Multi-line text preserved with '\n'


# ---------------------------------------------------------------------------
# build_program_time_map
# ---------------------------------------------------------------------------


def build_program_time_map(ranges: list[KeptRange]) -> ProgramTimeMap:
    """Construct the source→program map by walking kept ranges in order.

    Program offset accumulates as program_dur = source_dur / time_scalar.
    All arithmetic is performed in RationalTime (no float seconds until the
    ffmpeg edge — NFR-2 / D7).

    has_warp: any(not _is_warp_identity(r.time_scalar) for r in ranges).
    has_cut:  multiple KeptRanges exist AND source ranges are non-contiguous
              (previous source_end != next source_start).  A single KeptRange
              is always has_cut=False (ADR-5).

    Args:
        ranges: Ordered list of kept ranges from resolve_kept_ranges.

    Returns:
        ProgramTimeMap with segments, has_cut, and has_warp.
    """
    has_warp = any(not _is_warp_identity(r.time_scalar) for r in ranges)

    segments: list[ProgramSegment] = []
    has_cut = False

    # Use a rate consistent with the source ranges for program offset arithmetic.
    # Fall back to 30 fps if ranges is empty.
    map_rate = ranges[0].source_range.start_time.rate if ranges else 30.0

    program_offset = otio.opentime.RationalTime(0, map_rate)

    for idx, r in enumerate(ranges):
        src_start = r.source_range.start_time
        src_end = r.source_range.end_time_exclusive()

        # has_cut: detect non-contiguous source (ADR-5)
        if idx > 0:
            prev = ranges[idx - 1]
            prev_source_end = prev.source_range.end_time_exclusive()
            # Rescale to the same rate before comparing to avoid rate mismatch.
            prev_end_rescaled = prev_source_end.rescaled_to(src_start.rate)
            if prev_end_rescaled != src_start:
                has_cut = True

        segments.append(
            ProgramSegment(
                source_start=src_start,
                source_end=src_end,
                program_start=program_offset,
                time_scalar=r.time_scalar,
            )
        )

        # Advance program_offset by source_dur / time_scalar.
        # To stay in integer RationalTime arithmetic:
        #   src_dur.value / time_scalar gives the scaled value at src_dur.rate.
        src_dur = r.source_range.duration
        scaled_value = src_dur.value / r.time_scalar
        prog_dur = otio.opentime.RationalTime(scaled_value, src_dur.rate)
        # Ensure program_offset is at the same rate as prog_dur before addition.
        program_offset = program_offset.rescaled_to(prog_dur.rate) + prog_dur

    return ProgramTimeMap(segments=segments, has_cut=has_cut, has_warp=has_warp)


# ---------------------------------------------------------------------------
# remap_window
# ---------------------------------------------------------------------------


def remap_window(
    tmap: ProgramTimeMap,
    src_start: otio.opentime.RationalTime,
    src_end: otio.opentime.RationalTime,
) -> RemapResult:
    """Map a source-time window [src_start, src_end) onto the program timeline.

    For each ProgramSegment, the half-open intersection
        [src_start, src_end) ∩ [seg.source_start, seg.source_end)
    is computed in RationalTime.  For each non-empty intersection::

        program_start = seg.program_start + (isect_start - seg.source_start)
                        / time_scalar
        program_end   = seg.program_start + (isect_end   - seg.source_start)
                        / time_scalar

    The division by time_scalar is implemented as integer value arithmetic at the
    source rate (rate fixed to seg.source_start.rate) to avoid float error
    accumulation (NFR-2).  Non-divisible warp values are absorbed at the final
    rounding stage by callers (_to_seconds / ms quantisation).

    Boundary rule: end == seg.source_end is NOT included in this segment
    (half-open).  The boundary point is handled by the next segment.

    Disposition flags (ADR-4 — no warning strings generated here):
        dropped  — all segments missed (fully in removed region)
        split    — >= 2 output windows
        clipped  — input window partially outside all kept ranges
        shifted  — any program_start != corresponding source_start

    Args:
        tmap:      ProgramTimeMap built by build_program_time_map.
        src_start: Start of the source window (inclusive).
        src_end:   End of the source window (exclusive).

    Returns:
        RemapResult with windows and disposition flags.
    """
    windows: list[ProgramWindow] = []

    for seg in tmap.segments:
        # Half-open intersection: [max(src_start, seg.source_start),
        #                          min(src_end, seg.source_end))
        isect_start = _rt_max(src_start, seg.source_start)
        isect_end = _rt_min(src_end, seg.source_end)

        # Rescale to the same rate for comparison
        rate = seg.source_start.rate
        isect_start_r = isect_start.rescaled_to(rate)
        isect_end_r = isect_end.rescaled_to(rate)

        # Non-empty intersection: isect_start < isect_end
        # Boundary: end == seg_end is excluded (half-open — handled by next seg)
        if isect_start_r >= isect_end_r:
            continue

        seg_start_r = seg.source_start.rescaled_to(rate)
        prog_start_r = seg.program_start.rescaled_to(rate)

        # offset_start = isect_start - seg.source_start (at seg rate)
        offset_start_val = isect_start_r.value - seg_start_r.value
        offset_end_val = isect_end_r.value - seg_start_r.value

        # program_start = prog_start + offset_start / time_scalar
        prog_start_val = prog_start_r.value + offset_start_val / seg.time_scalar
        prog_end_val = prog_start_r.value + offset_end_val / seg.time_scalar

        prog_win_start = otio.opentime.RationalTime(prog_start_val, rate)
        prog_win_end = otio.opentime.RationalTime(prog_end_val, rate)

        windows.append(
            ProgramWindow(program_start=prog_win_start, program_end=prog_win_end)
        )

    # --- Disposition flags ---
    dropped = len(windows) == 0
    split = len(windows) >= 2

    # clipped: the total covered source range is smaller than [src_start, src_end)
    # i.e. at least one input edge fell into a removed region.
    clipped = _is_clipped(src_start, src_end, tmap.segments)

    # shifted: any program_start != source_start (rescaled comparison).
    # A window is shifted when its program_start differs from the input src_start.
    # For multiple windows (split), subsequent windows are inherently shifted.
    shifted = False
    if not dropped:
        first_win = windows[0]
        win_rate = first_win.program_start.rate
        src_start_r = src_start.rescaled_to(win_rate)
        # Compare values directly (same rate after rescale)
        if first_win.program_start.value != src_start_r.value:
            shifted = True
        # Additional windows always involve shifted positions
        if not shifted and len(windows) > 1:
            shifted = True

    return RemapResult(
        windows=windows,
        dropped=dropped,
        split=split,
        clipped=clipped,
        shifted=shifted,
    )


def _rt_max(
    a: otio.opentime.RationalTime, b: otio.opentime.RationalTime
) -> otio.opentime.RationalTime:
    """Return the later of two RationalTimes (rescaled to a's rate for comparison)."""
    b_r = b.rescaled_to(a.rate)
    return a if a.value >= b_r.value else b


def _rt_min(
    a: otio.opentime.RationalTime, b: otio.opentime.RationalTime
) -> otio.opentime.RationalTime:
    """Return the earlier of two RationalTimes (rescaled to a's rate for comparison)."""
    b_r = b.rescaled_to(a.rate)
    return a if a.value <= b_r.value else b


def _is_clipped(
    src_start: otio.opentime.RationalTime,
    src_end: otio.opentime.RationalTime,
    segments: list[ProgramSegment],
) -> bool:
    """Return True when [src_start, src_end) is not fully covered by segments.

    Checks whether any part of the input window falls outside all kept ranges.
    """
    if not segments:
        return False  # dropped case; caller sets dropped=True

    rate = src_start.rate
    src_start_v = src_start.rescaled_to(rate).value
    src_end_v = src_end.rescaled_to(rate).value

    # Build the union of intersections at the source rate.
    # covered_start_v / covered_end_v track the total covered interval.
    covered_start_v: float | None = None
    covered_end_v: float | None = None

    for seg in segments:
        seg_start_v = seg.source_start.rescaled_to(rate).value
        seg_end_v = seg.source_end.rescaled_to(rate).value

        isect_start_v = max(src_start_v, seg_start_v)
        isect_end_v = min(src_end_v, seg_end_v)

        if isect_start_v >= isect_end_v:
            continue

        if covered_start_v is None:
            covered_start_v = isect_start_v
            covered_end_v = isect_end_v
        else:
            # Segments are ordered; extend coverage
            covered_end_v = max(covered_end_v, isect_end_v)

    if covered_start_v is None or covered_end_v is None:
        # fully dropped; not clipped
        return False

    # clipped if covered range is strictly smaller than input range
    return bool(covered_start_v > src_start_v or covered_end_v < src_end_v)


# ---------------------------------------------------------------------------
# SRT I/O
# ---------------------------------------------------------------------------

# Matches "HH:MM:SS,mmm" or "HH:MM:SS.mmm" (both separators for robustness)
_TIMECODE_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

_ARROW = " --> "


def _parse_timecode(tc: str, srt_rate: float = 1000.0) -> otio.opentime.RationalTime:
    """Parse "HH:MM:SS,mmm" into RationalTime at srt_rate (default 1000 = ms precision).

    Args:
        tc:       Timecode string ("HH:MM:SS,mmm").
        srt_rate: Rate for the resulting RationalTime (default 1000 for ms precision).

    Returns:
        RationalTime at srt_rate.

    Raises:
        ValueError: If tc does not match the expected timecode format.
    """
    m = _TIMECODE_RE.fullmatch(tc.strip())
    if m is None:
        raise ValueError(f"Invalid SRT timecode: {tc!r}")
    hours, minutes, seconds, ms = (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)),
    )
    total_ms = hours * 3_600_000 + minutes * 60_000 + seconds * 1000 + ms
    return otio.opentime.RationalTime(total_ms, srt_rate)


def _format_srt_timecode(rt: otio.opentime.RationalTime) -> str:
    """Format a RationalTime as "HH:MM:SS,mmm" for SRT output.

    Milliseconds are quantised using Python's round() built-in
    (round-half-even / banker's rounding), matching
    transcribe._format_timecode(sec, ms_separator=",") (ADR-2).

    Note: SRT uses ms-integer rounding; drawtext uses second-6-decimal rounding
    (_to_seconds). Both are correct for their respective outputs.

    Args:
        rt: RationalTime to format.

    Returns:
        Timecode string "HH:MM:SS,mmm".
    """
    total_ms = int(round(rt.to_seconds() * 1000.0))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def format_srt_timecode(rt: otio.opentime.RationalTime) -> str:
    """Public wrapper for SRT timecode formatting (CR-L-3).

    Delegates to _format_srt_timecode.  Provided so that render.py (and any
    other caller) can access the formatter without reaching into a private
    symbol across module boundaries.

    Args:
        rt: RationalTime to format.

    Returns:
        Timecode string "HH:MM:SS,mmm".
    """
    return _format_srt_timecode(rt)


def parse_srt(text: str) -> list[SrtCue]:
    """Parse SRT text into a list of RationalTime-based SrtCues.

    Differs from wrap.Cue which keeps timecode strings; retiming needs numeric
    RationalTime to feed remap_window without intermediate float conversions.

    Empty or whitespace-only input returns [].  Invalid timecodes raise ValueError
    (consistent with wrap's error-on-invalid-timecode approach).

    Args:
        text: Raw SRT file content.

    Returns:
        List of SrtCue with RationalTime start/end and preserved multi-line text.

    Raises:
        ValueError: If any timecode cannot be parsed.
    """
    text = text.strip()
    if not text:
        return []

    cues: list[SrtCue] = []

    # Split on blank lines to get individual cue blocks
    blocks = re.split(r"\n\s*\n", text)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        if len(lines) < 2:
            continue

        # Skip the index line (first line); find the timecode arrow line
        # The index line may be line 0; timecode line is next
        timecode_line_idx = None
        for i, line in enumerate(lines):
            if _ARROW in line:
                timecode_line_idx = i
                break

        if timecode_line_idx is None:
            continue

        tc_line = lines[timecode_line_idx]
        parts = tc_line.split(_ARROW)
        if len(parts) != 2:
            raise ValueError(f"Invalid SRT timecode line: {tc_line!r}")

        start_rt = _parse_timecode(parts[0])
        end_rt = _parse_timecode(parts[1])

        # Text is everything after the timecode line
        text_lines = lines[timecode_line_idx + 1 :]
        cue_text = "\n".join(text_lines)

        cues.append(SrtCue(start=start_rt, end=end_rt, text=cue_text))

    return cues


def serialize_srt(cues: list[SrtCue]) -> str:
    """Serialize RationalTime cues back to SRT format.

    Byte structure is identical to transcribe.to_srt / wrap._serialize_srt:
      - 1-based sequential index
      - "HH:MM:SS,mmm --> HH:MM:SS,mmm" timecode line
      - Cue text (multi-line preserved)
      - Blank line separator between blocks
      - Single trailing newline (last block ends with "\\n", no extra blank line)

    Millisecond quantisation uses Python's round() built-in
    (round-half-even / banker's rounding), matching
    transcribe._format_timecode(sec, ms_separator=",") (ADR-2).

    Note: SRT uses ms-integer rounding; drawtext uses second-6-decimal rounding.
    See _format_srt_timecode for details.

    Args:
        cues: Ordered list of SrtCues to serialize.

    Returns:
        SRT-formatted string.  Empty string when cues is empty.
    """
    if not cues:
        return ""

    blocks: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        start_tc = _format_srt_timecode(cue.start)
        end_tc = _format_srt_timecode(cue.end)
        block = f"{idx}\n{start_tc} --> {end_tc}\n{cue.text}"
        blocks.append(block)

    # Join with blank line separator; add single trailing newline
    return "\n\n".join(blocks) + "\n"
