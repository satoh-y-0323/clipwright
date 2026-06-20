"""test_schemas_hw.py — Tests for RenderOptions HW encoder fields (FR-1/D1).

Tests the three hardware-acceleration fields on RenderOptions:
  - hw_encoder: Literal["none","auto","nvenc","amf","qsv","vaapi","videotoolbox"]
  - hwaccel_decode: bool
  - quality: int | None, ge=0, le=51
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_render.schemas import RenderOptions


# ===========================================================================
# hw_encoder — default and valid values
# ===========================================================================


class TestHwEncoderDefault:
    """Verify hw_encoder defaults to 'none' when not specified (FR-1)."""

    def test_hw_encoder_default_is_none(self) -> None:
        """RenderOptions() without hw_encoder must default to 'none'."""
        # Arrange / Act
        opts = RenderOptions()

        # Assert
        assert opts.hw_encoder == "none"  # type: ignore[attr-defined]


class TestHwEncoderValidValues:
    """Verify that all seven valid hw_encoder values are accepted (FR-1)."""

    @pytest.mark.parametrize(
        "encoder",
        ["none", "auto", "nvenc", "amf", "qsv", "vaapi", "videotoolbox"],
    )
    def test_valid_hw_encoder_accepted(self, encoder: str) -> None:
        """All Literal values for hw_encoder must be accepted."""
        # Arrange / Act
        opts = RenderOptions(hw_encoder=encoder)  # type: ignore[call-arg]

        # Assert
        assert opts.hw_encoder == encoder  # type: ignore[attr-defined]


class TestHwEncoderInvalidValues:
    """Verify that undefined hw_encoder values raise ValidationError (FR-1)."""

    @pytest.mark.parametrize(
        "invalid_encoder",
        [
            "foo",  # arbitrary unknown string
            "cuda",  # plausible but not in spec
            "NVENC",  # case-sensitive check
            "None",  # capitalised
            "",  # empty string
        ],
    )
    def test_invalid_hw_encoder_raises_validation_error(
        self, invalid_encoder: str
    ) -> None:
        """hw_encoder values outside the Literal set must raise ValidationError."""
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            RenderOptions(hw_encoder=invalid_encoder)  # type: ignore[call-arg]


# ===========================================================================
# hwaccel_decode — default and valid values
# ===========================================================================


class TestHwaccelDecodeDefault:
    """Verify hwaccel_decode defaults to False when not specified (FR-1)."""

    def test_hwaccel_decode_default_is_false(self) -> None:
        """RenderOptions() without hwaccel_decode must default to False."""
        # Arrange / Act
        opts = RenderOptions()

        # Assert
        assert opts.hwaccel_decode is False  # type: ignore[attr-defined]


class TestHwaccelDecodeValidValues:
    """Verify that True and False are accepted for hwaccel_decode (FR-1)."""

    @pytest.mark.parametrize("value", [True, False])
    def test_hwaccel_decode_bool_accepted(self, value: bool) -> None:
        """hwaccel_decode=True and hwaccel_decode=False must both be accepted."""
        # Arrange / Act
        opts = RenderOptions(hwaccel_decode=value)  # type: ignore[call-arg]

        # Assert
        assert opts.hwaccel_decode is value  # type: ignore[attr-defined]


# ===========================================================================
# quality — default, valid boundary values, and out-of-range rejection
# ===========================================================================


class TestQualityDefault:
    """Verify quality defaults to None when not specified (FR-1)."""

    def test_quality_default_is_none(self) -> None:
        """RenderOptions() without quality must default to None."""
        # Arrange / Act
        opts = RenderOptions()

        # Assert
        assert opts.quality is None  # type: ignore[attr-defined]


class TestQualityValidBoundaries:
    """Verify that quality boundary values 0 and 51 are accepted (FR-1)."""

    def test_quality_zero_accepted(self) -> None:
        """quality=0 (lower boundary, ge=0) must be accepted."""
        # Arrange / Act
        opts = RenderOptions(quality=0)  # type: ignore[call-arg]

        # Assert
        assert opts.quality == 0  # type: ignore[attr-defined]

    def test_quality_51_accepted(self) -> None:
        """quality=51 (upper boundary, le=51) must be accepted."""
        # Arrange / Act
        opts = RenderOptions(quality=51)  # type: ignore[call-arg]

        # Assert
        assert opts.quality == 51  # type: ignore[attr-defined]

    def test_quality_mid_range_accepted(self) -> None:
        """quality=23 (mid-range value) must be accepted."""
        # Arrange / Act
        opts = RenderOptions(quality=23)  # type: ignore[call-arg]

        # Assert
        assert opts.quality == 23  # type: ignore[attr-defined]

    def test_quality_none_accepted(self) -> None:
        """quality=None (explicit None) must be accepted."""
        # Arrange / Act
        opts = RenderOptions(quality=None)  # type: ignore[call-arg]

        # Assert
        assert opts.quality is None  # type: ignore[attr-defined]


class TestQualityOutOfRange:
    """Verify that quality values outside 0-51 raise ValidationError (FR-1)."""

    def test_quality_negative_one_raises_validation_error(self) -> None:
        """quality=-1 must raise ValidationError (violates ge=0)."""
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            RenderOptions(quality=-1)  # type: ignore[call-arg]

    def test_quality_52_raises_validation_error(self) -> None:
        """quality=52 must raise ValidationError (violates le=51)."""
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            RenderOptions(quality=52)  # type: ignore[call-arg]

    @pytest.mark.parametrize("out_of_range", [-100, -1, 52, 100])
    def test_quality_out_of_range_parametrized(self, out_of_range: int) -> None:
        """quality values outside 0-51 must raise ValidationError."""
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            RenderOptions(quality=out_of_range)  # type: ignore[call-arg]


# ===========================================================================
# extra="forbid" backward-compatibility — unknown fields still rejected
# ===========================================================================


class TestExtraForbidUnchanged:
    """Verify extra='forbid' is not broken by HW field addition (FR-1)."""

    def test_unknown_field_still_raises_validation_error(self) -> None:
        """RenderOptions with an unknown field must still raise ValidationError.

        The HW field addition must not change model_config extra='forbid'.
        """
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            RenderOptions(unknown_hw_field="evil")  # type: ignore[call-arg]

    def test_hw_fields_alongside_existing_fields(self) -> None:
        """All three HW fields can be set alongside existing RenderOptions fields."""
        # Arrange / Act
        opts = RenderOptions(  # type: ignore[call-arg]
            video_codec="libx264",
            hw_encoder="nvenc",
            hwaccel_decode=True,
            quality=28,
        )

        # Assert
        assert opts.video_codec == "libx264"
        assert opts.hw_encoder == "nvenc"  # type: ignore[attr-defined]
        assert opts.hwaccel_decode is True  # type: ignore[attr-defined]
        assert opts.quality == 28  # type: ignore[attr-defined]
