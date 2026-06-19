"""test_plan.py — Tests for derive_keep_ranges (pure logic, no mocks).

Covers boundary condition matrix B1–B15 (architecture-report §3.5) with the
following confirmed overrides from the task specification:
  - B13: both keep=[] and drop=[] → full-duration passthrough [(0, duration)].
         (ADR-4's INVALID_INPUT is NOT adopted; FR-2 literal honored.)
  - B14: both keep non-empty AND drop non-empty → INVALID_INPUT (AC-4).
  - Empty computed result → INVALID_INPUT (AC-5).
  - Drop-mode padding: inward (shrinks drop, keeps region grows). (ADR-5)
"""

from __future__ import annotations

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_trim.plan import _EPSILON, derive_keep_ranges
from clipwright_trim.schemas import TrimOptions, TrimRange

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

D = 100.0  # default test duration (seconds)


def _keep(*ranges: tuple[float, float]) -> TrimOptions:
    """Build a TrimOptions with only keep ranges."""
    return TrimOptions(keep=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges])


def _drop(*ranges: tuple[float, float], padding: float = 0.0) -> TrimOptions:
    """Build a TrimOptions with only drop ranges."""
    return TrimOptions(
        drop=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
        padding_sec=padding,
    )


def _keep_pad(*ranges: tuple[float, float], padding: float) -> TrimOptions:
    """Build a TrimOptions with keep ranges and padding."""
    return TrimOptions(
        keep=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
        padding_sec=padding,
    )


# --------------------------------------------------------------------------- #
# B1 — keep [(0, D)] → single full-duration range (AC-3)
# --------------------------------------------------------------------------- #


def test_b1_keep_full_duration() -> None:
    """keep [(0, D)] produces a single (0, D) range with no warnings."""
    ranges, warnings, mode = derive_keep_ranges(D, _keep((0.0, D)))
    assert ranges == [(0.0, D)]
    assert warnings == []
    assert mode == "keep"


# --------------------------------------------------------------------------- #
# B2 — keep [(0, D+5)] → clamped to (0, D) + warning (AC-3)
# --------------------------------------------------------------------------- #


def test_b2_keep_clamp_beyond_duration() -> None:
    """keep range extending beyond D is clamped to D with a clamp warning."""
    ranges, warnings, mode = derive_keep_ranges(D, _keep((0.0, D + 5.0)))
    assert ranges == [(0.0, D)]
    assert len(warnings) == 1
    assert mode == "keep"
    # SR-M-2: warning is a fixed message without numeric values
    assert warnings[0] == "A keep range was clamped to the media boundary."


# --------------------------------------------------------------------------- #
# B3 — start_sec >= end_sec → INVALID_INPUT (AC-3)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "start, end",
    [
        (10.0, 10.0),  # equal
        (15.0, 10.0),  # reversed
    ],
)
def test_b3_keep_start_ge_end_raises(start: float, end: float) -> None:
    """A keep range with start_sec >= end_sec raises INVALID_INPUT."""
    with pytest.raises(ClipwrightError) as exc_info:
        derive_keep_ranges(D, _keep((start, end)))
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------- #
# B4 — keep [(D+1, D+5)] → degenerate after clamp → INVALID_INPUT
# --------------------------------------------------------------------------- #


def test_b4_keep_entirely_outside_duration_raises() -> None:
    """keep range fully outside [0, D] becomes degenerate after clamp → INVALID_INPUT."""
    with pytest.raises(ClipwrightError) as exc_info:
        derive_keep_ranges(D, _keep((D + 1.0, D + 5.0)))
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------- #
# B5 — keep two non-overlapping ranges → order preserved, not merged
# --------------------------------------------------------------------------- #


def test_b5_keep_two_ranges_order_preserved() -> None:
    """Two keep ranges are returned in enumeration order and are not merged."""
    ranges, warnings, mode = derive_keep_ranges(D, _keep((10.0, 30.0), (50.0, 70.0)))
    assert len(ranges) == 2
    assert ranges[0] == (10.0, 30.0)
    assert ranges[1] == (50.0, 70.0)
    assert warnings == []
    assert mode == "keep"


def test_b5_keep_reversed_order_preserved() -> None:
    """keep mode preserves enumeration order even when ranges are given in reverse order."""
    ranges, warnings, mode = derive_keep_ranges(D, _keep((50.0, 70.0), (10.0, 30.0)))
    assert ranges[0] == (50.0, 70.0)
    assert ranges[1] == (10.0, 30.0)
    assert warnings == []
    assert mode == "keep"


# --------------------------------------------------------------------------- #
# B6 — keep overlapping ranges → pass-through (no merge, ADR-3)
# --------------------------------------------------------------------------- #


