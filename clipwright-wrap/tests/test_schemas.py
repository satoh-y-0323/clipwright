"""test_schemas.py — WrapCaptionsOptions の Red テスト（契約面 100% 目標）。

architecture WR-AD-05 の WrapCaptionsOptions 仕様を観点に固定する。
このファイルは schemas.py が存在しない段階で import 失敗により
機能未実装として失敗することを意図した Red テスト群。

DC-AM-005 ゲート: language pattern は spike 確定の実ロード可能言語のみ許可。
  ja / zh-hans / zh-hant / th の4言語（全ロード成功）
  正規表現: ^(ja|zh-hans|zh-hant|th)$

transcribe SR M-1 同型の入力検証（pattern / max_length）を WrapCaptionsOptions に適用する。
"""

from __future__ import annotations

import pytest
from clipwright_wrap.schemas import WrapCaptionsOptions  # noqa: E402
from pydantic import ValidationError

# ===========================================================================
# デフォルト構築
# ===========================================================================


class TestWrapCaptionsOptionsDefaults:
    """全フィールド省略でモデルが構築でき、各フィールドが既定値を持つこと。"""

    def test_build_with_no_args(self) -> None:
        """引数なしでモデルが構築できること。"""
        opts = WrapCaptionsOptions()
        assert opts is not None

    def test_language_default_is_ja(self) -> None:
        """language の既定値は 'ja'（日本語）であること（WR-AD-05）。"""
        opts = WrapCaptionsOptions()
        assert opts.language == "ja"

    def test_max_chars_default_is_16(self) -> None:
        """max_chars の既定値は 16（日本語字幕慣習の全角 ~16）であること（WR-AD-05）。"""
        opts = WrapCaptionsOptions()
        assert opts.max_chars == 16

    def test_max_lines_default_is_2(self) -> None:
        """max_lines の既定値は 2 であること（WR-AD-05）。"""
        opts = WrapCaptionsOptions()
        assert opts.max_lines == 2


# ===========================================================================
# language フィールド
# ===========================================================================


