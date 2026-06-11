"""test_schemas.py — DetectLoudnessOptions / LoudnessDirective / Target / Measured の完全版テスト。

契約面（schemas）は実質 100% を目標にカバーする（CONVENTIONS §テストカバレッジ）。

検証観点:
  - DetectLoudnessOptions: mode∈{loudnorm,peak}・scope∈{track}のみ・target 上書き・
    既定 I=-14/TP=-1/LRA=11・不正値 ValidationError
  - LoudnessDirective: kind="loudness"・mode・scope="track"・
    target=LoudnormTarget|PeakTarget discriminate・
    measured=LoudnormMeasured|PeakMeasured|None・tool/version max_length=64・数値 inf/nan 拒否
  - LoudnormMeasured: input_i/input_tp/input_lra/input_thresh/target_offset 全 float・有限値のみ
  - PeakMeasured: max_volume_db 範囲 [-200..0]
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_loudness.schemas import (
    DetectLoudnessOptions,
    LoudnessDirective,
    LoudnormMeasured,
    LoudnormTarget,
    PeakMeasured,
    PeakTarget,
)

# ===========================================================================
# DetectLoudnessOptions
# ===========================================================================


class TestDetectLoudnessOptionsDefaults:
    """デフォルト構築と既定値の確認。"""

    def test_defaults_mode_is_loudnorm(self) -> None:
        opts = DetectLoudnessOptions()
        assert opts.mode == "loudnorm"

    def test_defaults_scope_is_track(self) -> None:
        opts = DetectLoudnessOptions()
        assert opts.scope == "track"

    def test_defaults_target_i_is_minus14(self) -> None:
        opts = DetectLoudnessOptions()
        assert opts.target_i == pytest.approx(-14.0)

    def test_defaults_target_tp_is_minus1(self) -> None:
        opts = DetectLoudnessOptions()
        assert opts.target_tp == pytest.approx(-1.0)

    def test_defaults_target_lra_is_11(self) -> None:
        opts = DetectLoudnessOptions()
        assert opts.target_lra == pytest.approx(11.0)

    def test_defaults_target_peak_db_is_minus1(self) -> None:
        """peak モードの既定 target_peak_db は -1.0。"""
        opts = DetectLoudnessOptions(mode="peak")
        assert opts.target_peak_db == pytest.approx(-1.0)

    def test_build_with_no_args(self) -> None:
        opts = DetectLoudnessOptions()
        assert opts.mode == "loudnorm"
        assert opts.scope == "track"


class TestDetectLoudnessOptionsMode:
    """mode フィールドの有効値・不正値テスト。"""

    def test_mode_loudnorm_accepted(self) -> None:
        opts = DetectLoudnessOptions(mode="loudnorm")
        assert opts.mode == "loudnorm"

    def test_mode_peak_accepted(self) -> None:
        opts = DetectLoudnessOptions(mode="peak")
        assert opts.mode == "peak"

    @pytest.mark.parametrize(
        "invalid",
        ["LOUDNORM", "PEAK", "ebur128", "volumedetect", "", "dynamic", "normalize"],
    )
    def test_invalid_mode_raises_validation_error(self, invalid: str) -> None:
        with pytest.raises(ValidationError):
            DetectLoudnessOptions(mode=invalid)  # type: ignore[arg-type]


class TestDetectLoudnessOptionsScope:
    """scope フィールドは track のみ（per_clip は今回スコープ外）。"""

    def test_scope_track_accepted(self) -> None:
        opts = DetectLoudnessOptions(scope="track")
        assert opts.scope == "track"

    @pytest.mark.parametrize(
        "invalid",
        ["per_clip", "clip", "TRACK", "all", ""],
    )
    def test_invalid_scope_raises_validation_error(self, invalid: str) -> None:
        with pytest.raises(ValidationError):
            DetectLoudnessOptions(scope=invalid)  # type: ignore[arg-type]

    def test_per_clip_scope_is_rejected(self) -> None:
        """per_clip は今回スコープ外（DC-AS-003）: ValidationError になること。"""
        with pytest.raises(ValidationError):
            DetectLoudnessOptions(scope="per_clip")  # type: ignore[arg-type]


class TestDetectLoudnessOptionsTargetOverride:
    """loudnorm target 上書きテスト。"""

    def test_target_i_override(self) -> None:
        opts = DetectLoudnessOptions(mode="loudnorm", target_i=-16.0)
        assert opts.target_i == pytest.approx(-16.0)

    def test_target_tp_override(self) -> None:
        opts = DetectLoudnessOptions(mode="loudnorm", target_tp=-2.0)
        assert opts.target_tp == pytest.approx(-2.0)

    def test_target_lra_override(self) -> None:
        opts = DetectLoudnessOptions(mode="loudnorm", target_lra=7.0)
        assert opts.target_lra == pytest.approx(7.0)

    def test_peak_target_peak_db_override(self) -> None:
        opts = DetectLoudnessOptions(mode="peak", target_peak_db=-3.0)
        assert opts.target_peak_db == pytest.approx(-3.0)


class TestDetectLoudnessOptionsCombinations:
    """有効な組み合わせを網羅する。"""

    @pytest.mark.parametrize("mode", ["loudnorm", "peak"])
    def test_all_valid_modes_with_track_scope_accepted(self, mode: str) -> None:
        opts = DetectLoudnessOptions(mode=mode, scope="track")
        assert opts.mode == mode
        assert opts.scope == "track"


# ===========================================================================
# LoudnormTarget
# ===========================================================================


class TestLoudnormTargetDefaults:
    """LoudnormTarget の既定値確認（I=-14/TP=-1/LRA=11）。"""

    def test_default_i_is_minus14(self) -> None:
        t = LoudnormTarget()
        assert t.i == pytest.approx(-14.0)

    def test_default_tp_is_minus1(self) -> None:
        t = LoudnormTarget()
        assert t.tp == pytest.approx(-1.0)

    def test_default_lra_is_11(self) -> None:
        t = LoudnormTarget()
        assert t.lra == pytest.approx(11.0)


class TestLoudnormTargetRanges:
    """LoudnormTarget の範囲制約テスト。"""

    def test_i_lower_boundary_minus70_accepted(self) -> None:
        t = LoudnormTarget(i=-70.0)
        assert t.i == pytest.approx(-70.0)

    def test_i_upper_boundary_minus5_accepted(self) -> None:
        t = LoudnormTarget(i=-5.0)
        assert t.i == pytest.approx(-5.0)

    def test_i_below_minus70_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormTarget(i=-71.0)

    def test_i_above_minus5_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormTarget(i=-4.0)

    def test_tp_lower_boundary_minus9_accepted(self) -> None:
        t = LoudnormTarget(tp=-9.0)
        assert t.tp == pytest.approx(-9.0)

    def test_tp_upper_boundary_0_accepted(self) -> None:
        t = LoudnormTarget(tp=0.0)
        assert t.tp == pytest.approx(0.0)

    def test_tp_below_minus9_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormTarget(tp=-10.0)

    def test_tp_above_0_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormTarget(tp=1.0)

    def test_lra_lower_boundary_1_accepted(self) -> None:
        t = LoudnormTarget(lra=1.0)
        assert t.lra == pytest.approx(1.0)

    def test_lra_upper_boundary_50_accepted(self) -> None:
        t = LoudnormTarget(lra=50.0)
        assert t.lra == pytest.approx(50.0)

    def test_lra_below_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormTarget(lra=0.5)

    def test_lra_above_50_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormTarget(lra=51.0)


# ===========================================================================
# PeakTarget
# ===========================================================================


class TestPeakTargetRanges:
    """PeakTarget の範囲制約テスト。"""

    def test_default_peak_db_minus1(self) -> None:
        t = PeakTarget()
        assert t.peak_db == pytest.approx(-1.0)

    def test_lower_boundary_minus60_accepted(self) -> None:
        t = PeakTarget(peak_db=-60.0)
        assert t.peak_db == pytest.approx(-60.0)

    def test_upper_boundary_0_accepted(self) -> None:
        t = PeakTarget(peak_db=0.0)
        assert t.peak_db == pytest.approx(0.0)

    def test_below_minus60_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakTarget(peak_db=-61.0)

    def test_above_0_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakTarget(peak_db=1.0)

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakTarget(peak_db=math.inf)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakTarget(peak_db=math.nan)


# ===========================================================================
# LoudnormMeasured
# ===========================================================================


class TestLoudnormMeasured:
    """LoudnormMeasured の全フィールド確認・有限値制約テスト。"""

    def test_construct_full_measured(self) -> None:
        m = LoudnormMeasured(
            input_i=-21.75,
            input_tp=-18.06,
            input_lra=0.0,
            input_thresh=-31.75,
            target_offset=0.03,
        )
        assert m.input_i == pytest.approx(-21.75)
        assert m.input_tp == pytest.approx(-18.06)
        assert m.input_lra == pytest.approx(0.0)
        assert m.input_thresh == pytest.approx(-31.75)
        assert m.target_offset == pytest.approx(0.03)

    def test_input_i_inf_rejected(self) -> None:
        """input_i = inf は拒否（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=math.inf,
                input_tp=-18.06,
                input_lra=0.0,
                input_thresh=-31.75,
                target_offset=0.03,
            )

    def test_input_i_neg_inf_rejected(self) -> None:
        """-inf は拒否（無音素材など）。"""
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=-math.inf,
                input_tp=-18.06,
                input_lra=0.0,
                input_thresh=-31.75,
                target_offset=0.03,
            )

    def test_input_i_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=math.nan,
                input_tp=-18.06,
                input_lra=0.0,
                input_thresh=-31.75,
                target_offset=0.03,
            )

    def test_input_tp_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=-21.75,
                input_tp=math.inf,
                input_lra=0.0,
                input_thresh=-31.75,
                target_offset=0.03,
            )

    def test_input_lra_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=-21.75,
                input_tp=-18.06,
                input_lra=math.inf,
                input_thresh=-31.75,
                target_offset=0.03,
            )

    def test_input_thresh_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=-21.75,
                input_tp=-18.06,
                input_lra=0.0,
                input_thresh=math.nan,
                target_offset=0.03,
            )

    def test_target_offset_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnormMeasured(
                input_i=-21.75,
                input_tp=-18.06,
                input_lra=0.0,
                input_thresh=-31.75,
                target_offset=math.inf,
            )


