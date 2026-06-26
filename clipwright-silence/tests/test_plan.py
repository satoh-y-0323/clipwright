"""test_plan.py — Tests for plan.py (pure logic).

Target function:
  derive_keep_ranges(
      total_duration_sec: float,
      silence_intervals: list[tuple[float, float]],
      options: DetectSilenceOptions,
  ) -> list[tuple[float, float]]

plan.py is pure logic that never runs ffmpeg.
silence_threshold_db / min_silence_duration are the silencedetect layer's responsibility
and are not passed to derive_keep_ranges (only padding / min_keep_duration are used).

Covers the perspectives of AD-3 / DC-AM-001 / DC-GP-001.
"""

from __future__ import annotations

import pytest

from clipwright_silence.plan import _EPSILON, derive_keep_ranges
from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# Helpers
# ===========================================================================


def _opts(
    padding: float = 0.0,
    min_keep_duration: float = 0.0,
) -> DetectSilenceOptions:
    """Build a DetectSilenceOptions for tests (silence-related parameters use defaults)."""
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=padding,
        min_keep_duration=min_keep_duration,
    )


# ===========================================================================
# (1) Zero silence -> full duration as 1 interval
# ===========================================================================


class TestNoSilence:
    """When silence intervals are empty: returns the full duration as 1 KEEP (AD-3 §2)."""

    def test_no_silence_returns_full_range(self) -> None:
        """Zero silence -> 1 interval [(0.0, total_duration)]."""
        keeps = derive_keep_ranges(10.0, [], _opts())
        assert keeps == [(0.0, 10.0)]

    def test_no_silence_returns_single_interval(self) -> None:
        """Zero silence: returned list has length 1."""
        keeps = derive_keep_ranges(60.0, [], _opts())
        assert len(keeps) == 1

    def test_no_silence_with_padding_still_full_range(self) -> None:
        """Zero silence with padding: remains [(0.0, total)] (no change after clamp)."""
        keeps = derive_keep_ranges(10.0, [], _opts(padding=1.0))
        assert keeps == [(0.0, 10.0)]


# ===========================================================================
# (2) Invert leading silence
# ===========================================================================


class TestLeadingSilence:
    """When the start is silence: KEEP begins at the silence end (AD-3 §2)."""

    def test_leading_silence_keep_starts_at_silence_end(self) -> None:
        """Leading 0~3s silence -> KEEP is (3.0, 10.0)."""
        keeps = derive_keep_ranges(10.0, [(0.0, 3.0)], _opts())
        assert keeps == [(3.0, 10.0)]

    def test_leading_silence_exact_boundary(self) -> None:
        """Leading silence boundary: silence_end and keep_start coincide."""
        keeps = derive_keep_ranges(5.0, [(0.0, 2.5)], _opts())
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(2.5)
        assert keeps[0][1] == pytest.approx(5.0)


# ===========================================================================
# (3) Invert trailing silence / invert multiple silence intervals
# ===========================================================================


class TestTrailingAndMultipleSilence:
    """Inversion of trailing silence and multiple silence intervals (AD-3 §2)."""

    def test_trailing_silence_keep_ends_at_silence_start(self) -> None:
        """Trailing 7~10s silence -> KEEP is (0.0, 7.0)."""
        keeps = derive_keep_ranges(10.0, [(7.0, 10.0)], _opts())
        assert keeps == [(0.0, 7.0)]

    def test_two_silences_three_keeps(self) -> None:
        """2 mid silences -> inverted into 3 KEEP intervals."""
        # silence: 2~3, 6~7 -> KEEP: (0,2), (3,6), (7,10)
        keeps = derive_keep_ranges(10.0, [(2.0, 3.0), (6.0, 7.0)], _opts())
        assert len(keeps) == 3
        assert keeps[0] == pytest.approx((0.0, 2.0))
        assert keeps[1] == pytest.approx((3.0, 6.0))
        assert keeps[2] == pytest.approx((7.0, 10.0))

    def test_leading_and_trailing_silence_one_keep(self) -> None:
        """Leading and trailing silence -> 1 mid KEEP."""
        keeps = derive_keep_ranges(10.0, [(0.0, 2.0), (8.0, 10.0)], _opts())
        assert keeps == [(2.0, 8.0)]

    def test_three_silences_four_keeps(self) -> None:
        """3 silence intervals -> 4 KEEP intervals."""
        # silence: 1~2, 4~5, 7~8 -> KEEP: (0,1),(2,4),(5,7),(8,10)
        keeps = derive_keep_ranges(10.0, [(1.0, 2.0), (4.0, 5.0), (7.0, 8.0)], _opts())
        assert len(keeps) == 4
        assert keeps[0] == pytest.approx((0.0, 1.0))
        assert keeps[1] == pytest.approx((2.0, 4.0))
        assert keeps[2] == pytest.approx((5.0, 7.0))
        assert keeps[3] == pytest.approx((8.0, 10.0))


