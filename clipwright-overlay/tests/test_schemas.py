"""Tests for AddOverlayOptions schema validation.

Covers:
- Required field rejection (image_path / start_sec / duration_sec missing)
- Unknown key rejection (extra="forbid")
- inf/nan rejection (allow_inf_nan=False) for all float fields
- Default values for all optional fields
- scale Field(gt=0, le=8.0) per V2-9: schema layer rejects scale<=0 and scale>8.0
- No redefinition of core common types

Note: Value-range validation OTHER than the scale Field constraint (start_sec>=0,
duration_sec>0, opacity 0..1, fade_in+fade_out<=duration_sec) is NOT enforced at
the Pydantic schema level per OQ-1 — it is validated manually inside overlay.py
for precise hints.  The tests below reflect the SCHEMA contract only.

Note: The _MAX_IMAGE_OVERLAYS=64 cap and image_path 4-stage validation (co-location /
existence / allowlist / safety) are exercised in test_overlay.py, not here.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_overlay.schemas import AddOverlayOptions

# ===========================================================================
# Required fields: image_path, start_sec, duration_sec
# ===========================================================================


class TestRequiredFields:
    """All three required fields must be present; omitting any raises ValidationError."""

    def test_all_required_present_constructs(self) -> None:
        """AddOverlayOptions with all required fields must construct without error."""
        opts = AddOverlayOptions(image_path="logo.png", start_sec=1.0, duration_sec=3.0)
        assert opts.image_path == "logo.png"
        assert opts.start_sec == pytest.approx(1.0)
        assert opts.duration_sec == pytest.approx(3.0)

    def test_missing_image_path_raises(self) -> None:
        """Omitting image_path must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(start_sec=1.0, duration_sec=3.0)  # type: ignore[call-arg]

    def test_missing_start_sec_raises(self) -> None:
        """Omitting start_sec must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(image_path="logo.png", duration_sec=3.0)  # type: ignore[call-arg]

    def test_missing_duration_sec_raises(self) -> None:
        """Omitting duration_sec must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(image_path="logo.png", start_sec=1.0)  # type: ignore[call-arg]

    def test_missing_all_required_raises(self) -> None:
        """Omitting all required fields must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions()  # type: ignore[call-arg]

    def test_missing_image_path_and_start_sec_raises(self) -> None:
        """Omitting image_path and start_sec must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(duration_sec=3.0)  # type: ignore[call-arg]


# ===========================================================================
# extra="forbid": unknown keys rejected
# ===========================================================================


class TestExtraForbid:
    """Unknown fields must be rejected (model_config extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """Passing an unknown keyword must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(  # type: ignore[call-arg]
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                unknown_field=123,
            )

    def test_multiple_unknown_fields_rejected(self) -> None:
        """Multiple unknown keywords must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(  # type: ignore[call-arg]
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                foo="bar",
                baz=42,
            )

    def test_typo_field_rejected(self) -> None:
        """A typo in a valid field name must be rejected as unknown."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(  # type: ignore[call-arg]
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                opacitty=0.5,
            )


# ===========================================================================
# allow_inf_nan=False: inf and nan rejected for float fields
# ===========================================================================


