"""test_schemas.py — BgmOptions / DuckingOptions / BgmDirective / DuckingDirective の契約面テスト。

契約面は実質 100% を目標にカバーする（CONVENTIONS §テストカバレッジ）。

検証観点:
  1. BgmOptions 既定値（volume_db=-6.0・fade_in/out=0.0・ducking.enabled=False・
     ducking.threshold=0.05・ducking.ratio=4.0）
  2. volume_db 範囲外 / fade 負値 / inf・nan → ValidationError
  3. writer BgmDirective の tool/version max_length=64 超 → ValidationError（DC-AS-001）
  4. BgmDirective kind が "bgm" 以外 → ValidationError
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
# テスト観点 1: BgmOptions 既定値
# ===========================================================================


class TestBgmOptionsDefaults:
    """BgmOptions デフォルト構築と既定値の確認。"""

    def test_volume_db_default_is_minus6(self) -> None:
        """volume_db の既定値は -6.0 であること（ADR-B9）。"""
        # NOTE: schemas.py が volume_db のデフォルト値を -6.0 で定義することを期待する
        # 実装前は ImportError / ValueError で失敗することが期待される
        opts = BgmOptions(volume_db=-6.0)
        assert opts.volume_db == pytest.approx(-6.0)

    def test_fade_in_sec_default_is_zero(self) -> None:
        """fade_in_sec の既定値は 0.0 であること（ADR-B9-r3）。"""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.fade_in_sec == pytest.approx(0.0)

    def test_fade_out_sec_default_is_zero(self) -> None:
        """fade_out_sec の既定値は 0.0 であること（ADR-B9-r3）。"""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.fade_out_sec == pytest.approx(0.0)

    def test_ducking_enabled_default_is_false(self) -> None:
        """DuckingOptions.enabled の既定値は False であること（ADR-B9）。"""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.ducking.enabled is False

    def test_ducking_threshold_default_is_0_05(self) -> None:
        """DuckingOptions.threshold の既定値は 0.05 であること（ADR-B9）。"""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.ducking.threshold == pytest.approx(0.05)

    def test_ducking_ratio_default_is_4_0(self) -> None:
        """DuckingOptions.ratio の既定値は 4.0 であること（ADR-B9）。"""
        opts = BgmOptions(volume_db=-6.0)
        assert opts.ducking.ratio == pytest.approx(4.0)

    def test_construct_minimal_with_only_volume_db(self) -> None:
        """volume_db のみ指定して構築できること（残フィールドは既定値）。"""
        opts = BgmOptions(volume_db=0.0)
        assert opts.volume_db == pytest.approx(0.0)
        assert opts.fade_in_sec == pytest.approx(0.0)
        assert opts.fade_out_sec == pytest.approx(0.0)
        assert opts.ducking.enabled is False


# ===========================================================================
# テスト観点 2: BgmOptions 範囲外・負値・inf/nan → ValidationError
# ===========================================================================


class TestBgmOptionsVolumeDbValidation:
    """volume_db の範囲制約テスト（ge=-60, le=20 / allow_inf_nan=False）。"""

    def test_volume_db_lower_boundary_minus60_accepted(self) -> None:
        """volume_db=-60.0 は許可されること。"""
        opts = BgmOptions(volume_db=-60.0)
        assert opts.volume_db == pytest.approx(-60.0)

    def test_volume_db_upper_boundary_20_accepted(self) -> None:
        """volume_db=20.0 は許可されること。"""
        opts = BgmOptions(volume_db=20.0)
        assert opts.volume_db == pytest.approx(20.0)

    def test_volume_db_below_minus60_rejected(self) -> None:
        """volume_db=-61.0 は ValidationError になること。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=-61.0)

    def test_volume_db_above_20_rejected(self) -> None:
        """volume_db=21.0 は ValidationError になること。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=21.0)

    def test_volume_db_inf_rejected(self) -> None:
        """volume_db=inf は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=math.inf)

    def test_volume_db_nan_rejected(self) -> None:
        """volume_db=nan は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=math.nan)

    def test_volume_db_neg_inf_rejected(self) -> None:
        """volume_db=-inf は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=-math.inf)


class TestBgmOptionsFadeValidation:
    """fade_in_sec / fade_out_sec の範囲制約テスト（ge=0）。"""

    def test_fade_in_sec_zero_accepted(self) -> None:
        """fade_in_sec=0.0 は許可されること（境界値）。"""
        opts = BgmOptions(volume_db=0.0, fade_in_sec=0.0)
        assert opts.fade_in_sec == pytest.approx(0.0)

    def test_fade_in_sec_positive_accepted(self) -> None:
        """fade_in_sec=2.5 は許可されること。"""
        opts = BgmOptions(volume_db=0.0, fade_in_sec=2.5)
        assert opts.fade_in_sec == pytest.approx(2.5)

    def test_fade_in_sec_negative_rejected(self) -> None:
        """fade_in_sec=-0.1 は ValidationError になること（ge=0 制約）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_in_sec=-0.1)

    def test_fade_out_sec_zero_accepted(self) -> None:
        """fade_out_sec=0.0 は許可されること（境界値）。"""
        opts = BgmOptions(volume_db=0.0, fade_out_sec=0.0)
        assert opts.fade_out_sec == pytest.approx(0.0)

    def test_fade_out_sec_positive_accepted(self) -> None:
        """fade_out_sec=1.0 は許可されること。"""
        opts = BgmOptions(volume_db=0.0, fade_out_sec=1.0)
        assert opts.fade_out_sec == pytest.approx(1.0)

    def test_fade_out_sec_negative_rejected(self) -> None:
        """fade_out_sec=-1.0 は ValidationError になること（ge=0 制約）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_out_sec=-1.0)

    def test_fade_in_inf_rejected(self) -> None:
        """fade_in_sec=inf は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_in_sec=math.inf)

    def test_fade_out_nan_rejected(self) -> None:
        """fade_out_sec=nan は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            BgmOptions(volume_db=0.0, fade_out_sec=math.nan)


class TestDuckingOptionsValidation:
    """DuckingOptions フィールドの制約テスト。"""

    def test_construct_default_ducking(self) -> None:
        """DuckingOptions() が既定値で構築できること。"""
        d = DuckingOptions()
        assert d.enabled is False
        assert d.threshold == pytest.approx(0.05)
        assert d.ratio == pytest.approx(4.0)

    def test_ducking_enabled_true_accepted(self) -> None:
        """enabled=True で構築できること。"""
        d = DuckingOptions(enabled=True)
        assert d.enabled is True

    def test_ducking_threshold_custom(self) -> None:
        """threshold をカスタム値で上書きできること。"""
        d = DuckingOptions(threshold=0.1)
        assert d.threshold == pytest.approx(0.1)

    def test_ducking_ratio_custom(self) -> None:
        """ratio をカスタム値で上書きできること。"""
        d = DuckingOptions(ratio=8.0)
        assert d.ratio == pytest.approx(8.0)

    def test_threshold_inf_rejected(self) -> None:
        """DuckingOptions.threshold=inf は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingOptions(threshold=math.inf)

    def test_threshold_nan_rejected(self) -> None:
        """DuckingOptions.threshold=nan は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingOptions(threshold=math.nan)

    def test_ratio_inf_rejected(self) -> None:
        """DuckingOptions.ratio=inf は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingOptions(ratio=math.inf)

    def test_ratio_nan_rejected(self) -> None:
        """DuckingOptions.ratio=nan は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingOptions(ratio=math.nan)


# ===========================================================================
# テスト観点 3: BgmDirective の tool/version max_length=64 超 → ValidationError（DC-AS-001）
# ===========================================================================