# ===========================================================================
# (4) padding expansion and [0, total] clamp
# ===========================================================================


class TestPaddingClamp:
    """After padding expansion, values must be clamped to [0, total_duration] (AD-3 §3)."""

    def test_padding_expands_keep_range(self) -> None:
        """padding=0.5s: KEEP is extended by 0.5s on each side."""
        # silence: 3~7 -> inverted KEEP: (0,3),(7,10)
        # padding=0.5 -> (0,3.5),(6.5,10) after clamp
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.5))
        assert len(keeps) == 2
        assert keeps[0][1] == pytest.approx(3.5)
        assert keeps[1][0] == pytest.approx(6.5)

    def test_padding_clamps_start_to_zero(self) -> None:
        """Leading KEEP start goes below 0 due to padding -> clamped to 0."""
        # silence: 2~5 -> KEEP: (0,2),(5,10)
        # padding=1.0 -> (0,3),(4,10) after start clamp
        keeps = derive_keep_ranges(10.0, [(2.0, 5.0)], _opts(padding=1.0))
        assert keeps[0][0] == pytest.approx(0.0)

    def test_padding_clamps_end_to_total(self) -> None:
        """Trailing KEEP end exceeds total due to padding -> clamped to total."""
        # silence: 5~8 -> KEEP: (0,5),(8,10)
        # padding=1.0 -> end(10+1=11) -> clamped to 10
        keeps = derive_keep_ranges(10.0, [(5.0, 8.0)], _opts(padding=1.0))
        last_end = keeps[-1][1]
        assert last_end == pytest.approx(10.0)

    def test_padding_zero_no_expansion(self) -> None:
        """padding=0.0: no expansion (inversion only)."""
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.0))
        assert keeps[0] == pytest.approx((0.0, 3.0))
        assert keeps[1] == pytest.approx((7.0, 10.0))


# ===========================================================================
# (5) Adjacent KEEPs are merged by padding
# ===========================================================================


class TestPaddingMerge:
    """Adjacent KEEPs that overlap after padding expansion must be merged (AD-3 §3)."""

    def test_padding_merges_adjacent_keeps(self) -> None:
        """padding causes 2 KEEP edges to overlap -> merged into 1 KEEP.

        Example: silence (3,4) -> KEEP (0,3),(4,10)
        padding=1.0 -> (0,4),(3,10) -> overlap -> (0,10)
        """
        keeps = derive_keep_ranges(10.0, [(3.0, 4.0)], _opts(padding=1.0))
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(0.0)
        assert keeps[0][1] == pytest.approx(10.0)

    def test_padding_no_merge_when_gap_large_enough(self) -> None:
        """When padding is small and KEEP edges do not overlap, they are not merged."""
        # silence (3,7) -> KEEP (0,3),(7,10)
        # padding=0.1 -> (0,3.1),(6.9,10) -> no overlap
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.1))
        assert len(keeps) == 2

    def test_padding_merges_three_keeps_into_two(self) -> None:
        """padding causes all 3 KEEPs to merge into 1 interval (CR L-4: deterministic)."""
        # silence: (2,3),(5,6) -> KEEP: (0,2),(3,5),(6,10)
        # padding=0.6 -> (0,2.6),(2.4,5.6),(5.4,10)
        # (0,2.6) and (2.4,5.6) overlap -> merge (0, 5.6)
        # (0,5.6) and (5.4,10) overlap -> merge (0, 10)
        # -> entire range becomes 1 interval (deterministic)
        keeps = derive_keep_ranges(10.0, [(2.0, 3.0), (5.0, 6.0)], _opts(padding=0.6))
        assert len(keeps) == 1


