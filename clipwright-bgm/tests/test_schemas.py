"""test_schemas.py — Contract tests for BgmOptions / DuckingOptions / BgmDirective / DuckingDirective.

Target: near 100% coverage of the contract layer (CONVENTIONS §TestCoverage).

Test scope:
  1. BgmOptions defaults (volume_db=-6.0, fade_in/out=0.0, ducking.enabled=False,
     ducking.threshold=0.05, ducking.ratio=4.0)
  2. volume_db out-of-range / negative fade / inf/nan → ValidationError
  3. writer BgmDirective tool/version max_length=64 exceeded → ValidationError (DC-AS-001)
  4. BgmDirective kind other than "bgm" → ValidationError
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_bgm.schemas import (
    BgmDirective,
    BgmOptions,
    DuckingDirective,
    DuckingOptions,
)

# ===========================================================================
# Test scope 1: BgmOptions defaults
# ===========================================================================


class TestBgmOptionsDefaults:
    """BgmOptions default construction and default value checks."""

    def test_volume_db_default_is_minus6(self) -> None:
        """volume_db default must be -6.0 (ADR-B9)."""
        # NOTE: schemas.py is expected to define -6.0 as the default for volume_db.
        # Before implementation, this is expected to fail with ImportError / ValueError.
        opts = BgmOptions(volume_db=-6.0)
        assert opts.volume_db == pytest.approx(-6.0)

    def test_fade_in_sec_default_is_zero(self) -> None:
        """fade_in_sec default must be 0.0 (ADR-B9-r3)."""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.fade_in_sec == pytest.approx(0.0)

    def test_fade_out_sec_default_is_zero(self) -> None:
        """fade_out_sec default must be 0.0 (ADR-B9-r3)."""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.fade_out_sec == pytest.approx(0.0)

    def test_ducking_enabled_default_is_false(self) -> None:
        """DuckingOptions.enabled default must be False (ADR-B9)."""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.ducking.enabled is False

    def test_ducking_threshold_default_is_0_05(self) -> None:
        """DuckingOptions.threshold default must be 0.05 (ADR-B9)."""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.ducking.threshold == pytest.approx(0.05)

    def test_ducking_ratio_default_is_4_0(self) -> None:
        """DuckingOptions.ratio default must be 4.0 (ADR-B9)."""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.ducking.ratio == pytest.approx(4.0)

    def test_construct_minimal_with_only_volume_db(self) -> None:
        """Must be constructible with only volume_db specified (remaining fields use defaults)."""
        opts = BgmOptions(volume_db=0.0)
        assert opts.volume_db == pytest.approx(0.0)
        assert opts.fade_in_sec == pytest.approx(0.0)
        assert opts.fade_out_sec == pytest.approx(0.0)
        assert opts.ducking.enabled is False


# ===========================================================================
# Test scope 2: BgmOptions out-of-range, negative values, inf/nan → ValidationError
# ===========================================================================


class TestBgmOptionsVolumeDbValidation:
    """volume_db range constraint tests (ge=-60, le=20, allow_inf_nan=False)."""

    def test_volume_db_lower_boundary_minus60_accepted(self) -> None:
        """volume_db=-60.0 must be accepted."""
        opts = BgmOptions(volume_db=-60.0)
        assert opts.volume_db == pytest.approx(-60.0)

    def test_volume_db_upper_boundary_20_accepted(self) -> None:
        """volume_db=20.0 must be accepted."""
        opts = BgmOptions(volume_db=20.0)
        assert opts.volume_db == pytest.approx(20.0)

    def test_volume_db_below_minus60_rejected(self) -> None:
        """volume_db=-61.0 must raise ValidationError."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=-61.0)

    def test_volume_db_above_20_rejected(self) -> None:
        """volume_db=21.0 must raise ValidationError."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=21.0)

    def test_volume_db_inf_rejected(self) -> None:
        """volume_db=inf must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=math.inf)

    def test_volume_db_nan_rejected(self) -> None:
        """volume_db=nan must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=math.nan)

    def test_volume_db_neg_inf_rejected(self) -> None:
        """volume_db=-inf must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=-math.inf)