# ===========================================================================
# PeakMeasured
# ===========================================================================


class TestPeakMeasured:
    """PeakMeasured の範囲制約・inf/nan 拒否テスト。"""

    def test_construct_valid(self) -> None:
        m = PeakMeasured(max_volume_db=-18.1)
        assert m.max_volume_db == pytest.approx(-18.1)

    def test_lower_boundary_minus200_accepted(self) -> None:
        m = PeakMeasured(max_volume_db=-200.0)
        assert m.max_volume_db == pytest.approx(-200.0)

    def test_upper_boundary_0_accepted(self) -> None:
        m = PeakMeasured(max_volume_db=0.0)
        assert m.max_volume_db == pytest.approx(0.0)

    def test_below_minus200_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakMeasured(max_volume_db=-201.0)

    def test_above_0_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakMeasured(max_volume_db=1.0)

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakMeasured(max_volume_db=math.inf)

    def test_neg_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakMeasured(max_volume_db=-math.inf)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PeakMeasured(max_volume_db=math.nan)


# ===========================================================================
# LoudnessDirective
# ===========================================================================


class TestLoudnessDirectiveKind:
    """kind="loudness" の固定値確認。"""

    def test_kind_loudness_accepted(self) -> None:
        d = LoudnessDirective(
            tool="clipwright-loudness",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=None,
        )
        assert d.kind == "loudness"

    def test_kind_wrong_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="clipwright-loudness",
                version="0.1.0",
                kind="denoise",  # type: ignore[arg-type]
                mode="loudnorm",
                scope="track",
                target=LoudnormTarget(),
                measured=None,
            )

    def test_kind_noise_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="t",
                version="0.1.0",
                kind="noise",  # type: ignore[arg-type]
                mode="loudnorm",
                scope="track",
                target=LoudnormTarget(),
                measured=None,
            )


