"""test_schemas.py — Tests for clipwright_color.schemas.

Verification points:
  - DetectColorOptions: defaults (target_luma=128.0, sample_interval_sec=1.0),
    sample_interval_sec<=0 rejected, target_luma out of 0-255 rejected, extra forbidden.
  - EqParams: neutral defaults (brightness=0/contrast=1/saturation=1/gamma=1),
    brightness range [-1,1], contrast/saturation range [0,2], gamma range [0.1,10],
    inf/nan rejected, extra forbidden.
  - BrightnessMeasured: inf/nan and out-of-range values rejected.
  - ColorDirective: version/kind/eq required, extra forbidden.

Requirements: FR-3 (DetectColorOptions), architecture-report §3.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_color.schemas import (  # type: ignore[import-not-found]
    BrightnessMeasured,
    ColorDirective,
    DetectColorOptions,
    EqParams,
)

# ===========================================================================
# DetectColorOptions
# ===========================================================================


class TestDetectColorOptionsDefaults:
    """Verify default construction and default field values (FR-3)."""

    def test_default_target_luma_is_128(self) -> None:
        """target_luma default must be 128.0."""
        opts = DetectColorOptions()
        assert opts.target_luma == pytest.approx(128.0)

    def test_default_sample_interval_sec_is_1(self) -> None:
        """sample_interval_sec default must be 1.0."""
        opts = DetectColorOptions()
        assert opts.sample_interval_sec == pytest.approx(1.0)

    def test_build_with_no_args(self) -> None:
        """Construction with no arguments must succeed."""
        opts = DetectColorOptions()
        assert opts.target_luma == pytest.approx(128.0)
        assert opts.sample_interval_sec == pytest.approx(1.0)


class TestDetectColorOptionsSampleInterval:
    """Validate sample_interval_sec boundary conditions."""

    def test_sample_interval_positive_accepted(self) -> None:
        """Positive sample_interval_sec must be accepted."""
        opts = DetectColorOptions(sample_interval_sec=2.0)
        assert opts.sample_interval_sec == pytest.approx(2.0)

    def test_sample_interval_zero_rejected(self) -> None:
        """sample_interval_sec=0 must be rejected (must be > 0)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(sample_interval_sec=0.0)

    def test_sample_interval_negative_rejected(self) -> None:
        """Negative sample_interval_sec must be rejected."""
        with pytest.raises(ValidationError):
            DetectColorOptions(sample_interval_sec=-1.0)

    def test_sample_interval_very_small_positive_accepted(self) -> None:
        """Very small positive sample_interval_sec (e.g. 0.001) must be accepted."""
        opts = DetectColorOptions(sample_interval_sec=0.001)
        assert opts.sample_interval_sec == pytest.approx(0.001)


class TestDetectColorOptionsTargetLuma:
    """Validate target_luma boundary conditions."""

    def test_target_luma_zero_accepted(self) -> None:
        """target_luma=0.0 (minimum) must be accepted."""
        opts = DetectColorOptions(target_luma=0.0)
        assert opts.target_luma == pytest.approx(0.0)

    def test_target_luma_255_accepted(self) -> None:
        """target_luma=255.0 (maximum) must be accepted."""
        opts = DetectColorOptions(target_luma=255.0)
        assert opts.target_luma == pytest.approx(255.0)

    def test_target_luma_negative_rejected(self) -> None:
        """target_luma below 0 must be rejected."""
        with pytest.raises(ValidationError):
            DetectColorOptions(target_luma=-1.0)

    def test_target_luma_above_255_rejected(self) -> None:
        """target_luma above 255 must be rejected."""
        with pytest.raises(ValidationError):
            DetectColorOptions(target_luma=256.0)

    def test_target_luma_inf_rejected(self) -> None:
        """inf target_luma must be rejected."""
        with pytest.raises(ValidationError):
            DetectColorOptions(target_luma=math.inf)

    def test_target_luma_nan_rejected(self) -> None:
        """nan target_luma must be rejected."""
        with pytest.raises(ValidationError):
            DetectColorOptions(target_luma=math.nan)


class TestDetectColorOptionsExtraForbidden:
    """Extra fields must be forbidden (extra: forbid)."""

    def test_extra_field_rejected(self) -> None:
        """Unknown field must raise ValidationError."""
        with pytest.raises(ValidationError):
            DetectColorOptions(unknown_field=True)  # type: ignore[call-arg]