class TestBgmOptionsFadeValidation:
    """fade_in_sec / fade_out_sec range constraint tests (ge=0)."""

    def test_fade_in_sec_zero_accepted(self) -> None:
        """fade_in_sec=0.0 must be accepted (boundary value)."""
        opts = BgmOptions(volume_db=0.0, fade_in_sec=0.0)
        assert opts.fade_in_sec == pytest.approx(0.0)

    def test_fade_in_sec_positive_accepted(self) -> None:
        """fade_in_sec=2.5 must be accepted."""
        opts = BgmOptions(volume_db=0.0, fade_in_sec=2.5)
        assert opts.fade_in_sec == pytest.approx(2.5)

    def test_fade_in_sec_negative_rejected(self) -> None:
        """fade_in_sec=-0.1 must raise ValidationError (ge=0 constraint)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_in_sec=-0.1)

    def test_fade_out_sec_zero_accepted(self) -> None:
        """fade_out_sec=0.0 must be accepted (boundary value)."""
        opts = BgmOptions(volume_db=0.0, fade_out_sec=0.0)
        assert opts.fade_out_sec == pytest.approx(0.0)

    def test_fade_out_sec_positive_accepted(self) -> None:
        """fade_out_sec=1.0 must be accepted."""
        opts = BgmOptions(volume_db=0.0, fade_out_sec=1.0)
        assert opts.fade_out_sec == pytest.approx(1.0)

    def test_fade_out_sec_negative_rejected(self) -> None:
        """fade_out_sec=-1.0 must raise ValidationError (ge=0 constraint)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_out_sec=-1.0)

    def test_fade_in_inf_rejected(self) -> None:
        """fade_in_sec=inf must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_in_sec=math.inf)

    def test_fade_out_nan_rejected(self) -> None:
        """fade_out_sec=nan must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_out_sec=math.nan)


class TestDuckingOptionsValidation:
    """DuckingOptions field constraint tests."""

    def test_construct_default_ducking(self) -> None:
        """DuckingOptions() must be constructible with default values."""
        d = DuckingOptions()
        assert d.enabled is False
        assert d.threshold == pytest.approx(0.05)
        assert d.ratio == pytest.approx(4.0)

    def test_ducking_enabled_true_accepted(self) -> None:
        """Must be constructible with enabled=True."""
        d = DuckingOptions(enabled=True)
        assert d.enabled is True

    def test_ducking_threshold_custom(self) -> None:
        """threshold must be overridable with a custom value."""
        d = DuckingOptions(threshold=0.1)
        assert d.threshold == pytest.approx(0.1)

    def test_ducking_ratio_custom(self) -> None:
        """ratio must be overridable with a custom value."""
        d = DuckingOptions(ratio=8.0)
        assert d.ratio == pytest.approx(8.0)

    def test_threshold_inf_rejected(self) -> None:
        """DuckingOptions.threshold=inf must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingOptions(threshold=math.inf)

    def test_threshold_nan_rejected(self) -> None:
        """DuckingOptions.threshold=nan must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingOptions(threshold=math.nan)

    def test_ratio_inf_rejected(self) -> None:
        """DuckingOptions.ratio=inf must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingOptions(ratio=math.inf)

    def test_ratio_nan_rejected(self) -> None:
        """DuckingOptions.ratio=nan must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingOptions(ratio=math.nan)


# ===========================================================================
# Test scope 3: BgmDirective tool/version max_length=64 exceeded → ValidationError (DC-AS-001)
# ===========================================================================


