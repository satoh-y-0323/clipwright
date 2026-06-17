"""Tests for SetSpeedOptions schema validation.

Covers:
- Boundary acceptance at 0.25 and 8.0 (schema-level; runtime range check is separate)
- Rejection of 0.24, 8.01, inf, nan (allow_inf_nan=False)
- Unknown key rejected (extra="forbid")
- clip_index negative rejected (ge=0)
- speed is a required field (no default)

Note: The speed range (0.25-8.0) is NOT enforced by Pydantic constraints per
decision OQ-1 — it is validated manually inside _set_speed_inner. Therefore,
0.24 and 8.01 are accepted at the schema level but are rejected at runtime.
The tests below reflect the SCHEMA contract only; runtime range tests belong
in test_speed.py.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_speed.schemas import SetSpeedOptions


# ===========================================================================
# speed: required field (no default)
# ===========================================================================


class TestSpeedRequired:
    """speed must be required — constructing without it raises ValidationError."""

    def test_speed_required_raises_without_it(self) -> None:
        """SetSpeedOptions() without speed must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions()  # type: ignore[call-arg]

    def test_speed_required_raises_with_only_clip_index(self) -> None:
        """SetSpeedOptions(clip_index=0) without speed must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(clip_index=0)  # type: ignore[call-arg]


# ===========================================================================
# speed: boundary values accepted at schema level
# ===========================================================================


class TestSpeedBoundaryAccepted:
    """Speed boundary values 0.25 and 8.0 must be accepted at the schema level.

    Note: schema does NOT enforce 0.25-8.0 range per OQ-1. These tests confirm
    that valid boundary values pass schema validation.
    """

    def test_speed_0_25_accepted(self) -> None:
        """speed=0.25 (lower boundary) must be accepted by the schema."""
        opts = SetSpeedOptions(speed=0.25)
        assert opts.speed == pytest.approx(0.25)

    def test_speed_8_0_accepted(self) -> None:
        """speed=8.0 (upper boundary) must be accepted by the schema."""
        opts = SetSpeedOptions(speed=8.0)
        assert opts.speed == pytest.approx(8.0)

    def test_speed_1_0_accepted(self) -> None:
        """speed=1.0 (identity warp) must be accepted by the schema."""
        opts = SetSpeedOptions(speed=1.0)
        assert opts.speed == pytest.approx(1.0)

    def test_speed_0_5_accepted(self) -> None:
        """speed=0.5 (half speed) must be accepted by the schema."""
        opts = SetSpeedOptions(speed=0.5)
        assert opts.speed == pytest.approx(0.5)

    def test_speed_2_0_accepted(self) -> None:
        """speed=2.0 (double speed) must be accepted by the schema."""
        opts = SetSpeedOptions(speed=2.0)
        assert opts.speed == pytest.approx(2.0)


# ===========================================================================
# speed: inf and nan rejected (allow_inf_nan=False)
# ===========================================================================


class TestSpeedInfNanRejected:
    """inf and nan must be rejected for speed (model_config allow_inf_nan=False)."""

    def test_speed_inf_rejected(self) -> None:
        """speed=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=float("inf"))

    def test_speed_neg_inf_rejected(self) -> None:
        """speed=-inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=float("-inf"))

    def test_speed_nan_rejected(self) -> None:
        """speed=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=float("nan"))

    def test_speed_math_inf_rejected(self) -> None:
        """speed=math.inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=math.inf)

    def test_speed_math_nan_rejected(self) -> None:
        """speed=math.nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=math.nan)


# ===========================================================================
# speed: schema does NOT enforce 0.25-8.0 range (OQ-1)
# Values 0.24 and 8.01 pass schema validation; they fail at runtime.
# ===========================================================================


class TestSpeedSchemaAllowsOutOfRangeValues:
    """Per OQ-1, schema does not constrain 0.25-8.0; out-of-range passes schema.

    Runtime rejection is tested in test_speed.py.
    """

    def test_speed_0_24_accepted_at_schema_level(self) -> None:
        """speed=0.24 passes schema validation (runtime rejects it via INVALID_INPUT)."""
        opts = SetSpeedOptions(speed=0.24)
        assert opts.speed == pytest.approx(0.24)

    def test_speed_8_01_accepted_at_schema_level(self) -> None:
        """speed=8.01 passes schema validation (runtime rejects it via INVALID_INPUT)."""
        opts = SetSpeedOptions(speed=8.01)
        assert opts.speed == pytest.approx(8.01)


# ===========================================================================
# extra="forbid": unknown keys rejected
# ===========================================================================


class TestExtraForbid:
    """Unknown fields must be rejected (model_config extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """Passing an unknown keyword must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=2.0, unknown_field=123)  # type: ignore[call-arg]

    def test_multiple_unknown_fields_rejected(self) -> None:
        """Multiple unknown keywords must raise ValidationError."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=2.0, foo="bar", baz=42)  # type: ignore[call-arg]

    def test_typo_field_rejected(self) -> None:
        """A typo in a valid field name must be rejected as unknown."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=2.0, clip_idx=0)  # type: ignore[call-arg]


# ===========================================================================
# clip_index: valid values accepted
# ===========================================================================


class TestClipIndexValid:
    """clip_index must accept None and non-negative integers."""

    def test_clip_index_none_accepted(self) -> None:
        """clip_index=None (default) must be accepted."""
        opts = SetSpeedOptions(speed=2.0)
        assert opts.clip_index is None

    def test_clip_index_zero_accepted(self) -> None:
        """clip_index=0 must be accepted (ge=0)."""
        opts = SetSpeedOptions(speed=2.0, clip_index=0)
        assert opts.clip_index == 0

    def test_clip_index_positive_accepted(self) -> None:
        """clip_index=5 must be accepted."""
        opts = SetSpeedOptions(speed=2.0, clip_index=5)
        assert opts.clip_index == 5


# ===========================================================================
# clip_index: negative rejected (ge=0)
# ===========================================================================


class TestClipIndexNegativeRejected:
    """Negative clip_index must be rejected (constraint: ge=0)."""

    def test_clip_index_minus_one_rejected(self) -> None:
        """clip_index=-1 must raise ValidationError (constraint: ge=0)."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=2.0, clip_index=-1)

    def test_clip_index_large_negative_rejected(self) -> None:
        """clip_index=-100 must raise ValidationError (constraint: ge=0)."""
        with pytest.raises(ValidationError):
            SetSpeedOptions(speed=2.0, clip_index=-100)


# ===========================================================================
# model_fields presence
# ===========================================================================


class TestFieldExistence:
    """Required fields must be present in model_fields."""

    def test_speed_field_exists(self) -> None:
        """model_fields must contain 'speed'."""
        assert "speed" in SetSpeedOptions.model_fields

    def test_clip_index_field_exists(self) -> None:
        """model_fields must contain 'clip_index'."""
        assert "clip_index" in SetSpeedOptions.model_fields

    def test_no_extra_fields(self) -> None:
        """model_fields must contain exactly 'speed' and 'clip_index'."""
        actual = set(SetSpeedOptions.model_fields.keys())
        assert actual == {"speed", "clip_index"}


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_set_speed_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    import clipwright_speed.schemas as speed_schemas

    assert not hasattr(speed_schemas, "MediaRef"), "schemas.py redefines MediaRef from core"
    assert not hasattr(speed_schemas, "Artifact"), "schemas.py redefines Artifact from core"
    assert not hasattr(speed_schemas, "ToolResult"), "schemas.py redefines ToolResult from core"