# ===========================================================================
# EqParams
# ===========================================================================


class TestEqParamsDefaults:
    """Neutral defaults for EqParams (architecture-report §3)."""

    def test_default_brightness_is_zero(self) -> None:
        """Default brightness must be 0.0 (neutral)."""
        eq = EqParams()
        assert eq.brightness == pytest.approx(0.0)

    def test_default_contrast_is_one(self) -> None:
        """Default contrast must be 1.0 (neutral)."""
        eq = EqParams()
        assert eq.contrast == pytest.approx(1.0)

    def test_default_saturation_is_one(self) -> None:
        """Default saturation must be 1.0 (neutral)."""
        eq = EqParams()
        assert eq.saturation == pytest.approx(1.0)

    def test_default_gamma_is_one(self) -> None:
        """Default gamma must be 1.0 (neutral)."""
        eq = EqParams()
        assert eq.gamma == pytest.approx(1.0)


class TestEqParamsBrightnessRange:
    """brightness must be in [-1.0, 1.0]."""

    def test_brightness_minus1_accepted(self) -> None:
        """brightness=-1.0 (lower bound) must be accepted."""
        eq = EqParams(brightness=-1.0)
        assert eq.brightness == pytest.approx(-1.0)

    def test_brightness_plus1_accepted(self) -> None:
        """brightness=1.0 (upper bound) must be accepted."""
        eq = EqParams(brightness=1.0)
        assert eq.brightness == pytest.approx(1.0)

    def test_brightness_below_minus1_rejected(self) -> None:
        """brightness below -1.0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(brightness=-1.1)

    def test_brightness_above_plus1_rejected(self) -> None:
        """brightness above 1.0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(brightness=1.1)

    def test_brightness_inf_rejected(self) -> None:
        """inf brightness must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            EqParams(brightness=math.inf)

    def test_brightness_nan_rejected(self) -> None:
        """nan brightness must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            EqParams(brightness=math.nan)


class TestEqParamsContrastRange:
    """contrast must be in [0.0, 2.0]."""

    def test_contrast_zero_accepted(self) -> None:
        """contrast=0.0 (lower bound) must be accepted."""
        eq = EqParams(contrast=0.0)
        assert eq.contrast == pytest.approx(0.0)

    def test_contrast_two_accepted(self) -> None:
        """contrast=2.0 (upper bound) must be accepted."""
        eq = EqParams(contrast=2.0)
        assert eq.contrast == pytest.approx(2.0)

    def test_contrast_negative_rejected(self) -> None:
        """contrast below 0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(contrast=-0.1)

    def test_contrast_above_two_rejected(self) -> None:
        """contrast above 2.0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(contrast=2.1)


class TestEqParamsSaturationRange:
    """saturation must be in [0.0, 2.0]."""

    def test_saturation_zero_accepted(self) -> None:
        """saturation=0.0 (lower bound) must be accepted."""
        eq = EqParams(saturation=0.0)
        assert eq.saturation == pytest.approx(0.0)

    def test_saturation_two_accepted(self) -> None:
        """saturation=2.0 (upper bound) must be accepted."""
        eq = EqParams(saturation=2.0)
        assert eq.saturation == pytest.approx(2.0)

    def test_saturation_negative_rejected(self) -> None:
        """saturation below 0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(saturation=-0.1)

    def test_saturation_above_two_rejected(self) -> None:
        """saturation above 2.0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(saturation=2.1)


class TestEqParamsGammaRange:
    """gamma must be in [0.1, 10.0]."""

    def test_gamma_point1_accepted(self) -> None:
        """gamma=0.1 (lower bound) must be accepted."""
        eq = EqParams(gamma=0.1)
        assert eq.gamma == pytest.approx(0.1)

    def test_gamma_ten_accepted(self) -> None:
        """gamma=10.0 (upper bound) must be accepted."""
        eq = EqParams(gamma=10.0)
        assert eq.gamma == pytest.approx(10.0)

    def test_gamma_zero_rejected(self) -> None:
        """gamma=0 must be rejected (lower bound is 0.1)."""
        with pytest.raises(ValidationError):
            EqParams(gamma=0.0)

    def test_gamma_above_ten_rejected(self) -> None:
        """gamma above 10.0 must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(gamma=10.1)

    def test_gamma_inf_rejected(self) -> None:
        """inf gamma must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(gamma=math.inf)

    def test_gamma_nan_rejected(self) -> None:
        """nan gamma must be rejected."""
        with pytest.raises(ValidationError):
            EqParams(gamma=math.nan)


