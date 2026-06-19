"""test_schemas.py — Tests for TrimRange and TrimOptions.

Covers FR-3, FR-4, AC-9 and ADR-6.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_trim.schemas import TrimOptions, TrimRange

# ===========================================================================
# TrimRange — default construction and valid values
# ===========================================================================


class TestTrimRangeValidValues:
    """TrimRange accepts valid (start_sec, end_sec) pairs."""

    def test_basic_valid_range(self) -> None:
        # Arrange / Act
        r = TrimRange(start_sec=1.0, end_sec=5.0)

        # Assert
        assert r.start_sec == pytest.approx(1.0)
        assert r.end_sec == pytest.approx(5.0)

    def test_start_sec_zero_is_valid(self) -> None:
        """start_sec=0.0 is valid (ge=0 boundary)."""
        r = TrimRange(start_sec=0.0, end_sec=10.0)
        assert r.start_sec == pytest.approx(0.0)

    def test_float_precision_preserved(self) -> None:
        r = TrimRange(start_sec=1.5, end_sec=3.75)
        assert r.start_sec == pytest.approx(1.5)
        assert r.end_sec == pytest.approx(3.75)


# ===========================================================================
# TrimRange — start_sec constraint: ge=0
# ===========================================================================


class TestTrimRangeStartSecConstraint:
    """start_sec must satisfy ge=0 (non-negative)."""

    @pytest.mark.parametrize(
        "start_sec",
        [-0.001, -1.0, -100.0, -1e-9],
    )
    def test_negative_start_sec_rejected(self, start_sec: float) -> None:
        """Negative start_sec -> ValidationError (ge=0 constraint)."""
        with pytest.raises(ValidationError):
            TrimRange(start_sec=start_sec, end_sec=10.0)

    def test_zero_start_sec_accepted(self) -> None:
        """start_sec=0.0 is exactly the lower bound and must be accepted."""
        r = TrimRange(start_sec=0.0, end_sec=1.0)
        assert r.start_sec == pytest.approx(0.0)


# ===========================================================================
# TrimRange — allow_inf_nan=False
# ===========================================================================


class TestTrimRangeInfNan:
    """TrimRange rejects inf and nan for all float fields (allow_inf_nan=False)."""

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_start_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for start_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            TrimRange(start_sec=value, end_sec=10.0)

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_end_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for end_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            TrimRange(start_sec=0.0, end_sec=value)


# ===========================================================================
# TrimRange — extra="forbid"
# ===========================================================================


class TestTrimRangeExtraForbid:
    """TrimRange must reject unknown fields (extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrimRange(start_sec=0.0, end_sec=5.0, unknown_field="value")  # type: ignore[call-arg]

    def test_typo_field_rejected(self) -> None:
        """A typo like 'start' instead of 'start_sec' must not silently pass."""
        with pytest.raises(ValidationError):
            TrimRange(start=0.0, end_sec=5.0)  # type: ignore[call-arg]


# ===========================================================================
# TrimRange — Field(description=...) present on all fields
# ===========================================================================


class TestTrimRangeFieldDescriptions:
    """All TrimRange fields must carry a non-empty description (NFR-1 self-describing API)."""

    def test_start_sec_has_description(self) -> None:
        field_info = TrimRange.model_fields["start_sec"]
        assert field_info.description, (
            "start_sec field must have a non-empty description"
        )

    def test_end_sec_has_description(self) -> None:
        field_info = TrimRange.model_fields["end_sec"]
        assert field_info.description, "end_sec field must have a non-empty description"


# ===========================================================================
# TrimOptions — defaults
# ===========================================================================


class TestTrimOptionsDefaults:
    """TrimOptions must be constructable with all fields omitted."""

    def test_default_construction(self) -> None:
        opts = TrimOptions()
        assert opts.keep == []
        assert opts.drop == []
        assert opts.padding_sec == pytest.approx(0.0)

    def test_default_keep_is_empty_list(self) -> None:
        opts = TrimOptions()
        assert isinstance(opts.keep, list)
        assert len(opts.keep) == 0

    def test_default_drop_is_empty_list(self) -> None:
        opts = TrimOptions()
        assert isinstance(opts.drop, list)
        assert len(opts.drop) == 0

    def test_default_padding_sec_is_zero(self) -> None:
        opts = TrimOptions()
        assert opts.padding_sec == pytest.approx(0.0)


# ===========================================================================
# TrimOptions — valid construction with values
# ===========================================================================


class TestTrimOptionsValidValues:
    """TrimOptions accepts valid combinations of keep/drop/padding_sec."""

    def test_keep_mode(self) -> None:
        r = TrimRange(start_sec=1.0, end_sec=5.0)
        opts = TrimOptions(keep=[r])
        assert len(opts.keep) == 1
        assert opts.keep[0].start_sec == pytest.approx(1.0)

    def test_drop_mode(self) -> None:
        r = TrimRange(start_sec=2.0, end_sec=8.0)
        opts = TrimOptions(drop=[r])
        assert len(opts.drop) == 1
        assert opts.drop[0].end_sec == pytest.approx(8.0)

    def test_padding_sec_positive_accepted(self) -> None:
        opts = TrimOptions(padding_sec=0.5)
        assert opts.padding_sec == pytest.approx(0.5)

    def test_padding_sec_zero_accepted(self) -> None:
        opts = TrimOptions(padding_sec=0.0)
        assert opts.padding_sec == pytest.approx(0.0)

    def test_both_keep_and_drop_non_empty_accepted_at_schema_level(self) -> None:
        """ADR-6: mutual exclusion is NOT enforced at schema level.

        TrimOptions must accept both keep and drop populated; the INVALID_INPUT
        error is deferred to plan.py (derive_keep_ranges).
        """
        r1 = TrimRange(start_sec=0.0, end_sec=3.0)
        r2 = TrimRange(start_sec=5.0, end_sec=8.0)
        # This must NOT raise ValidationError
        opts = TrimOptions(keep=[r1], drop=[r2])
        assert len(opts.keep) == 1
        assert len(opts.drop) == 1

    def test_multiple_keep_ranges(self) -> None:
        r1 = TrimRange(start_sec=0.0, end_sec=3.0)
        r2 = TrimRange(start_sec=5.0, end_sec=8.0)
        opts = TrimOptions(keep=[r1, r2])
        assert len(opts.keep) == 2

    def test_multiple_drop_ranges(self) -> None:
        r1 = TrimRange(start_sec=1.0, end_sec=3.0)
        r2 = TrimRange(start_sec=6.0, end_sec=9.0)
        opts = TrimOptions(drop=[r1, r2])
        assert len(opts.drop) == 2


# ===========================================================================
# TrimOptions — padding_sec constraint: ge=0
# ===========================================================================


class TestTrimOptionsPaddingSecConstraint:
    """padding_sec must satisfy ge=0."""

    @pytest.mark.parametrize(
        "padding_sec",
        [-0.001, -0.5, -1.0, -100.0],
    )
    def test_negative_padding_sec_rejected(self, padding_sec: float) -> None:
        """Negative padding_sec -> ValidationError (ge=0 constraint)."""
        with pytest.raises(ValidationError):
            TrimOptions(padding_sec=padding_sec)


# ===========================================================================
# TrimOptions — allow_inf_nan=False
# ===========================================================================


class TestTrimOptionsInfNan:
    """TrimOptions rejects inf and nan for float fields (allow_inf_nan=False)."""

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_padding_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for padding_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            TrimOptions(padding_sec=value)


# ===========================================================================
# TrimOptions — extra="forbid"
# ===========================================================================


class TestTrimOptionsExtraForbid:
    """TrimOptions must reject unknown fields (extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrimOptions(unknown_field="value")  # type: ignore[call-arg]

    def test_typo_field_rejected(self) -> None:
        """A typo like 'padding' instead of 'padding_sec' must not silently pass."""
        with pytest.raises(ValidationError):
            TrimOptions(padding=0.5)  # type: ignore[call-arg]


# ===========================================================================
# TrimOptions — Field(description=...) present on all fields
# ===========================================================================


class TestTrimOptionsFieldDescriptions:
    """All TrimOptions fields must carry a non-empty description (NFR-1)."""

    def test_keep_has_description(self) -> None:
        field_info = TrimOptions.model_fields["keep"]
        assert field_info.description, "keep field must have a non-empty description"

    def test_drop_has_description(self) -> None:
        field_info = TrimOptions.model_fields["drop"]
        assert field_info.description, "drop field must have a non-empty description"

    def test_padding_sec_has_description(self) -> None:
        field_info = TrimOptions.model_fields["padding_sec"]
        assert field_info.description, (
            "padding_sec field must have a non-empty description"
        )


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_trim_schemas_does_not_redefine_core_types() -> None:
    """clipwright_trim.schemas must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    # core common types must be importable
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    import clipwright_trim.schemas as trim_schemas

    assert not hasattr(trim_schemas, "MediaRef"), (
        "schemas.py redefines MediaRef from core"
    )
    assert not hasattr(trim_schemas, "Artifact"), (
        "schemas.py redefines Artifact from core"
    )
    assert not hasattr(trim_schemas, "ToolResult"), (
        "schemas.py redefines ToolResult from core"
    )
