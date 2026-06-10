"""test_schemas.py — DetectSilenceOptions の Red テスト。

architecture §AD-2/AD-3・DC-AM-001 の DetectSilenceOptions 仕様を観点に固定する。
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
        """min_keep_duration 既定は 0.0（DC-AM-001: opt-in ガード）。"""
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
    """core 共通型（MediaRef/Artifact/ToolResult）を再定義しないこと。"""
    # core の共通型が import できること
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    # clipwright_silence.schemas には同名クラスが存在しないこと
    import clipwright_silence.schemas as silence_schemas

    assert not hasattr(silence_schemas, "MediaRef"), (
        "schemas.py が core の MediaRef を再定義している"
    )
    assert not hasattr(silence_schemas, "Artifact"), (
        "schemas.py が core の Artifact を再定義している"
    )
    assert not hasattr(silence_schemas, "ToolResult"), (
        "schemas.py が core の ToolResult を再定義している"
    )


# ===========================================================================
# VAD 拡張フィールド — Red テスト（VAD-AD-01 / VAD-AD-05 / §7.6）
# ===========================================================================


class TestBackendField:
    """backend フィールドの型・既定値・制約を検証する（VAD-AD-01）。"""

    def test_backend_default_is_silencedetect(self) -> None:
        """backend 未指定で既定値が "silencedetect" であること（後方互換 opt-in VAD）。"""
        opts = DetectSilenceOptions()
        assert opts.backend == "silencedetect"

    def test_backend_silencedetect_accepted(self) -> None:
        """backend="silencedetect" を受理すること。"""
        opts = DetectSilenceOptions(backend="silencedetect")
        assert opts.backend == "silencedetect"

    def test_backend_vad_accepted(self) -> None:
        """backend="vad" を受理すること。"""
        opts = DetectSilenceOptions(backend="vad")
        assert opts.backend == "vad"

    @pytest.mark.parametrize(
        "invalid_backend",
        ["whisper", "auto", "VAD", "Silencedetect", "", "none", "ffmpeg"],
    )
    def test_invalid_backend_rejected(self, invalid_backend: str) -> None:
        """backend に Literal 外の値 → ValidationError。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(backend=invalid_backend)


class TestVadThresholdField:
    """vad_threshold フィールドの型・既定値・範囲制約を検証する（VAD-AD-05）。"""

    def test_vad_threshold_default_is_0_5(self) -> None:
        """vad_threshold の既定値が 0.5 であること。"""
        opts = DetectSilenceOptions()
        assert opts.vad_threshold == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 0.1, 0.5, 0.9, 1.0],
    )
    def test_valid_vad_threshold_accepted(self, threshold: float) -> None:
        """vad_threshold として 0.0–1.0 の値を受理すること。"""
        opts = DetectSilenceOptions(vad_threshold=threshold)
        assert opts.vad_threshold == pytest.approx(threshold)

    @pytest.mark.parametrize(
        "threshold",
        [-0.001, -0.1, -1.0, 1.001, 1.5, 2.0],
    )
    def test_out_of_range_vad_threshold_rejected(self, threshold: float) -> None:
        """vad_threshold に 0.0–1.0 の範囲外 → ValidationError。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(vad_threshold=threshold)


class TestVadMinSpeechDurationField:
    """vad_min_speech_duration フィールドの型・既定値・制約を検証する（VAD-AD-05）。"""

    def test_vad_min_speech_duration_default_is_0_25(self) -> None:
        """vad_min_speech_duration の既定値が 0.25 であること。"""
        opts = DetectSilenceOptions()
        assert opts.vad_min_speech_duration == pytest.approx(0.25)

    @pytest.mark.parametrize(
        "duration",
        [0.001, 0.1, 0.25, 0.5, 1.0, 5.0],
    )
    def test_valid_vad_min_speech_duration_accepted(self, duration: float) -> None:
        """vad_min_speech_duration として > 0 の値を受理すること。"""
        opts = DetectSilenceOptions(vad_min_speech_duration=duration)
        assert opts.vad_min_speech_duration == pytest.approx(duration)

    @pytest.mark.parametrize(
        "duration",
        [0.0, -0.001, -0.1, -1.0],
    )
    def test_non_positive_vad_min_speech_duration_rejected(self, duration: float) -> None:
        """vad_min_speech_duration に 0 以下 → ValidationError（制約: > 0）。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(vad_min_speech_duration=duration)