class TestLoudnessDirectiveScope:
    """scope="track" 固定の確認（per_clip は延期）。"""

    def test_scope_track_accepted(self) -> None:
        d = LoudnessDirective(
            tool="t",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=None,
        )
        assert d.scope == "track"

    def test_scope_per_clip_rejected(self) -> None:
        """per_clip は DC-AS-003 で延期: バリデーションエラーになること。"""
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="t",
                version="0.1.0",
                kind="loudness",
                mode="loudnorm",
                scope="per_clip",  # type: ignore[arg-type]
                target=LoudnormTarget(),
                measured=None,
            )


class TestLoudnessDirectiveTargetDiscriminate:
    """target が mode で discriminate されること（LoudnormTarget/PeakTarget）。"""

    def test_loudnorm_mode_with_loudnorm_target(self) -> None:
        d = LoudnessDirective(
            tool="t",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(i=-14.0, tp=-1.0, lra=11.0),
            measured=None,
        )
        assert isinstance(d.target, LoudnormTarget)
        assert d.target.i == pytest.approx(-14.0)

    def test_peak_mode_with_peak_target(self) -> None:
        d = LoudnessDirective(
            tool="t",
            version="0.1.0",
            kind="loudness",
            mode="peak",
            scope="track",
            target=PeakTarget(peak_db=-1.0),
            measured=None,
        )
        assert isinstance(d.target, PeakTarget)
        assert d.target.peak_db == pytest.approx(-1.0)