class TestLanguageField:
    """language フィールドの型・既定・有効値・制約違反を検証する（DC-AM-005 ゲート）。"""

    # -----------------------------------------------------------------------
    # 有効値: 実ロード可能な4言語は受理されること
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("lang", ["ja", "zh-hans", "zh-hant", "th"])
    def test_valid_languages_accepted(self, lang: str) -> None:
        """spike 確定の実ロード可能4言語は受理されること（DC-AM-005）。"""
        opts = WrapCaptionsOptions(language=lang)
        assert opts.language == lang

    def test_language_ja_accepted(self) -> None:
        """language='ja' は受理されること。"""
        opts = WrapCaptionsOptions(language="ja")
        assert opts.language == "ja"

    def test_language_zh_hans_accepted(self) -> None:
        """language='zh-hans' は受理されること。"""
        opts = WrapCaptionsOptions(language="zh-hans")
        assert opts.language == "zh-hans"

    def test_language_zh_hant_accepted(self) -> None:
        """language='zh-hant' は受理されること。"""
        opts = WrapCaptionsOptions(language="zh-hant")
        assert opts.language == "zh-hant"

    def test_language_th_accepted(self) -> None:
        """language='th' は受理されること。"""
        opts = WrapCaptionsOptions(language="th")
        assert opts.language == "th"

    # -----------------------------------------------------------------------
    # 制約違反: pattern 不一致（budoux 非対応言語）
    # -----------------------------------------------------------------------

    def test_language_en_rejected(self) -> None:
        """language='en' は pattern 不一致で ValidationError になること（英語は未対応）。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="en")

    def test_language_xx_rejected(self) -> None:
        """language='xx' は pattern 不一致で ValidationError になること（不明言語）。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="xx")

    def test_language_empty_rejected(self) -> None:
        """language='' は ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="")

    def test_language_zh_rejected(self) -> None:
        """language='zh'（zh-hans/zh-hant を指定しない）は ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="zh")

    def test_language_JP_rejected(self) -> None:
        """language='JP'（大文字）は ValidationError になること（大文字小文字区別あり）。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="JP")

    def test_language_ja_JP_rejected(self) -> None:
        """language='ja-JP'（ロケール形式）は ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="ja-JP")

    @pytest.mark.parametrize(
        "invalid_lang",
        [
            "en",  # 英語（budoux 非対応）
            "xx",  # 不明言語
            "",  # 空文字列
            "zh",  # 不完全な中国語コード
            "JP",  # 大文字
            "ja-JP",  # ロケール形式
            "ko",  # 韓国語（非対応）
            "fr",  # フランス語（非対応）
        ],
    )
    def test_invalid_languages_rejected(self, invalid_lang: str) -> None:
        """pattern ^(ja|zh-hans|zh-hant|th)$ に一致しない言語 → ValidationError（パラメータ化）。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language=invalid_lang)

    # -----------------------------------------------------------------------
    # max_length 制約（長すぎる値の拒否）
    # -----------------------------------------------------------------------

    def test_language_too_long_rejected(self) -> None:
        """language が max_length を超える文字列 → ValidationError。"""
        # ja/zh-hans/zh-hant/th より長い文字列（pattern にもマッチしない）
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="x" * 20)

    # -----------------------------------------------------------------------
    # Field description
    # -----------------------------------------------------------------------

    def test_language_has_description(self) -> None:
        """language フィールドに description が設定されていること（AI が読む説明）。"""
        field_info = WrapCaptionsOptions.model_fields["language"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# max_chars フィールド
# ===========================================================================


class TestMaxCharsField:
    """max_chars フィールドの型・既定・境界値・制約違反を検証する（WR-AD-05/WR-AD-14）。"""

    # -----------------------------------------------------------------------
    # 有効値
    # -----------------------------------------------------------------------

    def test_max_chars_positive_accepted(self) -> None:
        """max_chars に正の整数を渡して受理されること。"""
        opts = WrapCaptionsOptions(max_chars=10)
        assert opts.max_chars == 10

    def test_max_chars_1_accepted(self) -> None:
        """max_chars=1（gt=0 の最小値）は受理されること（境界値）。"""
        opts = WrapCaptionsOptions(max_chars=1)
        assert opts.max_chars == 1

    def test_max_chars_16_default_accepted(self) -> None:
        """max_chars=16（既定値）は受理されること。"""
        opts = WrapCaptionsOptions(max_chars=16)
        assert opts.max_chars == 16

    def test_max_chars_large_value_accepted(self) -> None:
        """max_chars に大きな正の整数を渡して受理されること。"""
        opts = WrapCaptionsOptions(max_chars=1000)
        assert opts.max_chars == 1000

    # -----------------------------------------------------------------------
    # 制約違反: gt=0 違反
    # -----------------------------------------------------------------------

    def test_max_chars_zero_rejected(self) -> None:
        """max_chars=0 → gt=0 制約違反で ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_chars=0)

    def test_max_chars_negative_rejected(self) -> None:
        """max_chars=-1 → gt=0 制約違反で ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_chars=-1)

    def test_max_chars_large_negative_rejected(self) -> None:
        """max_chars に大きな負の整数 → ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_chars=-100)

    # -----------------------------------------------------------------------
    # 型
    # -----------------------------------------------------------------------

    def test_max_chars_type_is_int(self) -> None:
        """max_chars フィールドの型が int であること。"""
        opts = WrapCaptionsOptions()
        assert isinstance(opts.max_chars, int)

    # -----------------------------------------------------------------------
    # Field description
    # -----------------------------------------------------------------------

    def test_max_chars_has_description(self) -> None:
        """max_chars フィールドに description が設定されていること（AI が読む説明）。"""
        field_info = WrapCaptionsOptions.model_fields["max_chars"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# max_lines フィールド
# ===========================================================================


class TestMaxLinesField:
    """max_lines フィールドの型・既定・境界値・制約違反を検証する（WR-AD-05）。"""

    # -----------------------------------------------------------------------
    # 有効値
    # -----------------------------------------------------------------------

    def test_max_lines_positive_accepted(self) -> None:
        """max_lines に正の整数を渡して受理されること。"""
        opts = WrapCaptionsOptions(max_lines=3)
        assert opts.max_lines == 3

    def test_max_lines_1_accepted(self) -> None:
        """max_lines=1（gt=0 の最小値）は受理されること（境界値）。"""
        opts = WrapCaptionsOptions(max_lines=1)
        assert opts.max_lines == 1

    def test_max_lines_2_default_accepted(self) -> None:
        """max_lines=2（既定値）は受理されること。"""
        opts = WrapCaptionsOptions(max_lines=2)
        assert opts.max_lines == 2

    # -----------------------------------------------------------------------
    # 制約違反: gt=0 違反
    # -----------------------------------------------------------------------

    def test_max_lines_zero_rejected(self) -> None:
        """max_lines=0 → gt=0 制約違反で ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_lines=0)

    def test_max_lines_negative_rejected(self) -> None:
        """max_lines=-1 → gt=0 制約違反で ValidationError になること。"""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_lines=-1)

    # -----------------------------------------------------------------------
    # 型
    # -----------------------------------------------------------------------

    def test_max_lines_type_is_int(self) -> None:
        """max_lines フィールドの型が int であること。"""
        opts = WrapCaptionsOptions()
        assert isinstance(opts.max_lines, int)

    # -----------------------------------------------------------------------
    # Field description
    # -----------------------------------------------------------------------

    def test_max_lines_has_description(self) -> None:
        """max_lines フィールドに description が設定されていること（AI が読む説明）。"""
        field_info = WrapCaptionsOptions.model_fields["max_lines"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# 全フィールド正常指定
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """全フィールドを明示的に正常値で指定してモデルが構築できること。"""
    opts = WrapCaptionsOptions(language="ja", max_chars=13, max_lines=3)
    assert opts.language == "ja"
    assert opts.max_chars == 13
    assert opts.max_lines == 3


def test_all_fields_zh_hans() -> None:
    """zh-hans を指定した全フィールド構築ができること。"""
    opts = WrapCaptionsOptions(language="zh-hans", max_chars=20, max_lines=2)
    assert opts.language == "zh-hans"
    assert opts.max_chars == 20
    assert opts.max_lines == 2


# ===========================================================================
# 共通型の再定義なし確認
# ===========================================================================


def test_wrap_captions_options_does_not_redefine_core_types() -> None:
    """core 共通型（MediaRef/Artifact/ToolResult）を再定義しないこと（命名規約）。"""
    import clipwright_wrap.schemas as wrap_schemas

    assert not hasattr(wrap_schemas, "MediaRef"), (
        "schemas.py が core の MediaRef を再定義している"
    )
    assert not hasattr(wrap_schemas, "Artifact"), (
        "schemas.py が core の Artifact を再定義している"
    )
    assert not hasattr(wrap_schemas, "ToolResult"), (
        "schemas.py が core の ToolResult を再定義している"
    )
