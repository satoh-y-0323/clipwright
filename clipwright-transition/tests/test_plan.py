"""test_plan.py — Unit tests for clipwright_transition.plan.resolve_transitions.

These tests exercise the pure logic layer (no I/O, no OTIO, no subprocess).
All tests are expected to FAIL at import time because plan.py and schemas.py
are not yet implemented (Red phase of TDD).

Test cases:
  (1) uniform mode: expands to all boundaries [0, n_clips-2] in ascending order,
      with consistent type and duration_sec across all boundaries.
  (2) per_boundary mode: passes through in-range indices, sorted ascending
      regardless of input order.
  (3) per_boundary out-of-range index (> n_clips-2) -> ClipwrightError(INVALID_INPUT)
      with hint that includes the allowed range [0, n_clips-2].
  (4) per_boundary duplicate index -> ClipwrightError(INVALID_INPUT).
  (5) n_clips < 2 -> ClipwrightError(INVALID_INPUT) with hint about
      clipwright-sequence / clipwright-trim.
"""

from __future__ import annotations

import pytest

# --- Imports under test (both modules are not yet implemented) ---
# These imports will raise ImportError in the Red phase, making all tests fail.
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright_transition.plan import ResolvedTransition, resolve_transitions  # type: ignore[import]  # noqa: E501
from clipwright_transition.schemas import (  # type: ignore[import]
    AddTransitionOptions,
    BoundaryTransition,
    TransitionSpec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uniform_opts(type_: str = "dissolve", duration_sec: float = 0.5) -> AddTransitionOptions:
    """Build AddTransitionOptions in uniform mode."""
    return AddTransitionOptions(uniform=TransitionSpec(type=type_, duration_sec=duration_sec))


def _per_opts(boundaries: list[BoundaryTransition]) -> AddTransitionOptions:
    """Build AddTransitionOptions in per_boundary mode."""
    return AddTransitionOptions(per_boundary=boundaries)


def _boundary(after_clip_index: int, type_: str = "fade", duration_sec: float = 0.3) -> BoundaryTransition:
    """Shorthand for BoundaryTransition."""
    return BoundaryTransition(
        after_clip_index=after_clip_index,
        type=type_,
        duration_sec=duration_sec,
    )


# ---------------------------------------------------------------------------
# (1) uniform mode
# ---------------------------------------------------------------------------


class TestUniformMode:
    """resolve_transitions with uniform options."""

    def test_two_clips_uniform_produces_single_boundary(self) -> None:
        """n_clips=2, uniform -> exactly one ResolvedTransition at index 0."""
        opts = _uniform_opts(type_="dissolve", duration_sec=0.5)
        result = resolve_transitions(n_clips=2, options=opts)

        assert len(result) == 1
        assert result[0].after_clip_index == 0
        assert result[0].type == "dissolve"
        assert result[0].duration_sec == 0.5

    def test_three_clips_uniform_produces_two_boundaries(self) -> None:
        """n_clips=3, uniform -> two ResolvedTransitions at indices 0 and 1."""
        opts = _uniform_opts(type_="fade", duration_sec=1.0)
        result = resolve_transitions(n_clips=3, options=opts)

        assert len(result) == 2
        assert result[0].after_clip_index == 0
        assert result[1].after_clip_index == 1

    def test_uniform_all_indices_ascending(self) -> None:
        """n_clips=5, uniform -> indices 0,1,2,3 in ascending order."""
        opts = _uniform_opts()
        result = resolve_transitions(n_clips=5, options=opts)

        indices = [r.after_clip_index for r in result]
        assert indices == list(range(4))  # [0, 1, 2, 3]

    def test_uniform_type_and_duration_consistent_across_all_boundaries(self) -> None:
        """All resolved transitions share the same type and duration_sec as the spec."""
        opts = _uniform_opts(type_="fadeblack", duration_sec=2.0)
        result = resolve_transitions(n_clips=4, options=opts)

        for rt in result:
            assert rt.type == "fadeblack"
            assert rt.duration_sec == 2.0

    def test_uniform_covers_exactly_n_minus_2_to_zero(self) -> None:
        """n_clips=6, uniform -> indices exactly [0..4] (n_clips-2=4)."""
        opts = _uniform_opts()
        result = resolve_transitions(n_clips=6, options=opts)

        indices = [r.after_clip_index for r in result]
        assert indices == [0, 1, 2, 3, 4]

    def test_uniform_all_types_accepted(self) -> None:
        """All four allowed type values produce valid ResolvedTransitions."""
        for t in ("fade", "dissolve", "fadeblack", "fadewhite"):
            opts = _uniform_opts(type_=t, duration_sec=0.5)
            result = resolve_transitions(n_clips=2, options=opts)
            assert result[0].type == t

    def test_resolved_transition_is_frozen(self) -> None:
        """ResolvedTransition is a frozen dataclass — mutation must raise."""
        opts = _uniform_opts()
        result = resolve_transitions(n_clips=2, options=opts)
        rt = result[0]

        with pytest.raises((AttributeError, TypeError)):
            rt.after_clip_index = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# (2) per_boundary mode
# ---------------------------------------------------------------------------


class TestPerBoundaryMode:
    """resolve_transitions with per_boundary options."""

    def test_per_boundary_single_in_range(self) -> None:
        """Single in-range per_boundary index is returned as-is."""
        opts = _per_opts([_boundary(0)])
        result = resolve_transitions(n_clips=3, options=opts)

        assert len(result) == 1
        assert result[0].after_clip_index == 0
        assert result[0].type == "fade"
        assert result[0].duration_sec == 0.3

    def test_per_boundary_multiple_returned_ascending_regardless_of_input_order(self) -> None:
        """per_boundary with out-of-order input is sorted ascending in the result."""
        # Input order: 2, 0, 1 — should be returned as [0, 1, 2].
        opts = _per_opts([_boundary(2), _boundary(0), _boundary(1)])
        result = resolve_transitions(n_clips=4, options=opts)

        indices = [r.after_clip_index for r in result]
        assert indices == [0, 1, 2]

    def test_per_boundary_max_valid_index_n_clips_minus_2(self) -> None:
        """after_clip_index == n_clips-2 is the maximum valid index and must succeed."""
        # n_clips=4, max valid index = 2
        opts = _per_opts([_boundary(2)])
        result = resolve_transitions(n_clips=4, options=opts)

        assert len(result) == 1
        assert result[0].after_clip_index == 2

    def test_per_boundary_preserves_type_and_duration_per_entry(self) -> None:
        """Each ResolvedTransition preserves the type and duration from its BoundaryTransition."""
        opts = _per_opts([
            BoundaryTransition(after_clip_index=0, type="dissolve", duration_sec=0.5),
            BoundaryTransition(after_clip_index=1, type="fadewhite", duration_sec=1.2),
        ])
        result = resolve_transitions(n_clips=3, options=opts)

        # Result is sorted by index
        assert result[0].after_clip_index == 0
        assert result[0].type == "dissolve"
        assert result[0].duration_sec == 0.5

        assert result[1].after_clip_index == 1
        assert result[1].type == "fadewhite"
        assert result[1].duration_sec == 1.2

    def test_per_boundary_index_0_is_valid(self) -> None:
        """after_clip_index=0 (minimum valid) must succeed for n_clips>=2."""
        opts = _per_opts([_boundary(0)])
        result = resolve_transitions(n_clips=2, options=opts)

        assert result[0].after_clip_index == 0


# ---------------------------------------------------------------------------
# (3) per_boundary out-of-range index
# ---------------------------------------------------------------------------


class TestPerBoundaryOutOfRange:
    """after_clip_index > n_clips-2 must raise INVALID_INPUT with range hint."""

    def test_index_exceeds_n_clips_minus_2(self) -> None:
        """after_clip_index = n_clips-1 (one past max) -> INVALID_INPUT."""
        # n_clips=3, max valid index = 1, so index=2 is out of range
        opts = _per_opts([_boundary(2)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=3, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_out_of_range_hint_contains_allowed_range(self) -> None:
        """The hint must mention the allowed range [0, n_clips-2]."""
        # n_clips=4, max valid = 2, so index=3 is out of range
        opts = _per_opts([_boundary(3)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=4, options=opts)

        hint = exc_info.value.hint
        # Hint must convey allowed boundary [0, n_clips-2] = [0, 2]
        assert "0" in hint
        assert "2" in hint  # n_clips-2 = 4-2 = 2

    def test_large_out_of_range_index(self) -> None:
        """Clearly out-of-range index -> INVALID_INPUT."""
        opts = _per_opts([_boundary(100)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=3, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_out_of_range_among_valid_indices(self) -> None:
        """Mix of valid and out-of-range indices: error on the out-of-range one."""
        # n_clips=3: valid indices are [0, 1]. index=2 is out of range.
        opts = _per_opts([_boundary(0), _boundary(2)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=3, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# (4) per_boundary duplicate index
# ---------------------------------------------------------------------------


class TestPerBoundaryDuplicate:
    """Duplicate after_clip_index values must raise INVALID_INPUT."""

    def test_duplicate_index_raises(self) -> None:
        """Two entries with the same after_clip_index -> INVALID_INPUT."""
        opts = _per_opts([_boundary(0), _boundary(0)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=3, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_duplicate_index_different_types(self) -> None:
        """Same index with different type/duration is still a duplicate -> INVALID_INPUT."""
        opts = _per_opts([
            BoundaryTransition(after_clip_index=1, type="fade", duration_sec=0.5),
            BoundaryTransition(after_clip_index=1, type="dissolve", duration_sec=1.0),
        ])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=4, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_triple_duplicate_raises(self) -> None:
        """Three entries with the same index -> INVALID_INPUT."""
        opts = _per_opts([_boundary(0), _boundary(0), _boundary(0)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=3, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# (5) n_clips < 2
# ---------------------------------------------------------------------------


class TestNClipsLessThanTwo:
    """n_clips < 2 must raise INVALID_INPUT with a hint about clipwright-sequence/trim."""

    def test_n_clips_1_raises(self) -> None:
        """Single-clip timeline -> INVALID_INPUT."""
        opts = _uniform_opts()
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=1, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_n_clips_0_raises(self) -> None:
        """Zero clips -> INVALID_INPUT."""
        opts = _uniform_opts()
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=0, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_n_clips_1_hint_mentions_sequence_or_trim(self) -> None:
        """Hint must reference clipwright-sequence or clipwright-trim."""
        opts = _uniform_opts()
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=1, options=opts)

        hint = exc_info.value.hint.lower()
        # Must mention at least one of the two tools that create multi-clip timelines
        assert "sequence" in hint or "trim" in hint

    def test_n_clips_1_per_boundary_also_raises(self) -> None:
        """n_clips=1 with per_boundary options also raises INVALID_INPUT."""
        opts = _per_opts([_boundary(0)])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=1, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_negative_n_clips_raises(self) -> None:
        """Negative n_clips (defensive guard) -> INVALID_INPUT."""
        opts = _uniform_opts()
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_transitions(n_clips=-1, options=opts)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# ResolvedTransition dataclass contract
# ---------------------------------------------------------------------------


class TestResolvedTransitionContract:
    """Verify ResolvedTransition is a frozen dataclass with the expected fields."""

    def test_resolved_transition_has_required_fields(self) -> None:
        """ResolvedTransition can be constructed with after_clip_index, type, duration_sec."""
        rt = ResolvedTransition(after_clip_index=0, type="fade", duration_sec=0.5)
        assert rt.after_clip_index == 0
        assert rt.type == "fade"
        assert rt.duration_sec == 0.5

    def test_resolved_transition_equality(self) -> None:
        """Two ResolvedTransitions with identical fields must be equal (dataclass default)."""
        a = ResolvedTransition(after_clip_index=1, type="dissolve", duration_sec=1.0)
        b = ResolvedTransition(after_clip_index=1, type="dissolve", duration_sec=1.0)
        assert a == b

    def test_resolved_transition_frozen_prevents_mutation(self) -> None:
        """Frozen dataclass must raise on attribute assignment."""
        rt = ResolvedTransition(after_clip_index=0, type="fade", duration_sec=0.5)
        with pytest.raises((AttributeError, TypeError)):
            rt.type = "dissolve"  # type: ignore[misc]