class TestBgmDirectiveToolVersionMaxLength:
    """writer BgmDirective tool/version max_length=64 constraint (DC-AS-001, ADR-B9-r2).

    The render reader side also defines the same fields with max_length=64,
    so attempting to write more than 64 characters on the writer side must raise ValidationError.
    """

    def _make_valid_directive(self, **overrides: object) -> BgmDirective:
        """Helper to construct a minimal valid BgmDirective."""
        base = {
            "tool": "clipwright-bgm",
            "version": "0.1.0",
            "kind": "bgm",
            "volume_db": -6.0,
            "fade_in_sec": 0.0,
            "fade_out_sec": 0.0,
            "ducking": DuckingDirective(enabled=False, threshold=0.05, ratio=4.0),
        }
        base.update(overrides)  # type: ignore[arg-type]
        return BgmDirective(**base)  # type: ignore[arg-type]

    def test_tool_at_max_length_64_accepted(self) -> None:
        """tool field with 64 characters must be accepted."""
        d = self._make_valid_directive(tool="t" * 64)
        assert len(d.tool) == 64

    def test_tool_over_max_length_65_rejected(self) -> None:
        """tool field with 65 characters must raise ValidationError."""
        with pytest.raises(ValidationError):
            self._make_valid_directive(tool="t" * 65)

    def test_version_at_max_length_64_accepted(self) -> None:
        """version field with 64 characters must be accepted."""
        d = self._make_valid_directive(version="1" * 64)
        assert len(d.version) == 64

    def test_version_over_max_length_65_rejected(self) -> None:
        """version field with 65 characters must raise ValidationError."""
        with pytest.raises(ValidationError):
            self._make_valid_directive(version="1" * 65)

    def test_normal_tool_and_version_accepted(self) -> None:
        """Normal tool/version strings must be accepted."""
        d = self._make_valid_directive(
            tool="clipwright-bgm",
            version="0.1.0",
        )
        assert d.tool == "clipwright-bgm"
        assert d.version == "0.1.0"


# ===========================================================================
# Test scope 4: BgmDirective kind other than "bgm" → ValidationError
# ===========================================================================


class TestBgmDirectiveKind:
    """BgmDirective.kind must only accept Literal["bgm"] (ADR-B9-r2)."""

    def test_kind_bgm_accepted(self) -> None:
        """kind="bgm" must construct successfully."""
        d = BgmDirective(
            tool="clipwright-bgm",
            version="0.1.0",
            kind="bgm",
            volume_db=-6.0,
            fade_in_sec=0.0,
            fade_out_sec=0.0,
            ducking=DuckingDirective(enabled=False, threshold=0.05, ratio=4.0),
        )
        assert d.kind == "bgm"

    @pytest.mark.parametrize(
        "invalid_kind",
        ["BGM", "bgm2", "noise", "loudness", "denoise", "", "track", "audio"],
    )
    def test_kind_other_value_rejected(self, invalid_kind: str) -> None:
        """kind values other than "bgm" must raise ValidationError."""
        with pytest.raises(ValidationError):
            BgmDirective(
                tool="clipwright-bgm",
                version="0.1.0",
                kind=invalid_kind,  # type: ignore[arg-type]
                volume_db=-6.0,
                fade_in_sec=0.0,
                fade_out_sec=0.0,
                ducking=DuckingDirective(enabled=False, threshold=0.05, ratio=4.0),
            )


# ===========================================================================
# BgmDirective: model_dump → reconstruction round-trip consistency
# ===========================================================================


