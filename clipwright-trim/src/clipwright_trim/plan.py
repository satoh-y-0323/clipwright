"""plan.py — Pure interval arithmetic for clipwright-trim.

No I/O, no subprocess, no OTIO. All operations work on float-second tuples.

Design decisions:
- Both keep and drop empty -> full-duration passthrough [(0, duration)] (FR-2 literal,
  confirmed override of ADR-4; server.py passes TrimOptions() for options=None).
- Both keep and drop non-empty -> INVALID_INPUT (AC-4).
- Keep mode: enumeration order preserved, no merge (ADR-3).
- Drop mode: padding shrinks the drop (content-protective direction, ADR-5).
- _merge_intervals is copied, not imported from clipwright-silence (ADR-7).
"""

from __future__ import annotations

from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_trim.schemas import TrimOptions

# Floating-point comparison tolerance (same name as silence plan.py _EPSILON — ADR-7).
_EPSILON = 1e-9


def derive_keep_ranges(
    duration_sec: float,
    options: TrimOptions,
) -> tuple[list[tuple[float, float]], list[str], str]:
    """Convert keep/drop TrimOptions into normalized keep ranges in seconds.

    Returns a 3-tuple of (keep_ranges, warnings, mode).
    keep_ranges is a list of (start_sec, end_sec) float tuples.
    warnings contains non-fatal clamp notices.
    mode is "keep" or "drop" (passthrough is represented as "keep").

    Raises:
        ClipwrightError(INVALID_INPUT) for:
          - Both keep and drop non-empty (mutual exclusion, AC-4).
          - A TrimRange with start_sec >= end_sec (AC-3).
          - A range fully outside [0, duration] after clamp.
          - Empty computed result (drop covers full duration, AC-5).
    """
    has_keep = len(options.keep) > 0
    has_drop = len(options.drop) > 0

    # Mutual exclusion check (AC-4)
    if has_keep and has_drop:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Both keep and drop were provided.",
            hint="Provide exactly one of keep or drop.",
        )

    # Both empty -> full-duration passthrough (FR-2 literal, override of ADR-4)
    # Passthrough is reported as "keep" mode (ADR-1).
    if not has_keep and not has_drop:
        return [(0.0, duration_sec)], [], "keep"

    if has_keep:
        keep_ranges, warnings = _process_keep(duration_sec, options)
        return keep_ranges, warnings, "keep"
    else:
        keep_ranges, warnings = _process_drop(duration_sec, options)
        return keep_ranges, warnings, "drop"


def _process_keep(
    duration: float,
    options: TrimOptions,
) -> tuple[list[tuple[float, float]], list[str]]:
    """Process keep mode: enumeration order preserved, padding outward, no merge."""
    padding = options.padding_sec
    result: list[tuple[float, float]] = []
    warnings: list[str] = []

    for r in options.keep:
        s, e = r.start_sec, r.end_sec

        # Pre-validation: start_sec >= end_sec (AC-3)
        if s >= e - _EPSILON:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="A trim range has start_sec >= end_sec.",
                hint="Ensure start_sec < end_sec for every range.",
            )

        # Apply padding outward
        ps = s - padding
        pe = e + padding

        # Detect if clamping will be needed (before clamping)
        needs_clamp = ps < 0.0 - _EPSILON or pe > duration + _EPSILON

        # Clamp to [0, duration]
        cs = max(0.0, ps)
        ce = min(duration, pe)

        if needs_clamp:
            warnings.append("A keep range was clamped to the media boundary.")

        # After clamp, check for degenerate range (entirely outside [0, duration])
        if ce - cs <= _EPSILON:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="A trim range falls entirely outside the media duration.",
                hint="Specify ranges within the media duration.",
            )

        result.append((cs, ce))

    return result, warnings


def _process_drop(
    duration: float,
    options: TrimOptions,
) -> tuple[list[tuple[float, float]], list[str]]:
    """Process drop mode: sort, merge drops, complement against [0, duration].

    Padding applied inward to each drop (shrinks drop, keep grows — ADR-5).
    Degenerate drops after padding are silently discarded.
    """
    padding = options.padding_sec
    warnings: list[str] = []

    # Pre-validate all drop ranges: start_sec >= end_sec is an error
    for r in options.drop:
        if r.start_sec >= r.end_sec - _EPSILON:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="A trim range has start_sec >= end_sec.",
                hint="Ensure start_sec < end_sec for every range.",
            )

    # Apply inward padding (shrinks the drop, lengthens the keep — ADR-5)
    padded_drops: list[tuple[float, float]] = []
    for r in options.drop:
        ds = r.start_sec + padding
        de = r.end_sec - padding
        # Clamp to [0, duration]
        ds = max(0.0, ds)
        de = min(duration, de)
        # Discard degenerate drops (after padding they became start >= end)
        if ds >= de - _EPSILON:
            continue
        padded_drops.append((ds, de))

    # Sort and merge drops
    merged_drops = _merge_intervals(padded_drops)

    # Complement against [0, duration]
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for ds, de in merged_drops:
        if ds > cursor + _EPSILON:
            keep.append((cursor, ds))
        cursor = max(cursor, de)
    if cursor < duration - _EPSILON:
        keep.append((cursor, duration))

    # Empty result means drop covered full duration (AC-5)
    if not keep:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="No ranges remain to keep.",
            hint="Reduce drop coverage so some region remains.",
        )

    return keep, warnings


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge overlapping or EPS-adjacent intervals; return sorted non-overlapping list.

    Copied from clipwright-silence/plan.py (ADR-7: no cross-satellite import).
    """
    if not intervals:
        return []

    sorted_ivs = sorted(intervals, key=lambda iv: iv[0])
    merged: list[tuple[float, float]] = [sorted_ivs[0]]

    for start, end in sorted_ivs[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + _EPSILON:
            # Overlapping or adjacent -> merge
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged
