"""test_schemas.py — Tests for clipwright-transition schema contract (ADR-T-5, T-7).

Covers schema-layer contract only:
  - TransitionSpec: type Literal allowlist, duration_sec gt=0 le=5.0
  - BoundaryTransition: after_clip_index ge=0, type Literal, duration_sec
  - AddTransitionOptions: uniform | per_boundary exclusive (model_validator)
    - both set         -> ValidationError
    - both empty/None  -> ValidationError
    - exactly one      -> OK
    - per_boundary=[]  -> treated as unset (empty = unspecified)
  - All models: ConfigDict(extra="forbid", allow_inf_nan=False)

NOTE: after_clip_index upper-bound (n_clips-2) is deferred to plan.py because
n_clips is unknown at schema time.  Those tests belong in test_plan.py.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_transition.schemas import (
    AddTransitionOptions,
    BoundaryTransition,
    TransitionSpec,
)

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_VALID_TYPES = ["fade", "dissolve", "fadeblack", "fadewhite"]
_VALID_DURATION = 1.0  # representative valid value


def _spec(type: str = "fade", duration_sec: float = _VALID_DURATION) -> TransitionSpec:
    return TransitionSpec(type=type, duration_sec=duration_sec)


def _bt(
    after_clip_index: int = 0,
    type: str = "fade",
    duration_sec: float = _VALID_DURATION,
) -> BoundaryTransition:
    return BoundaryTransition(
        after_clip_index=after_clip_index,
        type=type,
        duration_sec=duration_sec,
    )


# ===========================================================================
# TransitionSpec — valid construction
# ===========================================================================


class TestTransitionSpecValidValues:
    """TransitionSpec accepts all four allowed type values and valid duration_sec."""

    @pytest.mark.parametrize("t", _VALID_TYPES)
    def test_valid_type(self, t: str) -> None:
        """All four Literal type values must be accepted."""
        spec = TransitionSpec(type=t, duration_sec=_VALID_DURATION)
        assert spec.type == t

    def test_duration_sec_small_positive(self) -> None:
        """duration_sec just above 0 (gt=0 lower boundary) must be accepted."""
        spec = TransitionSpec(type="fade", duration_sec=0.001)
        assert spec.duration_sec == pytest.approx(0.001)

    def test_duration_sec_upper_boundary(self) -> None:
        """duration_sec=5.0 is the le=5.0 upper boundary and must be accepted."""
        spec = TransitionSpec(type="dissolve", duration_sec=5.0)
        assert spec.duration_sec == pytest.approx(5.0)

    def test_duration_sec_midrange(self) -> None:
        """A typical mid-range value (0.5 s) must be accepted."""
        spec = TransitionSpec(type="fadeblack", duration_sec=0.5)
        assert spec.duration_sec == pytest.approx(0.5)


# ===========================================================================
# TransitionSpec — type allowlist
# ===========================================================================


class TestTransitionSpecTypeAllowlist:
    """TransitionSpec rejects type values outside the Literal allowlist."""

    @pytest.mark.parametrize(
        "bad_type",
        ["xfade", "wipe", "slide", "FADE", "Fade", "", "None", "null"],
    )
    def test_invalid_type_rejected(self, bad_type: str) -> None:
        """Any string not in the allowlist raises ValidationError."""
        with pytest.raises(ValidationError):
            TransitionSpec(type=bad_type, duration_sec=_VALID_DURATION)

    def test_none_type_rejected(self) -> None:
        """type=None is not in the Literal allowlist and must be rejected."""
        with pytest.raises(ValidationError):
            TransitionSpec(type=None, duration_sec=_VALID_DURATION)  # type: ignore[arg-type]


# ===========================================================================
# TransitionSpec — duration_sec constraints (gt=0, le=5.0)
# ===========================================================================


class TestTransitionSpecDurationConstraints:
    """TransitionSpec enforces gt=0 (strictly positive) and le=5.0 (max 5 s)."""

    def test_zero_duration_rejected(self) -> None:
        """duration_sec=0.0 violates gt=0 and must be rejected."""
        with pytest.raises(ValidationError):
            TransitionSpec(type="fade", duration_sec=0.0)

    @pytest.mark.parametrize("d", [-0.001, -1.0, -5.0])
    def test_negative_duration_rejected(self, d: float) -> None:
        """Negative duration_sec must be rejected (gt=0)."""
        with pytest.raises(ValidationError):
            TransitionSpec(type="fade", duration_sec=d)

    def test_just_above_upper_bound_rejected(self) -> None:
        """duration_sec just above 5.0 violates le=5.0 and must be rejected."""
        with pytest.raises(ValidationError):
            TransitionSpec(type="fade", duration_sec=5.0001)

    def test_large_duration_rejected(self) -> None:
        """A large duration (100 s) violates le=5.0 and must be rejected."""
        with pytest.raises(ValidationError):
            TransitionSpec(type="fade", duration_sec=100.0)


# ===========================================================================
# TransitionSpec — allow_inf_nan=False
# ===========================================================================


class TestTransitionSpecInfNan:
    """TransitionSpec rejects inf and nan for duration_sec (allow_inf_nan=False)."""

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_duration_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for duration_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            TransitionSpec(type="fade", duration_sec=value)


# ===========================================================================
# TransitionSpec — extra="forbid"
# ===========================================================================


class TestTransitionSpecExtraForbid:
    """TransitionSpec rejects unknown fields (extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """An unrecognised keyword argument raises ValidationError."""
        with pytest.raises(ValidationError):
            TransitionSpec(  # type: ignore[call-arg]
                type="fade", duration_sec=_VALID_DURATION, unknown="x"
            )

    def test_typo_field_rejected(self) -> None:
        """A typo such as 'duration' instead of 'duration_sec' is rejected."""
        with pytest.raises(ValidationError):
            TransitionSpec(type="fade", duration=_VALID_DURATION)  # type: ignore[call-arg]


# ===========================================================================
# BoundaryTransition — valid construction
# ===========================================================================


class TestBoundaryTransitionValidValues:
    """BoundaryTransition accepts valid combinations of all three fields."""

    def test_minimal_valid(self) -> None:
        """after_clip_index=0 is the ge=0 lower boundary and must be accepted."""
        bt = BoundaryTransition(
            after_clip_index=0, type="fade", duration_sec=_VALID_DURATION
        )
        assert bt.after_clip_index == 0
        assert bt.type == "fade"
        assert bt.duration_sec == pytest.approx(_VALID_DURATION)

    @pytest.mark.parametrize("t", _VALID_TYPES)
    def test_all_type_values(self, t: str) -> None:
        """All four type values must be accepted in BoundaryTransition."""
        bt = BoundaryTransition(after_clip_index=1, type=t, duration_sec=0.5)
        assert bt.type == t

    def test_large_after_clip_index(self) -> None:
        """Large after_clip_index values (schema allows any ge=0 int) must be accepted."""
        bt = BoundaryTransition(
            after_clip_index=999, type="dissolve", duration_sec=1.0
        )
        assert bt.after_clip_index == 999

    def test_duration_sec_boundary_values(self) -> None:
        """duration_sec boundary values (just above 0 and exactly 5.0) must pass."""
        bt_low = BoundaryTransition(
            after_clip_index=0, type="fade", duration_sec=0.001
        )
        bt_high = BoundaryTransition(
            after_clip_index=0, type="fade", duration_sec=5.0
        )
        assert bt_low.duration_sec == pytest.approx(0.001)
        assert bt_high.duration_sec == pytest.approx(5.0)


# ===========================================================================
# BoundaryTransition — after_clip_index constraint (ge=0)
# ===========================================================================


class TestBoundaryTransitionAfterClipIndex:
    """BoundaryTransition rejects negative after_clip_index (ge=0)."""

    @pytest.mark.parametrize("idx", [-1, -2, -100])
    def test_negative_index_rejected(self, idx: int) -> None:
        """Negative after_clip_index violates ge=0 and must be rejected."""
        with pytest.raises(ValidationError):
            BoundaryTransition(
                after_clip_index=idx, type="fade", duration_sec=_VALID_DURATION
            )


# ===========================================================================
# BoundaryTransition — duration_sec constraints (gt=0, le=5.0)
# ===========================================================================


class TestBoundaryTransitionDurationConstraints:
    """BoundaryTransition enforces gt=0 and le=5.0 on duration_sec."""

    def test_zero_duration_rejected(self) -> None:
        """duration_sec=0.0 violates gt=0."""
        with pytest.raises(ValidationError):
            BoundaryTransition(
                after_clip_index=0, type="fade", duration_sec=0.0
            )

    @pytest.mark.parametrize("d", [-0.001, -1.0])
    def test_negative_duration_rejected(self, d: float) -> None:
        """Negative duration_sec must be rejected."""
        with pytest.raises(ValidationError):
            BoundaryTransition(after_clip_index=0, type="fade", duration_sec=d)

    def test_above_upper_bound_rejected(self) -> None:
        """duration_sec=5.0001 violates le=5.0."""
        with pytest.raises(ValidationError):
            BoundaryTransition(
                after_clip_index=0, type="fade", duration_sec=5.0001
            )


# ===========================================================================
# BoundaryTransition — allow_inf_nan=False
# ===========================================================================


class TestBoundaryTransitionInfNan:
    """BoundaryTransition rejects inf and nan (allow_inf_nan=False)."""

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_duration_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for duration_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            BoundaryTransition(
                after_clip_index=0, type="fade", duration_sec=value
            )


# ===========================================================================
# BoundaryTransition — type allowlist
# ===========================================================================


class TestBoundaryTransitionTypeAllowlist:
    """BoundaryTransition rejects type values outside the Literal allowlist."""

    @pytest.mark.parametrize("bad_type", ["xfade", "wipe", "FADE", ""])
    def test_invalid_type_rejected(self, bad_type: str) -> None:
        """Any string not in the allowlist raises ValidationError."""
        with pytest.raises(ValidationError):
            BoundaryTransition(
                after_clip_index=0, type=bad_type, duration_sec=_VALID_DURATION
            )


# ===========================================================================
# BoundaryTransition — extra="forbid"
# ===========================================================================


class TestBoundaryTransitionExtraForbid:
    """BoundaryTransition rejects unknown fields (extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """An unrecognised keyword argument raises ValidationError."""
        with pytest.raises(ValidationError):
            BoundaryTransition(  # type: ignore[call-arg]
                after_clip_index=0,
                type="fade",
                duration_sec=_VALID_DURATION,
                extra_key="value",
            )


# ===========================================================================
# AddTransitionOptions — valid construction (exactly one mode)
# ===========================================================================


class TestAddTransitionOptionsValidValues:
    """AddTransitionOptions accepts exactly one of uniform or per_boundary (non-empty)."""

    def test_uniform_only(self) -> None:
        """uniform provided, per_boundary omitted -> valid."""
        opts = AddTransitionOptions(uniform=_spec())
        assert opts.uniform is not None
        assert opts.per_boundary is None

    def test_per_boundary_only_single(self) -> None:
        """per_boundary with one entry, uniform=None -> valid."""
        opts = AddTransitionOptions(per_boundary=[_bt()])
        assert opts.uniform is None
        assert opts.per_boundary is not None
        assert len(opts.per_boundary) == 1

    def test_per_boundary_only_multiple(self) -> None:
        """per_boundary with multiple entries, uniform=None -> valid."""
        opts = AddTransitionOptions(
            per_boundary=[_bt(0), _bt(1, type="dissolve")]
        )
        assert len(opts.per_boundary) == 2  # type: ignore[arg-type]

    def test_uniform_with_per_boundary_none(self) -> None:
        """uniform provided, per_boundary explicitly None -> valid."""
        opts = AddTransitionOptions(uniform=_spec("dissolve"), per_boundary=None)
        assert opts.uniform is not None
        assert opts.per_boundary is None

    def test_per_boundary_with_uniform_none(self) -> None:
        """per_boundary provided, uniform explicitly None -> valid."""
        opts = AddTransitionOptions(uniform=None, per_boundary=[_bt()])
        assert opts.per_boundary is not None
        assert opts.uniform is None


# ===========================================================================
# AddTransitionOptions — both specified -> ValidationError
# ===========================================================================


class TestAddTransitionOptionsBothSpecified:
    """AddTransitionOptions rejects when both uniform and per_boundary are non-empty."""

    def test_both_uniform_and_per_boundary_raises(self) -> None:
        """Both non-empty -> ValidationError (model_validator exactly-one check)."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(
                uniform=_spec(),
                per_boundary=[_bt()],
            )

    def test_both_specified_multiple_boundaries_raises(self) -> None:
        """Both non-empty (multiple boundaries) -> ValidationError."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(
                uniform=_spec("dissolve", 0.5),
                per_boundary=[_bt(0), _bt(1)],
            )


# ===========================================================================
# AddTransitionOptions — both empty -> ValidationError
# ===========================================================================


class TestAddTransitionOptionsBothEmpty:
    """AddTransitionOptions rejects when both uniform and per_boundary are absent."""

    def test_both_none_raises(self) -> None:
        """uniform=None and per_boundary=None -> ValidationError."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(uniform=None, per_boundary=None)

    def test_no_fields_raises(self) -> None:
        """Omitting both fields -> ValidationError (both default to None)."""
        with pytest.raises(ValidationError):
            AddTransitionOptions()

    def test_uniform_none_per_boundary_empty_list_raises(self) -> None:
        """per_boundary=[] is treated as unset (empty list == unspecified), same as None.

        Both uniform=None and per_boundary=[] -> ValidationError because
        has_per uses len > 0 check, so empty list counts as 'not set'.
        """
        with pytest.raises(ValidationError):
            AddTransitionOptions(uniform=None, per_boundary=[])

    def test_uniform_none_per_boundary_empty_raises(self) -> None:
        """Explicit uniform=None with empty per_boundary -> ValidationError."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(per_boundary=[])


# ===========================================================================
# AddTransitionOptions — empty list as unspecified (per_boundary=[])
# ===========================================================================


class TestAddTransitionOptionsEmptyListUnspecified:
    """per_boundary=[] is treated as 'not provided' (same semantic as None).

    This means per_boundary=[] + uniform=spec is VALID (only uniform is set).
    """

    def test_uniform_with_empty_per_boundary_is_valid(self) -> None:
        """uniform provided and per_boundary=[] -> valid (empty list = unset)."""
        opts = AddTransitionOptions(uniform=_spec(), per_boundary=[])
        assert opts.uniform is not None
        # per_boundary may be [] or None depending on implementation; either is fine
        assert not opts.per_boundary  # empty or None -> falsy


# ===========================================================================
# AddTransitionOptions — per_boundary max_length=1000
# ===========================================================================


class TestAddTransitionOptionsMaxLength:
    """per_boundary accepts up to 1000 entries (max_length=1000)."""

    def test_max_length_boundary(self) -> None:
        """Exactly 1000 BoundaryTransition entries must be accepted."""
        boundaries = [_bt(i) for i in range(1000)]
        opts = AddTransitionOptions(per_boundary=boundaries)
        assert opts.per_boundary is not None
        assert len(opts.per_boundary) == 1000

    def test_exceeds_max_length_raises(self) -> None:
        """1001 entries exceeds max_length=1000 and must raise ValidationError."""
        boundaries = [_bt(i) for i in range(1001)]
        with pytest.raises(ValidationError):
            AddTransitionOptions(per_boundary=boundaries)


# ===========================================================================
# AddTransitionOptions — extra="forbid"
# ===========================================================================


class TestAddTransitionOptionsExtraForbid:
    """AddTransitionOptions rejects unknown fields (extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """An unrecognised keyword argument raises ValidationError."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(  # type: ignore[call-arg]
                uniform=_spec(), unknown_field="value"
            )


# ===========================================================================
# AddTransitionOptions — allow_inf_nan=False (propagated through nested models)
# ===========================================================================


class TestAddTransitionOptionsInfNan:
    """AddTransitionOptions rejects inf/nan propagated through nested models."""

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_uniform_duration_inf_nan_rejected(self, value: float) -> None:
        """inf/nan in uniform.duration_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(
                uniform=TransitionSpec(type="fade", duration_sec=value)
            )

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_per_boundary_duration_inf_nan_rejected(self, value: float) -> None:
        """inf/nan in per_boundary[*].duration_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            AddTransitionOptions(
                per_boundary=[
                    BoundaryTransition(
                        after_clip_index=0, type="fade", duration_sec=value
                    )
                ]
            )


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_transition_schemas_does_not_redefine_core_types() -> None:
    """clipwright_transition.schemas must not redefine core common types."""
    # core types must be importable
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    import clipwright_transition.schemas as tr_schemas

    assert not hasattr(tr_schemas, "MediaRef"), (
        "schemas.py must not redefine MediaRef from core"
    )
    assert not hasattr(tr_schemas, "Artifact"), (
        "schemas.py must not redefine Artifact from core"
    )
    assert not hasattr(tr_schemas, "ToolResult"), (
        "schemas.py must not redefine ToolResult from core"
    )