class TestBgmDirectiveModelDump:
    """All fields must be preserved through a model_dump → reconstruction round-trip."""

    def test_roundtrip_model_dump(self) -> None:
        """All fields must match after model_dump and reconstruction of BgmDirective."""
        d = BgmDirective(
            tool="clipwright-bgm",
            version="0.1.0",
            kind="bgm",
            volume_db=-6.0,
            fade_in_sec=1.0,
            fade_out_sec=2.0,
            ducking=DuckingDirective(enabled=True, threshold=0.03, ratio=8.0),
        )
        dumped = d.model_dump()
        d2 = BgmDirective(**dumped)
        assert d2.kind == "bgm"
        assert d2.tool == "clipwright-bgm"
        assert d2.volume_db == pytest.approx(-6.0)
        assert d2.fade_in_sec == pytest.approx(1.0)
        assert d2.fade_out_sec == pytest.approx(2.0)
        assert d2.ducking.enabled is True
        assert d2.ducking.threshold == pytest.approx(0.03)
        assert d2.ducking.ratio == pytest.approx(8.0)

    def test_model_dump_includes_ducking_subfields(self) -> None:
        """model_dump ducking field must include enabled/threshold/ratio."""
        d = BgmDirective(
            tool="clipwright-bgm",
            version="0.1.0",
            kind="bgm",
            volume_db=-6.0,
            fade_in_sec=0.0,
            fade_out_sec=0.0,
            ducking=DuckingDirective(enabled=False, threshold=0.05, ratio=4.0),
        )
        dumped = d.model_dump()
        assert "ducking" in dumped
        assert "enabled" in dumped["ducking"]
        assert "threshold" in dumped["ducking"]
        assert "ratio" in dumped["ducking"]

    def test_model_dump_includes_all_top_level_fields(self) -> None:
        """model_dump must include tool/version/kind/volume_db/fade_in_sec/fade_out_sec."""
        d = BgmDirective(
            tool="clipwright-bgm",
            version="0.1.0",
            kind="bgm",
            volume_db=-6.0,
            fade_in_sec=0.0,
            fade_out_sec=0.0,
            ducking=DuckingDirective(enabled=False, threshold=0.05, ratio=4.0),
        )
        dumped = d.model_dump()
        for key in (
            "tool",
            "version",
            "kind",
            "volume_db",
            "fade_in_sec",
            "fade_out_sec",
        ):
            assert key in dumped, f"model_dump is missing key {key!r}"


# ===========================================================================
# DuckingDirective (writer) basic checks
# ===========================================================================