class TestEqParamsExtraForbidden:
    """Extra fields must be forbidden."""

    def test_extra_field_rejected(self) -> None:
        """Unknown field must raise ValidationError."""
        with pytest.raises(ValidationError):
            EqParams(unknown=1.0)  # type: ignore[call-arg]


# ===========================================================================
# BrightnessMeasured
# ===========================================================================


class TestBrightnessMeasuredValidation:
    """BrightnessMeasured must reject inf/nan and out-of-range values."""

    def test_valid_measurement_accepted(self) -> None:
        """Normal in-range values must be accepted."""
        m = BrightnessMeasured(yavg=96.4, ymin=9.0, ymax=242.0, sampled_frames=12)
        assert m.yavg == pytest.approx(96.4)
        assert m.sampled_frames == 12

    def test_yavg_inf_rejected(self) -> None:
        """inf yavg must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=math.inf, sampled_frames=1)

    def test_yavg_nan_rejected(self) -> None:
        """nan yavg must be rejected."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=math.nan, sampled_frames=1)

    def test_yavg_above_255_rejected(self) -> None:
        """yavg above 255 must be rejected."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=256.0, sampled_frames=1)

    def test_yavg_negative_rejected(self) -> None:
        """yavg below 0 must be rejected."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=-1.0, sampled_frames=1)

    def test_ymin_ymax_optional(self) -> None:
        """ymin/ymax can be None (optional)."""
        m = BrightnessMeasured(yavg=100.0, sampled_frames=5)
        assert m.ymin is None
        assert m.ymax is None

    def test_sampled_frames_zero_accepted(self) -> None:
        """sampled_frames=0 must be accepted (ge=0)."""
        m = BrightnessMeasured(yavg=0.0, sampled_frames=0)
        assert m.sampled_frames == 0

    def test_extra_field_rejected(self) -> None:
        """Extra fields must be forbidden."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=100.0, sampled_frames=1, extra=True)  # type: ignore[call-arg]


# ===========================================================================
# ColorDirective
# ===========================================================================


class TestColorDirectiveRequired:
    """ColorDirective must require version, kind, eq (architecture-report §3)."""

    def test_valid_directive_constructed(self) -> None:
        """A fully specified ColorDirective must be accepted."""
        d = ColorDirective(
            version="0.1.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(brightness=0.1),
        )
        assert d.kind == "color"
        assert d.tool == "clipwright-color"

    def test_version_required(self) -> None:
        """Missing version must raise ValidationError."""
        with pytest.raises(ValidationError):
            ColorDirective(  # type: ignore[call-arg]
                kind="color",
                target_luma=128.0,
                eq=EqParams(),
            )

    def test_kind_required(self) -> None:
        """Missing kind must raise ValidationError."""
        with pytest.raises(ValidationError):
            ColorDirective(  # type: ignore[call-arg]
                version="0.1.0",
                target_luma=128.0,
                eq=EqParams(),
            )

    def test_eq_required(self) -> None:
        """Missing eq must raise ValidationError."""
        with pytest.raises(ValidationError):
            ColorDirective(  # type: ignore[call-arg]
                version="0.1.0",
                kind="color",
                target_luma=128.0,
            )

    def test_extra_field_rejected(self) -> None:
        """Extra fields must be forbidden (extra: forbid)."""
        with pytest.raises(ValidationError):
            ColorDirective(  # type: ignore[call-arg]
                version="0.1.0",
                kind="color",
                target_luma=128.0,
                eq=EqParams(),
                unknown_field="x",
            )

    def test_measured_optional_none_accepted(self) -> None:
        """measured=None must be accepted (U-1 parity)."""
        d = ColorDirective(
            version="0.1.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            measured=None,
        )
        assert d.measured is None

    def test_measured_with_brightness_accepted(self) -> None:
        """ColorDirective with a full BrightnessMeasured must be accepted."""
        m = BrightnessMeasured(yavg=96.4, ymin=9.0, ymax=242.0, sampled_frames=12)
        d = ColorDirective(
            version="0.1.0",
            kind="color",
            target_luma=128.0,
            measured=m,
            eq=EqParams(brightness=0.123),
        )
        assert d.measured is not None
        assert d.measured.yavg == pytest.approx(96.4)