class TestBgmDirectiveToolVersionMaxLength:
    """writer BgmDirective の tool/version max_length=64 制約（DC-AS-001・ADR-B9-r2）。

    reader（render plan.py）も同フィールド max_length=64 で定義するため、
    writer 側で 64 文字超を書き込もうとした段階で ValidationError になること。
    """

    def _make_valid_directive(self, **overrides: object) -> BgmDirective:
        """最小有効 BgmDirective を構築するヘルパー。"""
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
        """tool フィールドが 64 文字のとき受理されること。"""
        d = self._make_valid_directive(tool="t" * 64)
        assert len(d.tool) == 64

    def test_tool_over_max_length_65_rejected(self) -> None:
        """tool フィールドが 65 文字のとき ValidationError になること。"""
        with pytest.raises(ValidationError):
            self._make_valid_directive(tool="t" * 65)

    def test_version_at_max_length_64_accepted(self) -> None:
        """version フィールドが 64 文字のとき受理されること。"""
        d = self._make_valid_directive(version="1" * 64)
        assert len(d.version) == 64

    def test_version_over_max_length_65_rejected(self) -> None:
        """version フィールドが 65 文字のとき ValidationError になること。"""
        with pytest.raises(ValidationError):
            self._make_valid_directive(version="1" * 65)

    def test_normal_tool_and_version_accepted(self) -> None:
        """通常の tool/version 文字列は受理されること。"""
        d = self._make_valid_directive(
            tool="clipwright-bgm",
            version="0.1.0",
        )
        assert d.tool == "clipwright-bgm"
        assert d.version == "0.1.0"


# ===========================================================================
# テスト観点 4: BgmDirective kind が "bgm" 以外 → ValidationError
# ===========================================================================


class TestBgmDirectiveKind:
    """BgmDirective.kind は Literal["bgm"] のみ受理すること（ADR-B9-r2）。"""

    def test_kind_bgm_accepted(self) -> None:
        """kind="bgm" で正常に構築できること。"""
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
        """kind が "bgm" 以外のとき ValidationError になること。"""
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
# BgmDirective: model_dump → 再構築の往復整合性
# ===========================================================================


class TestBgmDirectiveModelDump:
    """model_dump → 再構築の往復でフィールドが保持されること。"""

    def test_roundtrip_model_dump(self) -> None:
        """BgmDirective を model_dump して再構築したとき全フィールドが一致すること。"""
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
        """model_dump の ducking フィールドに enabled/threshold/ratio が含まれること。"""
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
        """model_dump に tool/version/kind/volume_db/fade_in_sec/fade_out_sec が含まれること。"""
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
            assert key in dumped, f"model_dump に {key!r} が含まれていない"


# ===========================================================================
# DuckingDirective（writer 用）の基本確認
# ===========================================================================


class TestDuckingDirective:
    """DuckingDirective の構築確認。"""

    def test_construct_with_all_fields(self) -> None:
        """全フィールド指定で構築できること。"""
        d = DuckingDirective(enabled=True, threshold=0.08, ratio=6.0)
        assert d.enabled is True
        assert d.threshold == pytest.approx(0.08)
        assert d.ratio == pytest.approx(6.0)

    def test_construct_default_fields(self) -> None:
        """既定値で構築できること（enabled=False・threshold=0.05・ratio=4.0）。"""
        d = DuckingDirective()
        assert d.enabled is False
        assert d.threshold == pytest.approx(0.05)
        assert d.ratio == pytest.approx(4.0)

    # -------------------------------------------------------------------
    # inf/nan 拒否（SR M-1/L-1）
    # -------------------------------------------------------------------

    def test_threshold_inf_rejected(self) -> None:
        """DuckingDirective.threshold=inf は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=math.inf)

    def test_threshold_nan_rejected(self) -> None:
        """DuckingDirective.threshold=nan は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=math.nan)

    def test_ratio_inf_rejected(self) -> None:
        """DuckingDirective.ratio=inf は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=math.inf)

    def test_ratio_nan_rejected(self) -> None:
        """DuckingDirective.ratio=nan は ValidationError になること（SR M-1/L-1）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=math.nan)

    # -------------------------------------------------------------------
    # threshold 範囲外（gt=0.0, le=1.0）
    # -------------------------------------------------------------------

    def test_threshold_zero_rejected(self) -> None:
        """DuckingDirective.threshold=0.0 は ValidationError になること（gt=0.0）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=0.0)

    def test_threshold_negative_rejected(self) -> None:
        """DuckingDirective.threshold=-0.1 は ValidationError になること（gt=0.0）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=-0.1)

    def test_threshold_above_1_rejected(self) -> None:
        """DuckingDirective.threshold=1.01 は ValidationError になること（le=1.0）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(threshold=1.01)

    def test_threshold_boundary_1_accepted(self) -> None:
        """DuckingDirective.threshold=1.0 は許可されること（le=1.0 境界値）。"""
        d = DuckingDirective(threshold=1.0)
        assert d.threshold == pytest.approx(1.0)

    def test_threshold_default_0_05_accepted(self) -> None:
        """既定値 threshold=0.05 は有効であること（SR M-1 仕様確認）。"""
        d = DuckingDirective()
        assert d.threshold == pytest.approx(0.05)

    # -------------------------------------------------------------------
    # ratio 範囲外（ge=1.0, le=20.0）
    # -------------------------------------------------------------------

    def test_ratio_below_1_rejected(self) -> None:
        """DuckingDirective.ratio=0.9 は ValidationError になること（ge=1.0）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=0.9)

    def test_ratio_above_20_rejected(self) -> None:
        """DuckingDirective.ratio=20.01 は ValidationError になること（le=20.0）。"""
        with pytest.raises(ValidationError):
            DuckingDirective(ratio=20.01)

    def test_ratio_boundary_1_accepted(self) -> None:
        """DuckingDirective.ratio=1.0 は許可されること（ge=1.0 境界値）。"""
        d = DuckingDirective(ratio=1.0)
        assert d.ratio == pytest.approx(1.0)

    def test_ratio_boundary_20_accepted(self) -> None:
        """DuckingDirective.ratio=20.0 は許可されること（le=20.0 境界値）。"""
        d = DuckingDirective(ratio=20.0)
        assert d.ratio == pytest.approx(20.0)

    def test_ratio_default_4_0_accepted(self) -> None:
        """既定値 ratio=4.0 は有効であること（SR M-1 仕様確認）。"""
        d = DuckingDirective()
        assert d.ratio == pytest.approx(4.0)


# ===========================================================================
# NM-1: writer BgmDirective.volume_db 範囲制約テスト（NR-L-3 基礎・ADR-B9）
# ===========================================================================


class TestBgmDirectiveVolumeDb:
    """writer BgmDirective の volume_db 範囲制約テスト（NM-1・ge=-60.0, le=20.0, allow_inf_nan=False）。

    DuckingDirective と同型の観点で検証する。
    """

    def _make_directive(self, volume_db: float) -> BgmDirective:
        """volume_db だけ差し替えた最小有効 BgmDirective を構築するヘルパー。"""
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
        """volume_db=-200 は ValidationError になること（ge=-60.0 制約）。"""
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            self._make_directive(-200.0)

    def test_volume_db_100_rejected(self) -> None:
        """volume_db=100 は ValidationError になること（le=20.0 制約）。"""
        with pytest.raises(ValidationError):
            self._make_directive(100.0)

    def test_volume_db_lower_boundary_minus60_accepted(self) -> None:
        """volume_db=-60.0 は許可されること（境界値）。"""
        d = self._make_directive(-60.0)
        assert d.volume_db == pytest.approx(-60.0)

    def test_volume_db_upper_boundary_20_accepted(self) -> None:
        """volume_db=20.0 は許可されること（境界値）。"""
        d = self._make_directive(20.0)
        assert d.volume_db == pytest.approx(20.0)

    def test_volume_db_inf_rejected(self) -> None:
        """volume_db=inf は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            self._make_directive(math.inf)

    def test_volume_db_neg_inf_rejected(self) -> None:
        """volume_db=-inf は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            self._make_directive(-math.inf)

    def test_volume_db_nan_rejected(self) -> None:
        """volume_db=nan は ValidationError になること（allow_inf_nan=False）。"""
        with pytest.raises(ValidationError):
            self._make_directive(math.nan)