class TestInfNanRejected:
    """inf and nan must be rejected for float fields (model_config allow_inf_nan=False)."""

    def test_start_sec_inf_rejected(self) -> None:
        """start_sec=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png", start_sec=float("inf"), duration_sec=3.0
            )

    def test_start_sec_neg_inf_rejected(self) -> None:
        """start_sec=-inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png", start_sec=float("-inf"), duration_sec=3.0
            )

    def test_start_sec_nan_rejected(self) -> None:
        """start_sec=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png", start_sec=float("nan"), duration_sec=3.0
            )

    def test_duration_sec_inf_rejected(self) -> None:
        """duration_sec=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png", start_sec=1.0, duration_sec=float("inf")
            )

    def test_duration_sec_nan_rejected(self) -> None:
        """duration_sec=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png", start_sec=1.0, duration_sec=float("nan")
            )

    def test_scale_inf_rejected(self) -> None:
        """scale=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                scale=float("inf"),
            )

    def test_scale_nan_rejected(self) -> None:
        """scale=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                scale=float("nan"),
            )

    def test_opacity_inf_rejected(self) -> None:
        """opacity=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                opacity=math.inf,
            )

    def test_opacity_nan_rejected(self) -> None:
        """opacity=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                opacity=math.nan,
            )

    def test_fade_in_sec_inf_rejected(self) -> None:
        """fade_in_sec=inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                fade_in_sec=math.inf,
            )

    def test_fade_out_sec_nan_rejected(self) -> None:
        """fade_out_sec=nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                fade_out_sec=math.nan,
            )


# ===========================================================================
# Default values for all optional fields
# ===========================================================================


class TestDefaultValues:
    """All optional fields must have the specified defaults when not provided."""

    def _make_minimal(self) -> AddOverlayOptions:
        return AddOverlayOptions(image_path="logo.png", start_sec=1.0, duration_sec=3.0)

    def test_default_x(self) -> None:
        """x default must be '(W-w)/2' (horizontal center; note CAPITAL W/w for ffmpeg overlay)."""
        assert self._make_minimal().x == "(W-w)/2"

    def test_default_y(self) -> None:
        """y default must be '(H-h)/2' (vertical center; note CAPITAL H/h for ffmpeg overlay)."""
        assert self._make_minimal().y == "(H-h)/2"

    def test_default_scale(self) -> None:
        """scale default must be 1.0 (original size)."""
        assert self._make_minimal().scale == pytest.approx(1.0)

    def test_default_opacity(self) -> None:
        """opacity default must be 1.0 (fully opaque).

        Range enforcement (0..1) is validated manually in overlay.py, not at schema level.
        """
        assert self._make_minimal().opacity == pytest.approx(1.0)

    def test_default_fade_in_sec(self) -> None:
        """fade_in_sec default must be 0.3."""
        assert self._make_minimal().fade_in_sec == pytest.approx(0.3)

    def test_default_fade_out_sec(self) -> None:
        """fade_out_sec default must be 0.3."""
        assert self._make_minimal().fade_out_sec == pytest.approx(0.3)


# ===========================================================================
# scale Field(gt=0, le=8.0) per V2-9: schema layer rejects out-of-range values
# ===========================================================================


class TestScaleConstraint:
    """scale must satisfy gt=0 and le=8.0 at the schema level (V2-9).

    The schema layer is the first line of defence.  overlay.py also validates
    0 < scale <= 8.0 manually to emit a precise hint (OQ-1 pattern), but the
    schema must independently reject boundary violations.
    """

    def test_scale_zero_rejected(self) -> None:
        """scale=0 must raise ValidationError (gt=0 constraint from V2-9)."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                scale=0,
            )

    def test_scale_negative_rejected(self) -> None:
        """scale=-0.1 must raise ValidationError (gt=0 constraint)."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                scale=-0.1,
            )

    def test_scale_above_max_rejected(self) -> None:
        """scale=9.0 must raise ValidationError (le=8.0 constraint from V2-9)."""
        with pytest.raises(ValidationError):
            AddOverlayOptions(
                image_path="logo.png",
                start_sec=1.0,
                duration_sec=3.0,
                scale=9.0,
            )

    def test_scale_at_max_accepted(self) -> None:
        """scale=8.0 must be accepted (le=8.0 boundary is inclusive)."""
        opts = AddOverlayOptions(
            image_path="logo.png",
            start_sec=1.0,
            duration_sec=3.0,
            scale=8.0,
        )
        assert opts.scale == pytest.approx(8.0)

    def test_scale_half_accepted(self) -> None:
        """scale=0.5 must be accepted (valid shrink factor)."""
        opts = AddOverlayOptions(
            image_path="logo.png",
            start_sec=1.0,
            duration_sec=3.0,
            scale=0.5,
        )
        assert opts.scale == pytest.approx(0.5)

    def test_scale_default_accepted(self) -> None:
        """scale default (1.0) must be within valid range (gt=0, le=8.0)."""
        opts = AddOverlayOptions(image_path="logo.png", start_sec=1.0, duration_sec=3.0)
        assert opts.scale == pytest.approx(1.0)


# ===========================================================================
# Field presence in model_fields
# ===========================================================================


class TestFieldExistence:
    """All 9 schema fields must be present in model_fields."""

    _EXPECTED_FIELDS = {
        "image_path",
        "start_sec",
        "duration_sec",
        "x",
        "y",
        "scale",
        "opacity",
        "fade_in_sec",
        "fade_out_sec",
    }

    def test_all_expected_fields_exist(self) -> None:
        """model_fields must contain exactly the 9 specified fields."""
        actual = set(AddOverlayOptions.model_fields.keys())
        assert actual == self._EXPECTED_FIELDS

    def test_image_path_field_exists(self) -> None:
        """model_fields must contain 'image_path'."""
        assert "image_path" in AddOverlayOptions.model_fields

    def test_start_sec_field_exists(self) -> None:
        """model_fields must contain 'start_sec'."""
        assert "start_sec" in AddOverlayOptions.model_fields

    def test_duration_sec_field_exists(self) -> None:
        """model_fields must contain 'duration_sec'."""
        assert "duration_sec" in AddOverlayOptions.model_fields


# ===========================================================================
# No redefinition of core common types
# ===========================================================================


def test_add_overlay_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    import clipwright_overlay.schemas as overlay_schemas

    assert not hasattr(overlay_schemas, "MediaRef"), (
        "schemas.py redefines MediaRef from core"
    )
    assert not hasattr(overlay_schemas, "Artifact"), (
        "schemas.py redefines Artifact from core"
    )
    assert not hasattr(overlay_schemas, "ToolResult"), (
        "schemas.py redefines ToolResult from core"
    )


# ===========================================================================
# Field type checks
# ===========================================================================


class TestFieldTypes:
    """Field types must match the declared types."""

    def test_image_path_accepts_string(self) -> None:
        """image_path must accept a non-empty string."""
        opts = AddOverlayOptions(
            image_path="/path/to/logo.png", start_sec=1.0, duration_sec=3.0
        )
        assert opts.image_path == "/path/to/logo.png"

    def test_x_accepts_ffmpeg_center_expression(self) -> None:
        """x must accept a valid ffmpeg overlay expression with CAPITAL W."""
        opts = AddOverlayOptions(
            image_path="logo.png",
            start_sec=1.0,
            duration_sec=3.0,
            x="(W-w)/2",
        )
        assert opts.x == "(W-w)/2"

    def test_y_accepts_ffmpeg_center_expression(self) -> None:
        """y must accept a valid ffmpeg overlay expression with CAPITAL H."""
        opts = AddOverlayOptions(
            image_path="logo.png",
            start_sec=1.0,
            duration_sec=3.0,
            y="(H-h)/2",
        )
        assert opts.y == "(H-h)/2"