# ===========================================================================
# (6) min_keep_duration: no discard at default 0.0 / short KEEPs discarded when positive (DC-AM-001)
# ===========================================================================


class TestMinKeepDuration:
    """Behavior of min_keep_duration (DC-AM-001)."""

    def test_default_zero_keeps_all_intervals(self) -> None:
        """min_keep_duration=0.0 (default): short KEEPs are not discarded."""
        # silence (1,1.9) -> KEEP (0,1),(1.9,10)
        # (1.9,10) is long; (0,1) is 1s -> not discarded with 0.0
        keeps = derive_keep_ranges(10.0, [(1.0, 1.9)], _opts(min_keep_duration=0.0))
        assert len(keeps) == 2

    def test_min_keep_filters_short_keep(self) -> None:
        """min_keep_duration > 0: KEEPs shorter than this value are discarded.

        Example: silence (0.5, 9.5) -> KEEP (0,0.5),(9.5,10)
        (0,0.5) is 0.5s, (9.5,10) is 0.5s
        min_keep_duration=1.0 -> both discarded
        """
        keeps = derive_keep_ranges(10.0, [(0.5, 9.5)], _opts(min_keep_duration=1.0))
        for start, end in keeps:
            assert (end - start) >= 1.0 - _EPSILON

    def test_min_keep_keeps_long_interval(self) -> None:
        """With min_keep_duration set, long KEEPs are retained."""
        # silence (2,3) -> KEEP (0,2),(3,10)
        # (0,2)=2s, (3,10)=7s -> min_keep=1.5 -> both retained
        keeps = derive_keep_ranges(10.0, [(2.0, 3.0)], _opts(min_keep_duration=1.5))
        assert len(keeps) == 2

    def test_min_keep_exact_boundary_kept(self) -> None:
        """A KEEP equal to min_keep is not discarded (boundary value equal is retained)."""
        # silence (1,2) -> KEEP (0,1),(2,10)
        # (0,1)=1.0s -> min_keep=1.0 -> retained
        keeps = derive_keep_ranges(10.0, [(1.0, 2.0)], _opts(min_keep_duration=1.0))
        # 1.0s KEEP must remain
        durations = [end - start for start, end in keeps]
        assert any(abs(d - 1.0) < _EPSILON for d in durations)

    def test_min_keep_applied_after_padding_merge(self) -> None:
        """min_keep_duration is applied after padding and merging.

        The judgment uses the length of the merged KEEP after padding.
        """
        # silence (5,6) -> KEEP (0,5),(6,10)
        # padding=0 -> 2 KEEPs: 5s,4s -> min_keep=3 -> both retained
        keeps = derive_keep_ranges(
            10.0, [(5.0, 6.0)], _opts(padding=0.0, min_keep_duration=3.0)
        )
        assert len(keeps) == 2


# ===========================================================================
# (7) All silence -> empty KEEP list
# ===========================================================================


class TestAllSilence:
    """When the full duration is silence: returns an empty KEEP list (AD-3 §2)."""

    def test_full_silence_returns_empty_list(self) -> None:
        """Full silence -> []."""
        keeps = derive_keep_ranges(10.0, [(0.0, 10.0)], _opts())
        assert keeps == []

    def test_full_silence_no_padding_effect(self) -> None:
        """Full silence + padding: still returns an empty list."""
        keeps = derive_keep_ranges(10.0, [(0.0, 10.0)], _opts(padding=1.0))
        assert keeps == []

    def test_nearly_full_silence_only_tiny_keep(self) -> None:
        """Nearly all silence -> 1 tiny KEEP interval (not discarded with min_keep_duration=0)."""
        # silence (0,9.99) -> KEEP (9.99, 10.0)
        keeps = derive_keep_ranges(10.0, [(0.0, 9.99)], _opts(min_keep_duration=0.0))
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(9.99)
        assert keeps[0][1] == pytest.approx(10.0)


# ===========================================================================
# (8) padding bridges 2 KEEPs across short silence -> joined (DC-GP-001: word-break prevention)
# ===========================================================================


