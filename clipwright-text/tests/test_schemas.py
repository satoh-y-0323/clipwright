"""Tests for AddTextOptions schema validation.

Covers:
- Required field rejection (text / start_sec / duration_sec missing)
- Unknown key rejection (extra="forbid")
- inf/nan rejection (allow_inf_nan=False)
- Default values for all optional fields
- No redefinition of core common types

Note: Value-range validation (start_sec>=0, duration_sec>0, etc.) is NOT
enforced at the Pydantic schema level per OQ-1 — it is validated manually
inside _add_text_inner. Therefore those tests belong in test_text.py.
The tests below reflect the SCHEMA contract only.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_text.schemas import AddTextOptions

# ===========================================================================
# Required fields: text, start_sec, duration_sec
# ===========================================================================


class TestRequiredFields:
    """All three required fields must be present; omitting any raises ValidationError."""

    def test_all_required_present_constructs(self) -> None:
        """AddTextOptions with all required fields must construct without error."""
        opts = AddTextOptions(text="Hello", start_sec=1.0, duration_sec=3.0)
        assert opts.text == "Hello"
        assert opts.start_sec == pytest.approx(1.0)
        assert opts.duration_sec == pytest.approx(3.0)

    def test_missing_text_raises(self) -> None:
        """Omitting text must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(start_sec=1.0, duration_sec=3.0)  # type: ignore[call-arg]

    def test_missing_start_sec_raises(self) -> None:
        """Omitting start_sec must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", duration_sec=3.0)  # type: ignore[call-arg]

    def test_missing_duration_sec_raises(self) -> None:
        """Omitting duration_sec must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", start_sec=1.0)  # type: ignore[call-arg]

    def test_missing_all_required_raises(self) -> None:
        """Omitting all required fields must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions()  # type: ignore[call-arg]

    def test_missing_text_and_start_sec_raises(self) -> None:
        """Omitting text and start_sec must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(duration_sec=3.0)  # type: ignore[call-arg]


# ===========================================================================
# extra="forbid": unknown keys rejected
# ===========================================================================


class TestExtraForbid:
    """Unknown fields must be rejected (model_config extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """Passing an unknown keyword must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(  # type: ignore[call-arg]
                text="Hello", start_sec=1.0, duration_sec=3.0, unknown_field=123
            )

    def test_multiple_unknown_fields_rejected(self) -> None:
        """Multiple unknown keywords must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(  # type: ignore[call-arg]
                text="Hello", start_sec=1.0, duration_sec=3.0, foo="bar", baz=42
            )

    def test_typo_field_rejected(self) -> None:
        """A typo in a valid field name must be rejected as unknown."""
        with pytest.raises(ValidationError):
            AddTextOptions(  # type: ignore[call-arg]
                text="Hello", start_sec=1.0, duration_sec=3.0, font_sizes=48
            )


# ===========================================================================
# allow_inf_nan=False: inf and nan rejected for float fields
# ===========================================================================


class TestInfNanRejected:
    """inf and nan must be rejected for float fields (model_config allow_inf_nan=False)."""

    def test_start_sec_inf_rejected(self) -> None:
        """start_sec=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", start_sec=float("inf"), duration_sec=3.0)

    def test_start_sec_neg_inf_rejected(self) -> None:
        """start_sec=-inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", start_sec=float("-inf"), duration_sec=3.0)

    def test_start_sec_nan_rejected(self) -> None:
        """start_sec=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", start_sec=float("nan"), duration_sec=3.0)

    def test_duration_sec_inf_rejected(self) -> None:
        """duration_sec=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", start_sec=1.0, duration_sec=float("inf"))

    def test_duration_sec_nan_rejected(self) -> None:
        """duration_sec=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(text="Hello", start_sec=1.0, duration_sec=float("nan"))

    def test_fade_in_sec_inf_rejected(self) -> None:
        """fade_in_sec=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(
                text="Hello",
                start_sec=1.0,
                duration_sec=3.0,
                fade_in_sec=math.inf,
            )

    def test_fade_out_sec_nan_rejected(self) -> None:
        """fade_out_sec=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddTextOptions(
                text="Hello",
                start_sec=1.0,
                duration_sec=3.0,
                fade_out_sec=math.nan,
            )