class TestLoudnessDirectiveMeasured:
    """measured フィールド: LoudnormMeasured / PeakMeasured / None 全パターン。"""

    def test_measured_none_accepted(self) -> None:
        d = LoudnessDirective(
            tool="t",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=None,
        )
        assert d.measured is None

    def test_loudnorm_measured_accepted(self) -> None:
        m = LoudnormMeasured(
            input_i=-21.75,
            input_tp=-18.06,
            input_lra=0.0,
            input_thresh=-31.75,
            target_offset=0.03,
        )
        d = LoudnessDirective(
            tool="t",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=m,
        )
        assert isinstance(d.measured, LoudnormMeasured)
        assert d.measured.input_i == pytest.approx(-21.75)

    def test_peak_measured_accepted(self) -> None:
        m = PeakMeasured(max_volume_db=-18.1)
        d = LoudnessDirective(
            tool="t",
            version="0.1.0",
            kind="loudness",
            mode="peak",
            scope="track",
            target=PeakTarget(),
            measured=m,
        )
        assert isinstance(d.measured, PeakMeasured)
        assert d.measured.max_volume_db == pytest.approx(-18.1)


class TestLoudnessDirectiveToolVersionMaxLength:
    """tool / version フィールドに max_length=64 制約があること。"""

    def test_tool_at_max_length_64_accepted(self) -> None:
        long_tool = "t" * 64
        d = LoudnessDirective(
            tool=long_tool,
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=None,
        )
        assert len(d.tool) == 64

    def test_tool_over_max_length_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="t" * 65,
                version="0.1.0",
                kind="loudness",
                mode="loudnorm",
                scope="track",
                target=LoudnormTarget(),
                measured=None,
            )

    def test_version_at_max_length_64_accepted(self) -> None:
        long_version = "1" * 64
        d = LoudnessDirective(
            tool="clipwright-loudness",
            version=long_version,
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=None,
        )
        assert len(d.version) == 64

    def test_version_over_max_length_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="clipwright-loudness",
                version="1" * 65,
                kind="loudness",
                mode="loudnorm",
                scope="track",
                target=LoudnormTarget(),
                measured=None,
            )


class TestLoudnessDirectiveCrossModeConsistency:
    """_validate_target_mode_consistency: mode と target 型の整合性検証。"""

    def test_loudnorm_mode_with_peak_target_raises_validation_error(self) -> None:
        """mode=loudnorm ＋ target=PeakTarget は ValidationError になること。"""
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="clipwright-loudness",
                version="0.1.0",
                kind="loudness",
                mode="loudnorm",
                scope="track",
                target=PeakTarget(peak_db=-1.0),
                measured=None,
            )

    def test_peak_mode_with_loudnorm_target_raises_validation_error(self) -> None:
        """mode=peak ＋ target=LoudnormTarget は ValidationError になること。"""
        with pytest.raises(ValidationError):
            LoudnessDirective(
                tool="clipwright-loudness",
                version="0.1.0",
                kind="loudness",
                mode="peak",
                scope="track",
                target=LoudnormTarget(),
                measured=None,
            )


class TestLoudnessDirectiveModelDump:
    """model_dump → 再構築の往復整合性。"""

    def test_roundtrip_loudnorm_with_measured(self) -> None:
        m = LoudnormMeasured(
            input_i=-21.75,
            input_tp=-18.06,
            input_lra=0.0,
            input_thresh=-31.75,
            target_offset=0.03,
        )
        d = LoudnessDirective(
            tool="clipwright-loudness",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=m,
        )
        dumped = d.model_dump()
        d2 = LoudnessDirective(**dumped)
        assert d2.kind == "loudness"
        assert d2.mode == "loudnorm"
        assert d2.scope == "track"
        assert isinstance(d2.measured, LoudnormMeasured)
        assert d2.measured.input_i == pytest.approx(-21.75)

    def test_roundtrip_peak_with_measured(self) -> None:
        m = PeakMeasured(max_volume_db=-18.1)
        d = LoudnessDirective(
            tool="clipwright-loudness",
            version="0.1.0",
            kind="loudness",
            mode="peak",
            scope="track",
            target=PeakTarget(peak_db=-1.0),
            measured=m,
        )
        dumped = d.model_dump()
        d2 = LoudnessDirective(**dumped)
        assert d2.mode == "peak"
        assert isinstance(d2.measured, PeakMeasured)
        assert d2.measured.max_volume_db == pytest.approx(-18.1)

    def test_model_dump_includes_all_fields(self) -> None:
        d = LoudnessDirective(
            tool="clipwright-loudness",
            version="0.1.0",
            kind="loudness",
            mode="loudnorm",
            scope="track",
            target=LoudnormTarget(),
            measured=None,
        )
        dumped = d.model_dump()
        assert "tool" in dumped
        assert "version" in dumped
        assert "kind" in dumped
        assert "mode" in dumped
        assert "scope" in dumped
        assert "target" in dumped
        assert "measured" in dumped