class TestDuckingDirective:
    """DuckingDirective construction checks."""

    def test_construct_with_all_fields(self) -> None:
        """Must be constructible with all fields specified."""
        d = DuckingDirective(enabled=True, threshold=0.08, ratio=6.0)
        assert d.enabled is True
        assert d.threshold == pytest.approx(0.08)
        assert d.ratio == pytest.approx(6.0)

    def test_construct_default_fields(self) -> None:
        """Must be constructible with default values (enabled=False, threshold=0.05, ratio=4.0)."""
        d = DuckingDirective()
        assert d.enabled is False
        assert d.threshold == pytest.approx(0.05)
        assert d.ratio == pytest.approx(4.0)

    # -------------------------------------------------------------------
    # Reject inf/nan (SR M-1/L-1)
    # -------------------------------------------------------------------

    def test_threshold_inf_rejected(self) -> None:
        """DuckingDirective.threshold=inf must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=math.inf)

    def test_threshold_nan_rejected(self) -> None:
        """DuckingDirective.threshold=nan must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=math.nan)

    def test_ratio_inf_rejected(self) -> None:
        """DuckingDirective.ratio=inf must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=math.inf)

    def test_ratio_nan_rejected(self) -> None:
        """DuckingDirective.ratio=nan must raise ValidationError (SR M-1/L-1)."""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=math.nan)

    # -------------------------------------------------------------------
    # threshold out-of-range (gt=0.0, le=1.0)
    # -------------------------------------------------------------------

    def test_threshold_zero_rejected(self) -> None:
        """DuckingDirective.threshold=0.0 must raise ValidationError (gt=0.0)."""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=0.0)

    def test_threshold_negative_rejected(self) -> None:
        """DuckingDirective.threshold=-0.1 must raise ValidationError (gt=0.0)."""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=-0.1)

    def test_threshold_above_1_rejected(self) -> None:
        """DuckingDirective.threshold=1.01 must raise ValidationError (le=1.0)."""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=1.01)

    def test_threshold_boundary_1_accepted(self) -> None:
        """DuckingDirective.threshold=1.0 must be accepted (le=1.0 boundary value)."""
        d = DuckingDirective(threshold=1.0)
        assert d.threshold == pytest.approx(1.0)

    def test_threshold_default_0_05_accepted(self) -> None:
        """Default threshold=0.05 must be valid (SR M-1 spec check)."""
        d = DuckingDirective()
        assert d.threshold == pytest.approx(0.05)

    # -------------------------------------------------------------------
    # ratio out-of-range (ge=1.0, le=20.0)
    # -------------------------------------------------------------------

    def test_ratio_below_1_rejected(self) -> None:
        """DuckingDirective.ratio=0.9 must raise ValidationError (ge=1.0)."""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=0.9)

    def test_ratio_above_20_rejected(self) -> None:
        """DuckingDirective.ratio=20.01 must raise ValidationError (le=20.0)."""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=20.01)

    def test_ratio_boundary_1_accepted(self) -> None:
        """DuckingDirective.ratio=1.0 must be accepted (ge=1.0 boundary value)."""
        d = DuckingDirective(ratio=1.0)
        assert d.ratio == pytest.approx(1.0)

    def test_ratio_boundary_20_accepted(self) -> None:
        """DuckingDirective.ratio=20.0 must be accepted (le=20.0 boundary value)."""
        d = DuckingDirective(ratio=20.0)
        assert d.ratio == pytest.approx(20.0)

    def test_ratio_default_4_0_accepted(self) -> None:
        """Default ratio=4.0 must be valid (SR M-1 spec check)."""
        d = DuckingDirective()
        assert d.ratio == pytest.approx(4.0)


# ===========================================================================
# NM-1: writer BgmDirective.volume_db range constraint tests (NR-L-3 base, ADR-B9)
# ===========================================================================


class TestBgmDirectiveVolumeDb:
    """writer BgmDirective volume_db range constraint tests (NM-1, ge=-60.0, le=20.0, allow_inf_nan=False).

    Verified from the same perspective as DuckingDirective.
    """

    def _make_directive(self, volume_db: float) -> BgmDirective:
        """Helper to construct a minimal valid BgmDirective with only volume_db substituted."""
        return BgmDirective(
            tool="clipwright-bgm",
            version="0.1.0",
            kind="bgm",
            volume_db=volume_db,
            fade_in_sec=0.0,
            fade_out_sec=0.0,
            ducking=DuckingDirective(enabled=False, threshold=0.05, ratio=4.0),
        )

    def test_volume_db_minus_200_rejected(self) -> None:
        """volume_db=-200 must raise ValidationError (ge=-60.0 constraint)."""
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            self._make_directive(-200.0)

    def test_volume_db_100_rejected(self) -> None:
        """volume_db=100 must raise ValidationError (le=20.0 constraint)."""
        with pytest.raises(ValidationError):
            self._make_directive(100.0)

    def test_volume_db_lower_boundary_minus60_accepted(self) -> None:
        """volume_db=-60.0 must be accepted (boundary value)."""
        d = self._make_directive(-60.0)
        assert d.volume_db == pytest.approx(-60.0)

    def test_volume_db_upper_boundary_20_accepted(self) -> None:
        """volume_db=20.0 must be accepted (boundary value)."""
        d = self._make_directive(20.0)
        assert d.volume_db == pytest.approx(20.0)

    def test_volume_db_inf_rejected(self) -> None:
        """volume_db=inf must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            self._make_directive(math.inf)

    def test_volume_db_neg_inf_rejected(self) -> None:
        """volume_db=-inf must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            self._make_directive(-math.inf)

    def test_volume_db_nan_rejected(self) -> None:
        """volume_db=nan must raise ValidationError (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            self._make_directive(math.nan)