# ===========================================================================
# Default values for all optional fields
# ===========================================================================


class TestDefaultValues:
    """All optional fields must have the specified defaults when not provided."""

    def _make_minimal(self) -> AddTextOptions:
        return AddTextOptions(text="Hello", start_sec=1.0, duration_sec=3.0)

    def test_default_x(self) -> None:
        """x default must be '(w-tw)/2' (horizontal center)."""
        assert self._make_minimal().x == "(w-tw)/2"

    def test_default_y(self) -> None:
        """y default must be 'h-th-40' (lower third)."""
        assert self._make_minimal().y == "h-th-40"

    def test_default_font_size(self) -> None:
        """font_size default must be 48."""
        assert self._make_minimal().font_size == 48

    def test_default_font_color(self) -> None:
        """font_color default must be 'white'."""
        assert self._make_minimal().font_color == "white"

    def test_default_box(self) -> None:
        """box default must be False."""
        assert self._make_minimal().box is False

    def test_default_box_color(self) -> None:
        """box_color default must be 'black@0.5'."""
        assert self._make_minimal().box_color == "black@0.5"

    def test_default_fade_in_sec(self) -> None:
        """fade_in_sec default must be 0.3."""
        assert self._make_minimal().fade_in_sec == pytest.approx(0.3)

    def test_default_fade_out_sec(self) -> None:
        """fade_out_sec default must be 0.3."""
        assert self._make_minimal().fade_out_sec == pytest.approx(0.3)

    def test_default_font_path(self) -> None:
        """font_path default must be None."""
        assert self._make_minimal().font_path is None


# ===========================================================================
# Field presence in model_fields
# ===========================================================================


class TestFieldExistence:
    """All 12 schema fields must be present in model_fields."""

    _EXPECTED_FIELDS = {
        "text",
        "start_sec",
        "duration_sec",
        "x",
        "y",
        "font_size",
        "font_color",
        "box",
        "box_color",
        "fade_in_sec",
        "fade_out_sec",
        "font_path",
    }

    def test_all_expected_fields_exist(self) -> None:
        """model_fields must contain exactly the 12 specified fields."""
        actual = set(AddTextOptions.model_fields.keys())
        assert actual == self._EXPECTED_FIELDS

    def test_text_field_exists(self) -> None:
        """model_fields must contain 'text'."""
        assert "text" in AddTextOptions.model_fields

    def test_start_sec_field_exists(self) -> None:
        """model_fields must contain 'start_sec'."""
        assert "start_sec" in AddTextOptions.model_fields

    def test_duration_sec_field_exists(self) -> None:
        """model_fields must contain 'duration_sec'."""
        assert "duration_sec" in AddTextOptions.model_fields


# ===========================================================================
# No redefinition of core common types
# ===========================================================================


def test_add_text_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    import clipwright_text.schemas as text_schemas

    assert not hasattr(text_schemas, "MediaRef"), (
        "schemas.py redefines MediaRef from core"
    )
    assert not hasattr(text_schemas, "Artifact"), (
        "schemas.py redefines Artifact from core"
    )
    assert not hasattr(text_schemas, "ToolResult"), (
        "schemas.py redefines ToolResult from core"
    )


# ===========================================================================
# Type coercion: font_size must be int, not float
# ===========================================================================


class TestFieldTypes:
    """Field types must match the declared types."""

    def test_font_size_accepts_int(self) -> None:
        """font_size must accept an integer value."""
        opts = AddTextOptions(text="Hello", start_sec=1.0, duration_sec=3.0, font_size=64)
        assert opts.font_size == 64
        assert isinstance(opts.font_size, int)

    def test_box_accepts_bool_true(self) -> None:
        """box=True must be accepted."""
        opts = AddTextOptions(text="Hello", start_sec=1.0, duration_sec=3.0, box=True)
        assert opts.box is True

    def test_font_path_accepts_string(self) -> None:
        """font_path must accept a non-None string."""
        opts = AddTextOptions(
            text="Hello",
            start_sec=1.0,
            duration_sec=3.0,
            font_path="/path/to/font.ttf",
        )
        assert opts.font_path == "/path/to/font.ttf"