class TestVadMinSilenceDurationField:
    """vad_min_silence_duration フィールドの型・既定値・制約を検証する（VAD-AD-05）。"""

    def test_vad_min_silence_duration_default_is_0_1(self) -> None:
        """vad_min_silence_duration の既定値が 0.1 であること。"""
        opts = DetectSilenceOptions()
        assert opts.vad_min_silence_duration == pytest.approx(0.1)

    @pytest.mark.parametrize(
        "duration",
        [0.001, 0.05, 0.1, 0.5, 1.0, 3.0],
    )
    def test_valid_vad_min_silence_duration_accepted(self, duration: float) -> None:
        """vad_min_silence_duration として > 0 の値を受理すること。"""
        opts = DetectSilenceOptions(vad_min_silence_duration=duration)
        assert opts.vad_min_silence_duration == pytest.approx(duration)

    @pytest.mark.parametrize(
        "duration",
        [0.0, -0.001, -0.1, -1.0],
    )
    def test_non_positive_vad_min_silence_duration_rejected(self, duration: float) -> None:
        """vad_min_silence_duration に 0 以下 → ValidationError（制約: > 0）。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(vad_min_silence_duration=duration)


class TestExistingFieldsUnchanged:
    """既存フィールドの既定値・制約が VAD 拡張後も不変であること（非回帰）。"""

    def test_silence_threshold_db_default_unchanged(self) -> None:
        """silence_threshold_db の既定値が -30.0 のまま不変であること。"""
        opts = DetectSilenceOptions()
        assert opts.silence_threshold_db == pytest.approx(-30.0)

    def test_min_silence_duration_default_unchanged(self) -> None:
        """min_silence_duration の既定値が 0.5 のまま不変であること。"""
        opts = DetectSilenceOptions()
        assert opts.min_silence_duration == pytest.approx(0.5)

    def test_padding_default_unchanged(self) -> None:
        """padding の既定値が 0.1 のまま不変であること。"""
        opts = DetectSilenceOptions()
        assert opts.padding == pytest.approx(0.1)

    def test_min_keep_duration_default_unchanged(self) -> None:
        """min_keep_duration の既定値が 0.0 のまま不変であること。"""
        opts = DetectSilenceOptions()
        assert opts.min_keep_duration == pytest.approx(0.0)

    def test_positive_silence_threshold_db_still_rejected(self) -> None:
        """silence_threshold_db の > 0 制約が VAD 拡張後も維持されること。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(silence_threshold_db=0.1)

    def test_zero_min_silence_duration_still_rejected(self) -> None:
        """min_silence_duration の > 0 制約が VAD 拡張後も維持されること。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(min_silence_duration=0.0)

    def test_negative_padding_still_rejected(self) -> None:
        """padding の >= 0 制約が VAD 拡張後も維持されること。"""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(padding=-0.1)

    def test_all_fields_together_with_vad_fields(self) -> None:
        """既存フィールドと VAD 拡張フィールドを全て明示指定して構築できること。"""
        opts = DetectSilenceOptions(
            silence_threshold_db=-25.0,
            min_silence_duration=0.3,
            padding=0.05,
            min_keep_duration=1.0,
            backend="vad",
            vad_threshold=0.7,
            vad_min_speech_duration=0.3,
            vad_min_silence_duration=0.15,
        )
        assert opts.silence_threshold_db == pytest.approx(-25.0)
        assert opts.min_silence_duration == pytest.approx(0.3)
        assert opts.padding == pytest.approx(0.05)
        assert opts.min_keep_duration == pytest.approx(1.0)
        assert opts.backend == "vad"
        assert opts.vad_threshold == pytest.approx(0.7)
        assert opts.vad_min_speech_duration == pytest.approx(0.3)
        assert opts.vad_min_silence_duration == pytest.approx(0.15)


class TestFieldDescriptions:
    """Field description に誤用防止の明記があること（DC-AM-002・§7.6）。"""

    def test_min_silence_duration_description_mentions_silencedetect(self) -> None:
        """min_silence_duration の description に 'silencedetect' が含まれること。"""
        field_info = DetectSilenceOptions.model_fields["min_silence_duration"]
        description = field_info.description or ""
        assert "silencedetect" in description, (
            "min_silence_duration の description に 'silencedetect' が含まれていない。"
            "§7.6 DC-AM-002: silencedetect 専用フィールドである旨を明記すること。"
        )

    def test_silence_threshold_db_description_mentions_silencedetect(self) -> None:
        """silence_threshold_db の description に 'silencedetect' が含まれること。"""
        field_info = DetectSilenceOptions.model_fields["silence_threshold_db"]
        description = field_info.description or ""
        assert "silencedetect" in description, (
            "silence_threshold_db の description に 'silencedetect' が含まれていない。"
            "§7.6 DC-AM-002: silencedetect 専用フィールドである旨を明記すること。"
        )

    def test_vad_threshold_description_mentions_vad(self) -> None:
        """vad_threshold の description に 'VAD' が含まれること。"""
        field_info = DetectSilenceOptions.model_fields["vad_threshold"]
        description = field_info.description or ""
        assert "VAD" in description, (
            "vad_threshold の description に 'VAD' が含まれていない。"
            "§7.6 DC-AM-002: VAD 専用フィールドである旨を明記すること。"
        )

    def test_vad_min_speech_duration_description_mentions_vad(self) -> None:
        """vad_min_speech_duration の description に 'VAD' が含まれること。"""
        field_info = DetectSilenceOptions.model_fields["vad_min_speech_duration"]
        description = field_info.description or ""
        assert "VAD" in description, (
            "vad_min_speech_duration の description に 'VAD' が含まれていない。"
            "§7.6 DC-AM-002: VAD 専用フィールドである旨を明記すること。"
        )

    def test_vad_min_silence_duration_description_mentions_vad(self) -> None:
        """vad_min_silence_duration の description に 'VAD' が含まれること。"""
        field_info = DetectSilenceOptions.model_fields["vad_min_silence_duration"]
        description = field_info.description or ""
        assert "VAD" in description, (
            "vad_min_silence_duration の description に 'VAD' が含まれていない。"
            "§7.6 DC-AM-002: VAD 専用フィールドである旨を明記すること。"
        )