def test_b6_keep_overlapping_not_merged() -> None:
    """Overlapping keep ranges pass through as separate tuples (ADR-3)."""
    ranges, warnings, mode = derive_keep_ranges(D, _keep((0.0, 10.0), (5.0, 15.0)))
    assert len(ranges) == 2
    assert ranges[0] == (0.0, 10.0)
    assert ranges[1] == (5.0, 15.0)
    assert warnings == []
    assert mode == "keep"


# --------------------------------------------------------------------------- #
# B7 — drop [(s, e)], 0 < s < e < D → complement = (0, s) and (e, D)
# --------------------------------------------------------------------------- #


def test_b7_drop_middle_produces_two_ranges() -> None:
    """drop [(30, 60)] produces (0, 30) and (60, D) (AC-2)."""
    ranges, warnings, mode = derive_keep_ranges(D, _drop((30.0, 60.0)))
    assert ranges == [(0.0, 30.0), (60.0, D)]
    assert warnings == []
    assert mode == "drop"


# --------------------------------------------------------------------------- #
# B8 — drop [(0, D)] → empty keep → INVALID_INPUT (AC-5)
# --------------------------------------------------------------------------- #


def test_b8_drop_full_duration_raises() -> None:
    """drop covering full duration leaves no keep ranges → INVALID_INPUT (AC-5)."""
    with pytest.raises(ClipwrightError) as exc_info:
        derive_keep_ranges(D, _drop((0.0, D)))
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------- #
# B9 — drop [(0, s)] → single Clip (s, D)
# --------------------------------------------------------------------------- #


def test_b9_drop_from_start() -> None:
    """drop [(0, 40)] produces single keep range (40, D)."""
    ranges, warnings, mode = derive_keep_ranges(D, _drop((0.0, 40.0)))
    assert ranges == [(40.0, D)]
    assert warnings == []
    assert mode == "drop"


# --------------------------------------------------------------------------- #
# B10 — drop [(s, D)] → single Clip (0, s)
# --------------------------------------------------------------------------- #


def test_b10_drop_to_end() -> None:
    """drop [(60, D)] produces single keep range (0, 60)."""
    ranges, warnings, mode = derive_keep_ranges(D, _drop((60.0, D)))
    assert ranges == [(0.0, 60.0)]
    assert warnings == []
    assert mode == "drop"


# --------------------------------------------------------------------------- #
# B11 — drop overlapping → merged, then complement
# --------------------------------------------------------------------------- #


def test_b11_drop_overlapping_merged_then_complement() -> None:
    """Overlapping drop [(0,10),(5,15)] merged to (0,15); complement is (15, D)."""
    ranges, warnings, mode = derive_keep_ranges(D, _drop((0.0, 10.0), (5.0, 15.0)))
    assert ranges == [(15.0, D)]
    assert warnings == []
    assert mode == "drop"


# --------------------------------------------------------------------------- #
# B12 — drop adjacent → EPS-merged, complement applied
# --------------------------------------------------------------------------- #


def test_b12_drop_adjacent_merged() -> None:
    """Adjacent drop [(0,10),(10,20)] EPS-merged to (0,20); complement is (20, D)."""
    ranges, warnings, mode = derive_keep_ranges(D, _drop((0.0, 10.0), (10.0, 20.0)))
    assert ranges == [(20.0, D)]
    assert warnings == []
    assert mode == "drop"


# --------------------------------------------------------------------------- #
# B13 — both keep=[] and drop=[] → passthrough [(0, D)]  (confirmed spec override)
# --------------------------------------------------------------------------- #


def test_b13_both_empty_returns_full_passthrough() -> None:
    """Both keep and drop empty → full-duration passthrough [(0, D)]. No error, no warnings."""
    options = TrimOptions()
    ranges, warnings, mode = derive_keep_ranges(D, options)
    assert ranges == [(0.0, D)]
    assert warnings == []
    # Passthrough is reported as "keep" mode (ADR-1)
    assert mode == "keep"


# --------------------------------------------------------------------------- #
# B14 — both keep non-empty AND drop non-empty → INVALID_INPUT (AC-4)
# --------------------------------------------------------------------------- #


def test_b14_both_keep_and_drop_raises() -> None:
    """Providing both keep and drop raises INVALID_INPUT (AC-4)."""
    options = TrimOptions(
        keep=[TrimRange(start_sec=0.0, end_sec=10.0)],
        drop=[TrimRange(start_sec=20.0, end_sec=30.0)],
    )
    with pytest.raises(ClipwrightError) as exc_info:
        derive_keep_ranges(D, options)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------- #
# B15 — padding causes two keep ranges to overlap → NOT merged (ADR-3)
# --------------------------------------------------------------------------- #