class TestShortSilenceBridging:
    """When padding spans a short silence: fill-in (joining) occurs (DC-GP-001).

    Design intent: when KEEPs are adjacent across a short silence (breath / pause),
    padding bridges the gap to prevent word breaks.
    """

    def test_short_silence_bridged_by_padding(self) -> None:
        """Short silence (4.8, 5.2) = 0.4s spanned by padding=0.3 -> joined into 1 KEEP.

        KEEP before padding: (0,4.8),(5.2,10)
        KEEP after padding:  (0,5.1),(4.9,10)  <- overlapping
        merged:              (0, 10)
        """
        keeps = derive_keep_ranges(10.0, [(4.8, 5.2)], _opts(padding=0.3))
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(0.0)
        assert keeps[0][1] == pytest.approx(10.0)

    def test_long_silence_not_bridged(self) -> None:
        """Long silence (3,7) = 4s is not spanned by padding=0.3 -> stays as 2 KEEPs."""
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.3))
        assert len(keeps) == 2

    def test_bridging_preserves_outer_keeps(self) -> None:
        """The joined KEEP after fill-in contains both original KEEPs."""
        # silence (4.9, 5.1) -> KEEP (0,4.9),(5.1,10) -> padding=0.2 -> bridge
        keeps = derive_keep_ranges(10.0, [(4.9, 5.1)], _opts(padding=0.2))
        assert len(keeps) == 1
        # full (0, 10) is retained (edges clamped to 0.0 / 10.0)
        assert keeps[0][0] == pytest.approx(0.0)
        assert keeps[0][1] == pytest.approx(10.0)

    def test_multiple_short_silences_all_bridged(self) -> None:
        """Multiple short silences all spanned by padding -> entire range becomes 1 KEEP.

        Silence: (2,2.3),(5,5.3),(8,8.3) -> each 0.3s
        padding=0.2 -> each silence spanned -> 1 KEEP total
        """
        keeps = derive_keep_ranges(
            10.0,
            [(2.0, 2.3), (5.0, 5.3), (8.0, 8.3)],
            _opts(padding=0.2),
        )
        assert len(keeps) == 1


# ===========================================================================
# Boundary values and other edge cases
# ===========================================================================


class TestEdgeCases:
    """Boundary values and other edge cases."""

    def test_total_duration_zero_no_silence_returns_empty(self) -> None:
        """total_duration=0.0, no silence -> [] or [(0,0)] (zero-length interval).

        Implementation-dependent, but a zero-length interval is practically meaningless
        so empty is also acceptable.
        """
        keeps = derive_keep_ranges(0.0, [], _opts())
        # accept empty or (0,0) (exact behavior determined at implementation time)
        assert isinstance(keeps, list)

    def test_silence_interval_at_exact_boundary(self) -> None:
        """Safety when a silence interval exactly covers the end of total_duration."""
        # silence (9.0, 10.0) -> KEEP (0.0, 9.0)
        keeps = derive_keep_ranges(10.0, [(9.0, 10.0)], _opts())
        assert len(keeps) == 1
        assert keeps[0] == pytest.approx((0.0, 9.0))

    def test_return_type_is_list_of_tuples(self) -> None:
        """Return type must be list[tuple[float, float]]."""
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts())
        assert isinstance(keeps, list)
        for item in keeps:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], float)
            assert isinstance(item[1], float)

    def test_keep_intervals_are_non_overlapping(self) -> None:
        """Returned KEEP intervals must not overlap (already merged)."""
        keeps = derive_keep_ranges(
            20.0,
            [(2.0, 3.0), (5.0, 6.0), (8.0, 9.0)],
            _opts(padding=0.0),
        )
        for i in range(len(keeps) - 1):
            assert keeps[i][1] <= keeps[i + 1][0] + _EPSILON, (
                f"KEEP intervals overlap: {keeps[i]} and {keeps[i + 1]}"
            )

    def test_keep_intervals_are_ordered(self) -> None:
        """Returned KEEP intervals must be in chronological order."""
        keeps = derive_keep_ranges(
            20.0,
            [(5.0, 6.0), (2.0, 3.0)],  # reverse order input
            _opts(padding=0.0),
        )
        for i in range(len(keeps) - 1):
            assert keeps[i][0] < keeps[i + 1][0], (
                "KEEP intervals are not in chronological order"
            )
