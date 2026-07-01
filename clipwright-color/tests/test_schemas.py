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


# ===========================================================================
# DetectColorOptions — new optional eq override fields (FR-1, FR-6, architecture-report §3.1)
# ===========================================================================


class TestDetectColorOptionsEqOverrides:
    """DetectColorOptions gains optional saturation/contrast/gamma eq override fields (FR-1).

    All new fields default to None; None means "leave the EqParams neutral default unchanged."
    Ranges mirror the EqParams constraints so callers cannot supply an invalid value.
    """

    def test_saturation_option_default_is_none(self) -> None:
        """saturation option field must default to None (FR-1 / backward-compat)."""
        opts = DetectColorOptions()
        assert opts.saturation is None

    def test_contrast_option_default_is_none(self) -> None:
        """contrast option field must default to None."""
        opts = DetectColorOptions()
        assert opts.contrast is None

    def test_gamma_option_default_is_none(self) -> None:
        """gamma option field must default to None."""
        opts = DetectColorOptions()
        assert opts.gamma is None

    def test_saturation_valid_midrange_accepted(self) -> None:
        """saturation=1.0 (mid-range) must be accepted without error."""
        opts = DetectColorOptions(saturation=1.0)
        assert opts.saturation == pytest.approx(1.0)

    def test_saturation_zero_accepted(self) -> None:
        """saturation=0.0 (ge=0.0 lower bound) must be accepted."""
        opts = DetectColorOptions(saturation=0.0)
        assert opts.saturation == pytest.approx(0.0)

    def test_saturation_two_accepted(self) -> None:
        """saturation=2.0 (le=2.0 upper bound) must be accepted."""
        opts = DetectColorOptions(saturation=2.0)
        assert opts.saturation == pytest.approx(2.0)

    def test_saturation_above_range_rejected(self) -> None:
        """saturation=2.5 must raise ValidationError (le=2.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(saturation=2.5)

    def test_saturation_negative_rejected(self) -> None:
        """saturation=-0.1 must raise ValidationError (ge=0.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(saturation=-0.1)

    def test_contrast_option_valid_accepted(self) -> None:
        """contrast option=1.0 must be accepted."""
        opts = DetectColorOptions(contrast=1.0)
        assert opts.contrast == pytest.approx(1.0)

    def test_contrast_option_above_range_rejected(self) -> None:
        """contrast option=2.5 must raise ValidationError (le=2.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(contrast=2.5)

    def test_contrast_option_negative_rejected(self) -> None:
        """contrast option=-0.1 must raise ValidationError (ge=0.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(contrast=-0.1)

    def test_gamma_option_valid_accepted(self) -> None:
        """gamma option=1.0 must be accepted."""
        opts = DetectColorOptions(gamma=1.0)
        assert opts.gamma == pytest.approx(1.0)

    def test_gamma_option_zero_rejected(self) -> None:
        """gamma option=0.0 must raise ValidationError (ge=0.1 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(gamma=0.0)

    def test_gamma_option_above_range_rejected(self) -> None:
        """gamma option=10.1 must raise ValidationError (le=10.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(gamma=10.1)


# ===========================================================================
# DetectColorOptions — new optional WB override fields (FR-3, FR-6, architecture-report §3.1)
# ===========================================================================


class TestDetectColorOptionsWbOverrides:
    """DetectColorOptions gains optional temperature/tint WB caller override fields (FR-3).

    None (default) means "use auto gray-world measurement result".
    Ranges are normalised [-1, 1] caller-facing axes mapped to per-channel gain [0, 4] (neutral 1.0) at render time (ADR-CO-7).
    """

    def test_temperature_default_is_none(self) -> None:
        """temperature field must default to None (use auto WB)."""
        opts = DetectColorOptions()
        assert opts.temperature is None

    def test_tint_default_is_none(self) -> None:
        """tint field must default to None (use auto WB)."""
        opts = DetectColorOptions()
        assert opts.tint is None

    def test_temperature_warm_accepted(self) -> None:
        """temperature=0.5 (warm bias) must be accepted."""
        opts = DetectColorOptions(temperature=0.5)
        assert opts.temperature == pytest.approx(0.5)

    def test_temperature_cool_accepted(self) -> None:
        """temperature=-0.5 (cool bias) must be accepted."""
        opts = DetectColorOptions(temperature=-0.5)
        assert opts.temperature == pytest.approx(-0.5)

    def test_temperature_boundary_plus1_accepted(self) -> None:
        """temperature=1.0 (le=1.0 upper bound) must be accepted."""
        opts = DetectColorOptions(temperature=1.0)
        assert opts.temperature == pytest.approx(1.0)

    def test_temperature_boundary_minus1_accepted(self) -> None:
        """temperature=-1.0 (ge=-1.0 lower bound) must be accepted."""
        opts = DetectColorOptions(temperature=-1.0)
        assert opts.temperature == pytest.approx(-1.0)

    def test_temperature_above_range_rejected(self) -> None:
        """temperature=1.5 must raise ValidationError (le=1.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(temperature=1.5)

    def test_temperature_below_range_rejected(self) -> None:
        """temperature=-1.5 must raise ValidationError (ge=-1.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(temperature=-1.5)

    def test_tint_magenta_accepted(self) -> None:
        """tint=0.3 (magenta bias) must be accepted."""
        opts = DetectColorOptions(tint=0.3)
        assert opts.tint == pytest.approx(0.3)

    def test_tint_above_range_rejected(self) -> None:
        """tint=1.5 must raise ValidationError (le=1.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(tint=1.5)

    def test_tint_below_range_rejected(self) -> None:
        """tint=-1.5 must raise ValidationError (ge=-1.0 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(tint=-1.5)


# ===========================================================================
# DetectColorOptions — new optional lut field (FR-5, FR-6, architecture-report §3.1)
# ===========================================================================


class TestDetectColorOptionsLutField:
    """DetectColorOptions gains optional lut field for a caller-provided .cube path (FR-5).

    The field stores the raw caller-provided path string; clipwright-color validates and
    resolves it via pathpolicy helpers during detect_color execution.
    """

    def test_lut_option_default_is_none(self) -> None:
        """lut option field must default to None (no LUT applied)."""
        opts = DetectColorOptions()
        assert opts.lut is None

    def test_lut_valid_path_accepted(self) -> None:
        """A valid .cube path string must be accepted."""
        opts = DetectColorOptions(lut="/path/to/grade.cube")
        assert opts.lut == "/path/to/grade.cube"

    def test_lut_min_length_one_accepted(self) -> None:
        """A single-char lut value (min_length=1) must be accepted."""
        opts = DetectColorOptions(lut="x")
        assert opts.lut == "x"

    def test_lut_max_length_4096_accepted(self) -> None:
        """A lut value exactly 4096 chars long (le=max_length) must be accepted."""
        opts = DetectColorOptions(lut="x" * 4096)
        assert opts.lut is not None and len(opts.lut) == 4096

    def test_lut_empty_string_rejected(self) -> None:
        """lut="" must raise ValidationError (min_length=1 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(lut="")

    def test_lut_above_max_length_rejected(self) -> None:
        """lut longer than 4096 chars must raise ValidationError (max_length=4096 violated)."""
        with pytest.raises(ValidationError):
            DetectColorOptions(lut="x" * 4097)

    def test_lut_none_explicit_accepted(self) -> None:
        """Explicit lut=None must be accepted (same as default)."""
        opts = DetectColorOptions(lut=None)
        assert opts.lut is None


# ===========================================================================
# BrightnessMeasured — new optional uavg / vavg chroma fields (FR-2, architecture-report §3.2)
# ===========================================================================


class TestBrightnessMeasuredChromaFields:
    """BrightnessMeasured gains optional uavg/vavg fields for median chroma measurement.

    Both fields are Optional (default None) so v0.2.x directives without them still validate.
    Range [0.0, 255.0] matches the 8-bit YUV per-channel scale used by signalstats.
    uavg/vavg are medians (not means) across sampled frames — consistent with ADR-CO-9.
    """

    def test_uavg_default_is_none(self) -> None:
        """uavg must default to None (absent in v0.2.x dicts — backward compat)."""
        m = BrightnessMeasured(yavg=128.0, sampled_frames=5)
        assert m.uavg is None

    def test_vavg_default_is_none(self) -> None:
        """vavg must default to None."""
        m = BrightnessMeasured(yavg=128.0, sampled_frames=5)
        assert m.vavg is None

    def test_uavg_neutral_value_accepted(self) -> None:
        """uavg=128.0 (neutral chroma) must be accepted."""
        m = BrightnessMeasured(yavg=128.0, uavg=128.0, sampled_frames=5)
        assert m.uavg == pytest.approx(128.0)

    def test_vavg_neutral_value_accepted(self) -> None:
        """vavg=128.0 must be accepted."""
        m = BrightnessMeasured(yavg=128.0, vavg=128.0, sampled_frames=5)
        assert m.vavg == pytest.approx(128.0)

    def test_uavg_zero_accepted(self) -> None:
        """uavg=0.0 (ge=0.0 lower bound) must be accepted."""
        m = BrightnessMeasured(yavg=128.0, uavg=0.0, sampled_frames=5)
        assert m.uavg == pytest.approx(0.0)

    def test_uavg_255_accepted(self) -> None:
        """uavg=255.0 (le=255.0 upper bound) must be accepted."""
        m = BrightnessMeasured(yavg=128.0, uavg=255.0, sampled_frames=5)
        assert m.uavg == pytest.approx(255.0)

    def test_uavg_above_255_rejected(self) -> None:
        """uavg=256.0 must raise ValidationError (le=255.0 violated)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=128.0, uavg=256.0, sampled_frames=5)

    def test_uavg_negative_rejected(self) -> None:
        """uavg=-1.0 must raise ValidationError (ge=0.0 violated)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=128.0, uavg=-1.0, sampled_frames=5)

    def test_vavg_above_255_rejected(self) -> None:
        """vavg=256.0 must raise ValidationError (le=255.0 violated)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=128.0, vavg=256.0, sampled_frames=5)

    def test_vavg_negative_rejected(self) -> None:
        """vavg=-1.0 must raise ValidationError (ge=0.0 violated)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=128.0, vavg=-1.0, sampled_frames=5)

    def test_uavg_inf_rejected(self) -> None:
        """inf uavg must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=128.0, uavg=math.inf, sampled_frames=5)

    def test_vavg_nan_rejected(self) -> None:
        """nan vavg must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BrightnessMeasured(yavg=128.0, vavg=math.nan, sampled_frames=5)

    def test_v02x_dict_without_chroma_parses_backward_compat(self) -> None:
        """A v0.2.x dict with no uavg/vavg keys must parse successfully (AC-1 backward compat).

        The parse must succeed and both chroma fields must be None after parsing.
        This is the critical backward-compatibility guard: existing stored directives
        must remain loadable after the schema extension.
        """
        v02x_dict = {"yavg": 96.4, "ymin": 9.0, "ymax": 242.0, "sampled_frames": 12}
        m = BrightnessMeasured.model_validate(v02x_dict)
        assert m.yavg == pytest.approx(96.4)
        assert m.uavg is None
        assert m.vavg is None


# ===========================================================================
# WhiteBalanceParams — new model (FR-6, D2, ADR-CO-7, architecture-report §3.4)
# ===========================================================================


class TestWhiteBalanceParams:
    """WhiteBalanceParams is a Pydantic model for colorchannelmixer per-channel gains.

    Maps 1:1 to ffmpeg colorchannelmixer rr/gg/bb diagonal gains (linear per-channel
    multipliers). Neutral = all 1.0 (identity gain). Range (0.0, 4.0] per channel
    (strictly positive multipliers; neutral = 1.0. Zero collapses a channel to black
    and negative gains are rejected).
    extra=forbid; allow_inf_nan=False.
    """

    def test_import_succeeds(self) -> None:
        """WhiteBalanceParams must be importable from clipwright_color.schemas."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

    def test_default_r_is_one(self) -> None:
        """r must default to 1.0 (neutral identity gain — no correction)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams()
        assert wb.r == pytest.approx(1.0)

    def test_default_g_is_one(self) -> None:
        """g must default to 1.0 (neutral identity gain)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams()
        assert wb.g == pytest.approx(1.0)

    def test_default_b_is_one(self) -> None:
        """b must default to 1.0 (neutral identity gain)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams()
        assert wb.b == pytest.approx(1.0)

    def test_all_neutral_defaults_together(self) -> None:
        """All three channels must default to 1.0 simultaneously (neutral = identity, no correction)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams()
        assert wb.r == pytest.approx(1.0)
        assert wb.g == pytest.approx(1.0)
        assert wb.b == pytest.approx(1.0)

    def test_half_gain_accepted(self) -> None:
        """r=0.5 (half gain, cuts red by 50%) must be accepted."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams(r=0.5)
        assert wb.r == pytest.approx(0.5)

    def test_double_gain_accepted(self) -> None:
        """r=2.0 (double gain, boosts red) must be accepted."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams(r=2.0)
        assert wb.r == pytest.approx(2.0)

    def test_gain_values_in_range_accepted(self) -> None:
        """r=1.4, g=1.0, b=0.6 (typical blue-cast correction) must be accepted."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams(r=1.4, g=1.0, b=0.6)
        assert wb.r == pytest.approx(1.4)
        assert wb.g == pytest.approx(1.0)
        assert wb.b == pytest.approx(0.6)

    def test_r_zero_rejected(self) -> None:
        """r=0.0 must raise ValidationError (gt=0.0: zero gain destroys the channel — SR M-1).

        gain=0 maps to colorchannelmixer rr=0 which multiplies all red output to black.
        The schema lower bound is gt=0.0 (exclusive) to prevent silent black-channel corruption.
        """
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(r=0.0)

    def test_g_zero_rejected(self) -> None:
        """g=0.0 must raise ValidationError (gt=0.0: zero gain destroys the channel — SR M-1)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(g=0.0)

    def test_b_zero_rejected(self) -> None:
        """b=0.0 must raise ValidationError (gt=0.0: zero gain destroys the channel — SR M-1)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(b=0.0)

    def test_boundary_four_accepted(self) -> None:
        """b=4.0 (le=4.0 upper bound) must be accepted."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams(b=4.0)
        assert wb.b == pytest.approx(4.0)

    def test_r_negative_rejected(self) -> None:
        """r=-0.1 must raise ValidationError (gt=0.0 violated — negative gain inverts channel)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(r=-0.1)

    def test_r_above_range_rejected(self) -> None:
        """r=4.1 must raise ValidationError (le=4.0 violated)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(r=4.1)

    def test_g_negative_rejected(self) -> None:
        """g=-0.1 must raise ValidationError (gt=0.0 violated)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(g=-0.1)

    def test_g_above_range_rejected(self) -> None:
        """g=4.1 must raise ValidationError (le=4.0 violated)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(g=4.1)

    def test_b_negative_rejected(self) -> None:
        """b=-0.1 must raise ValidationError (gt=0.0 violated)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(b=-0.1)

    def test_b_above_range_rejected(self) -> None:
        """b=4.1 must raise ValidationError (le=4.0 violated)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(b=4.1)

    def test_extra_field_rejected(self) -> None:
        """Unknown field must raise ValidationError (extra=forbid)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(unknown=0.5)  # type: ignore[call-arg]

    def test_inf_rejected(self) -> None:
        """inf r must be rejected (allow_inf_nan=False)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(r=math.inf)

    def test_nan_rejected(self) -> None:
        """nan r must be rejected (allow_inf_nan=False)."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        with pytest.raises(ValidationError):
            WhiteBalanceParams(r=math.nan)


# ===========================================================================
# ColorDirective — new optional white_balance and lut fields (FR-6, architecture-report §3.4)
# ===========================================================================


class TestColorDirectiveNewFields:
    """ColorDirective gains white_balance (WhiteBalanceParams|None=None) and lut (str|None=None).

    Both fields are Optional with None default for strict backward compatibility with v0.2.x
    directives (AC-1 / AC-8 contract side). Absence of both fields is a no-op in render.
    """

    def test_white_balance_field_default_is_none(self) -> None:
        """white_balance must default to None when not supplied."""
        d = ColorDirective(
            version="0.3.0", kind="color", target_luma=128.0, eq=EqParams()
        )
        assert d.white_balance is None

    def test_lut_field_default_is_none(self) -> None:
        """lut field must default to None when not supplied."""
        d = ColorDirective(
            version="0.3.0", kind="color", target_luma=128.0, eq=EqParams()
        )
        assert d.lut is None

    def test_v02x_directive_without_wb_and_lut_parses(self) -> None:
        """A v0.2.x ColorDirective JSON with no white_balance and no lut parses without error.

        This is the AC-1 / AC-8 backward-compatibility contract side test.
        The directive written by clipwright-color v0.2.x must load cleanly in v0.3.0.
        """
        v02x_dict = {
            "version": "0.2.1",
            "kind": "color",
            "target_luma": 128.0,
            "measured": None,
            "eq": {
                "brightness": 0.05,
                "contrast": 1.0,
                "saturation": 1.0,
                "gamma": 1.0,
            },
        }
        d = ColorDirective.model_validate(v02x_dict)
        assert d.white_balance is None
        assert d.lut is None

    def test_white_balance_none_explicit_accepted(self) -> None:
        """Explicit white_balance=None must be accepted (same as absent)."""
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            white_balance=None,
        )
        assert d.white_balance is None

    def test_white_balance_params_accepted(self) -> None:
        """ColorDirective with a WhiteBalanceParams gain value must be accepted."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams(r=1.4, g=1.0, b=0.7)
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            white_balance=wb,
        )
        assert d.white_balance is not None
        assert d.white_balance.r == pytest.approx(1.4)

    def test_lut_path_accepted(self) -> None:
        """ColorDirective with a lut path string must be accepted."""
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            lut="/resolved/path/to/grade.cube",
        )
        assert d.lut == "/resolved/path/to/grade.cube"

    def test_lut_none_explicit_accepted(self) -> None:
        """Explicit lut=None must be accepted (no LUT applied in render)."""
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            lut=None,
        )
        assert d.lut is None

    def test_lut_max_length_4096_accepted(self) -> None:
        """lut exactly 4096 chars must be accepted (max_length=4096 boundary)."""
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            lut="x" * 4096,
        )
        assert d.lut is not None and len(d.lut) == 4096

    def test_lut_above_max_length_rejected(self) -> None:
        """lut longer than 4096 chars must raise ValidationError (max_length=4096 violated)."""
        with pytest.raises(ValidationError):
            ColorDirective(
                version="0.3.0",
                kind="color",
                target_luma=128.0,
                eq=EqParams(),
                lut="x" * 4097,
            )

    def test_full_v03x_directive_with_all_new_fields(self) -> None:
        """A ColorDirective with all new fields populated must be accepted."""
        from clipwright_color.schemas import WhiteBalanceParams  # noqa: F401

        wb = WhiteBalanceParams(r=1.4, g=1.0, b=0.7)
        measured = BrightnessMeasured(
            yavg=110.0, sampled_frames=12, uavg=132.0, vavg=124.0
        )
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            measured=measured,
            eq=EqParams(brightness=0.07, saturation=1.1),
            white_balance=wb,
            lut="/media/luts/filmic.cube",
        )
        assert d.white_balance is not None
        assert d.white_balance.r == pytest.approx(1.4)
        assert d.measured is not None
        assert d.measured.uavg == pytest.approx(132.0)
        assert d.lut == "/media/luts/filmic.cube"


# ===========================================================================
# SR-V-001: ColorDirective.lut min_length=1
# ===========================================================================


class TestColorDirectiveLutMinLength:
    """SR-V-001: ColorDirective.lut must have min_length=1 (parity with DetectColorOptions.lut).

    DetectColorOptions.lut already enforces min_length=1.  ColorDirective.lut
    also has min_length=1 — an empty string is meaningless and opens a CWE-20 injection path.
    """

    def test_color_directive_lut_empty_string_rejected(self) -> None:
        """ColorDirective with lut='' must raise ValidationError (min_length=1 absent on current schema)."""
        with pytest.raises(ValidationError):
            ColorDirective(
                version="0.3.0",
                kind="color",
                target_luma=128.0,
                eq=EqParams(),
                lut="",
            )

    def test_color_directive_lut_nonempty_accepted(self) -> None:
        """A non-empty lut value must continue to be accepted (regression guard)."""
        d = ColorDirective(
            version="0.3.0",
            kind="color",
            target_luma=128.0,
            eq=EqParams(),
            lut="grade.cube",
        )
        assert d.lut == "grade.cube"