def test_b15_keep_padding_overlap_not_merged() -> None:
    """Padding expanding keep ranges into overlap does not trigger merging (ADR-3)."""
    # (10, 30) + 5s padding → (5, 35); (32, 50) + 5s padding → (27, 55)
    # After padding they overlap: (5,35) and (27,55) — keep mode must NOT merge.
    ranges, warnings, mode = derive_keep_ranges(
        D, _keep_pad((10.0, 30.0), (32.0, 50.0), padding=5.0)
    )
    assert len(ranges) == 2
    # Both padded and clamped
    assert abs(ranges[0][0] - 5.0) < _EPSILON
    assert abs(ranges[0][1] - 35.0) < _EPSILON
    assert abs(ranges[1][0] - 27.0) < _EPSILON
    assert abs(ranges[1][1] - 55.0) < _EPSILON
    assert mode == "keep"


# --------------------------------------------------------------------------- #
# Additional edge cases not in B-matrix but required by spec
# --------------------------------------------------------------------------- #


class TestKeepModeAdditional:
    """Additional keep-mode edge cases."""

    def test_keep_single_point_at_boundary_start(self) -> None:
        """keep (0, 0) is start_sec >= end_sec → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            derive_keep_ranges(D, _keep((0.0, 0.0)))
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_keep_clamp_negative_start_with_padding(self) -> None:
        """Padding expanding start below 0 is clamped to 0; warning emitted."""
        # (5, 20) with padding=10 → raw (-5, 30) → clamped (0, 30) + warning
        ranges, warnings, mode = derive_keep_ranges(
            D, _keep_pad((5.0, 20.0), padding=10.0)
        )
        assert ranges == [(0.0, 30.0)]
        assert len(warnings) == 1
        assert mode == "keep"
        # SR-M-2: warning is a fixed message without numeric values
        assert warnings[0] == "A keep range was clamped to the media boundary."

    def test_keep_multiple_ranges_warnings_per_clamped(self) -> None:
        """Each clamped range emits its own warning."""
        # Two ranges both clamped
        ranges, warnings, mode = derive_keep_ranges(
            D, _keep_pad((0.0, 10.0), (90.0, D), padding=15.0)
        )
        # (0, 10) + 15 → raw (-15, 25) → clamped (0, 25) warns
        # (90, 100) + 15 → raw (75, 115) → clamped (75, 100) warns
        assert len(ranges) == 2
        assert len(warnings) == 2
        assert mode == "keep"


class TestDropModeAdditional:
    """Additional drop-mode edge cases."""

    def test_drop_padding_inward_shrinks_drop(self) -> None:
        """Drop-mode padding shrinks the drop region (content-protective direction, ADR-5).

        drop [(20, 80)], padding=5 → effective drop (25, 75) → keep (0,25) and (75,D).
        """
        ranges, warnings, mode = derive_keep_ranges(D, _drop((20.0, 80.0), padding=5.0))
        assert len(ranges) == 2
        assert abs(ranges[0][0] - 0.0) < _EPSILON
        assert abs(ranges[0][1] - 25.0) < _EPSILON
        assert abs(ranges[1][0] - 75.0) < _EPSILON
        assert abs(ranges[1][1] - D) < _EPSILON
        assert mode == "drop"

    def test_drop_degenerate_after_padding_discarded(self) -> None:
        """A drop range that becomes degenerate after inward padding is discarded.

        drop [(40, 50)], padding=6 → effective drop (46, 44) → degenerate → discarded.
        Result: full passthrough [(0, D)] because all drops disappear.
        """
        ranges, warnings, mode = derive_keep_ranges(D, _drop((40.0, 50.0), padding=6.0))
        assert ranges == [(0.0, D)]
        assert mode == "drop"

    def test_drop_single_range_entire_complement(self) -> None:
        """drop [(50, 50)] is start>=end → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            derive_keep_ranges(D, _drop((50.0, 50.0)))
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_drop_unordered_input_still_correct(self) -> None:
        """drop ranges provided out of order are sorted before complementing."""
        # drop [(60, 80), (10, 30)] unsorted → sorted → (10,30),(60,80) → (0,10),(30,60),(80,D)
        ranges, warnings, mode = derive_keep_ranges(
            D, _drop((60.0, 80.0), (10.0, 30.0))
        )
        assert ranges == [(0.0, 10.0), (30.0, 60.0), (80.0, D)]
        assert warnings == []
        assert mode == "drop"

    def test_drop_all_coverage_with_two_ranges_raises(self) -> None:
        """Two drop ranges covering all of [0, D] → INVALID_INPUT (AC-5)."""
        with pytest.raises(ClipwrightError) as exc_info:
            derive_keep_ranges(D, _drop((0.0, 50.0), (50.0, D)))
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
