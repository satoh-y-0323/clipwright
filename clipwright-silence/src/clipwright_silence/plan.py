"""plan.py — Pure logic for deriving KEEP intervals from silence intervals.

Does not execute ffmpeg at all. Performs interval arithmetic on float seconds
and delegates OTIO conversion to the detect layer (AD-2/AD-3 design policy).
"""

from __future__ import annotations

from clipwright_silence.schemas import DetectSilenceOptions

# CR-Q-004: Floating-point comparison tolerance. Used to preserve boundary
# equality (DC-AM-001: min_keep equal preservation / _merge_intervals adjacent merging).
_EPSILON = 1e-9


def derive_keep_ranges(
    total_duration_sec: float,
    silence_intervals: list[tuple[float, float]],
    options: DetectSilenceOptions,
) -> list[tuple[float, float]]:
    """Derive KEEP intervals from a list of silence intervals.

    Processing flow (AD-3):
    1. Sort silence intervals by start time.
    2. Invert [0, total_duration_sec] against silence intervals to get KEEP intervals.
       - Zero silence -> [(0.0, total_duration_sec)] as a single interval.
       - All silence -> empty list.
    3. Extend each KEEP by padding and clamp to [0, total].
    4. Merge overlapping KEEPs (DC-GP-001 short-silence fill-in).
    5. Discard intervals shorter than min_keep_duration (DC-AM-001 opt-in).

    Args:
        total_duration_sec: Total duration of the source media (seconds).
        silence_intervals: List of silence intervals. Each element is
            (start_sec, end_sec).
        options: DetectSilenceOptions. Uses padding / min_keep_duration.
                 (silence_threshold_db / min_silence_duration are the silencedetect
                 layer's responsibility and are not referenced in this function.)

    Returns:
        List of KEEP intervals. Each element is a tuple[float, float]
        of (start_sec, end_sec).
        Sorted by time, non-overlapping.
    """
    total = total_duration_sec
    padding = options.padding
    min_keep = options.min_keep_duration

    # 1. Sort silence intervals.
    sorted_silence = sorted(silence_intervals, key=lambda iv: iv[0])

    # 2. Invert: subtract silence intervals from [0, total] to get KEEPs.
    keeps: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted_silence:
        if s_start > cursor:
            keeps.append((cursor, s_start))
        # Advance cursor to end of silence (handles overlapping silences).
        cursor = max(cursor, s_end)
    # Trailing speech interval.
    if cursor < total:
        keeps.append((cursor, total))

    # 3. Padding extension + clamp.
    if padding > 0.0:
        padded: list[tuple[float, float]] = []
        for start, end in keeps:
            new_start = max(0.0, start - padding)
            new_end = min(total, end + padding)
            padded.append((new_start, new_end))
        keeps = padded

    # 4. Merge overlapping intervals (DC-GP-001).
    keeps = _merge_intervals(keeps)

    # 5. Discard intervals shorter than min_keep_duration (default 0.0 = no discard).
    if min_keep > 0.0:
        # DC-AM-001: Use _EPSILON to preserve intervals equal to min_keep
        keeps = [
            (start, end) for start, end in keeps if (end - start) >= min_keep - _EPSILON
        ]

    return keeps


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge overlapping intervals and return a sorted, non-overlapping list.

    Intervals do not need to be pre-sorted by start time (sorted internally).
    """
    if not intervals:
        return []

    sorted_ivs = sorted(intervals, key=lambda iv: iv[0])
    merged: list[tuple[float, float]] = [sorted_ivs[0]]

    for start, end in sorted_ivs[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + _EPSILON:
            # Overlapping or adjacent -> merge.
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged
