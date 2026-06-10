"""test_schemas.py — DetectSilenceOptions の Red テスト。

architecture §AD-2/AD-3・DC-AM-001 の DetectSilenceOptions 確定仕様を観点として固定する。
このファイルは schemas.py が存在しない / DetectSilenceOptions が未実装の段階で
機能未実装により失敗することを意図した Red テスト群。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_silence.schemas import DetectSilenceOptions


# ===========================================================================
# デフォルト構築
# ===========================================================================


class TestDetectSilenceOptionsDefaults:
    """全フィールド省略でモデルが構築でき、各フィールドが既定値を持つこと。"""

    def test_build_with_no_args(self) -> None:
        # Arrange / Act
        opts = DetectSilenceOptions()

        # Assert
        assert opts.silence_threshold_db == pytest.approx(-30.0)
        assert opts.min_silence_duration == pytest.approx(0.5)
        assert opts.padding == pytest.approx(0.1)
        assert opts.min_keep_duration == pytest.approx(0.0)

    def test_default_silence_threshold_db_is_negative(self) -> None:
        """silence_threshold_db の既定値は負値（dB ≤ 0 の制約内）であること。"""
        opts = DetectSilenceOptions()
        assert opts.silence_threshold_db <= 0.0

    def test_default_min_silence_duration_is_positive(self) -> None:
        """min_silence_duration の既定値は正値（> 0 の制約内）であること。"""
        opts = DetectSilenceOptions()
        assert opts.min_silence_duration > 0.0

    def test_default_padding_is_non_negative(self) -> None:
        """padding の既定値は 0 以上（≥ 0 の制約内）であること。"""
        opts = DetectSilenceOptions()
        assert opts.padding >= 0.0

    def test_default_min_keep_duration_is_zero(self) -> None:
        """min_keep_duration の既定値は 0.0（DC-AM-001: opt-in ガード・既定は無破棄）。"""
        opts = DetectSilenceOptions()
        assert opts.min_keep_duration == pytest.approx(0.0)


# ===========================================================================
# 有効値の受理
# ===========================================================================


@pytest.mark.parametrize(
    "threshold",
    [-10.0, -20.0, -30.0, -40.0, -60.0, 0.0],
)
def test_valid_silence_threshold_db_accepted(threshold: float) -> None:
    """silence_threshold_db として ≤ 0 の値を受理すること。"""
    opts = DetectSilenceOptions(silence_threshold_db=threshold)
    assert opts.silence_threshold_db == pytest.approx(threshold)


@pytest.mark.parametrize(
    "duration",
    [0.001, 0.1, 0.5, 1.0, 5.0, 10.0],
)
def test_valid_min_silence_duration_accepted(duration: float) -> None:
    """min_silence_duration として > 0 の値を受理すること。"""
    opts = DetectSilenceOptions(min_silence_duration=duration)
    assert opts.min_silence_duration == pytest.approx(duration)


@pytest.mark.parametrize(
    "pad",
    [0.0, 0.05, 0.1, 0.5, 1.0, 2.0],
)
def test_valid_padding_accepted(pad: float) -> None:
    """padding として ≥ 0 の値を受理すること。"""
    opts = DetectSilenceOptions(padding=pad)
    assert opts.padding == pytest.approx(pad)


@pytest.mark.parametrize(
    "min_keep",
    [0.0, 0.1, 0.5, 1.0, 2.0],
)
def test_valid_min_keep_duration_accepted(min_keep: float) -> None:
    """min_keep_duration として ≥ 0 の値を受理すること。"""
    opts = DetectSilenceOptions(min_keep_duration=min_keep)
    assert opts.min_keep_duration == pytest.approx(min_keep)


# ===========================================================================
# 制約違反（ValidationError）
# ===========================================================================


@pytest.mark.parametrize(
    "threshold",
    [0.1, 1.0, 10.0, 0.001],
)
def test_positive_silence_threshold_db_rejected(threshold: float) -> None:
    """silence_threshold_db に正値 → ValidationError（制約: ≤ 0）。"""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(silence_threshold_db=threshold)


@pytest.mark.parametrize(
    "duration",
    [0.0, -0.001, -1.0, -5.0],
)
def test_non_positive_min_silence_duration_rejected(duration: float) -> None:
    """min_silence_duration に 0 以下 → ValidationError（制約: > 0）。"""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(min_silence_duration=duration)


@pytest.mark.parametrize(
    "pad",
    [-0.001, -0.1, -1.0, -5.0],
)
def test_negative_padding_rejected(pad: float) -> None:
    """padding に負値 → ValidationError（制約: ≥ 0）。"""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(padding=pad)


@pytest.mark.parametrize(
    "min_keep",
    [-0.001, -0.1, -1.0, -5.0],
)
def test_negative_min_keep_duration_rejected(min_keep: float) -> None:
    """min_keep_duration に負値 → ValidationError（制約: ≥ 0）。"""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(min_keep_duration=min_keep)


# ===========================================================================
# 全フィールド正常指定
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """全フィールドを明示的に正常値で指定してモデルが構築できること。"""
    opts = DetectSilenceOptions(
        silence_threshold_db=-25.0,
        min_silence_duration=0.3,
        padding=0.05,
        min_keep_duration=1.0,
    )
    assert opts.silence_threshold_db == pytest.approx(-25.0)
    assert opts.min_silence_duration == pytest.approx(0.3)
    assert opts.padding == pytest.approx(0.05)
    assert opts.min_keep_duration == pytest.approx(1.0)


# ===========================================================================
# 共通型の再定義なし確認
# ===========================================================================


def test_detect_silence_options_does_not_redefine_core_types() -> None:
    """DetectSilenceOptions が core 共通型（MediaRef/Artifact/ToolResult）を再定義しないこと。"""
    # core の共通型が import できること
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    # clipwright_silence.schemas には同名クラスが存在しないこと
    import clipwright_silence.schemas as silence_schemas

    assert not hasattr(silence_schemas, "MediaRef"), (
        "DetectSilenceOptions を定義する schemas.py が core の MediaRef を再定義している"
    )
    assert not hasattr(silence_schemas, "Artifact"), (
        "DetectSilenceOptions を定義する schemas.py が core の Artifact を再定義している"
    )
    assert not hasattr(silence_schemas, "ToolResult"), (
        "DetectSilenceOptions を定義する schemas.py が core の ToolResult を再定義している"
    )
