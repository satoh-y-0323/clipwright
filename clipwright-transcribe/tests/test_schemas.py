"""test_schemas.py — TranscribeOptions の Red テスト。

architecture TR-AD-06 の TranscribeOptions 仕様を観点に固定する。
このファイルは schemas.py が存在しない段階で import 失敗により
機能未実装として失敗することを意図した Red テスト群。

SR M-1 / SR L-1 対応: TranscribeOptions 入力検証強化テストを末尾セクションに追加。
language は ISO639-1 相当 2 文字以上英字または "auto" のみ許可（^[a-zA-Z]{2,}$|^auto$）、
max_length=10。model_path は max_length=4096。initial_prompt は max_length=2048。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_transcribe.schemas import TranscribeOptions

# ===========================================================================
# デフォルト構築
# ===========================================================================


class TestTranscribeOptionsDefaults:
    """全フィールド省略でモデルが構築でき、各フィールドが既定値を持つこと。"""

    def test_build_with_no_args(self) -> None:
        opts = TranscribeOptions()
        assert opts.language is None
        assert opts.model_path is None
        assert opts.initial_prompt is None

    def test_language_default_is_none(self) -> None:
        """language の既定値は None（自動検出）であること（TR-AD-06）。"""
        opts = TranscribeOptions()
        assert opts.language is None

    def test_model_path_default_is_none(self) -> None:
        """model_path の既定値は None（env フォールバック）であること（TR-AD-06）。"""
        opts = TranscribeOptions()
        assert opts.model_path is None

    def test_initial_prompt_default_is_none(self) -> None:
        """initial_prompt の既定値は None（プロンプトなし）であること（TR-AD-06）。"""
        opts = TranscribeOptions()
        assert opts.initial_prompt is None


# ===========================================================================
# None 許容と str 受理
# ===========================================================================


class TestLanguageField:
    """language フィールドの型・None 許容・str 受理を検証する。"""

    def test_language_none_accepted(self) -> None:
        """language=None を明示指定しても構築できること。"""
        opts = TranscribeOptions(language=None)
        assert opts.language is None

    def test_language_str_accepted(self) -> None:
        """language に str を渡して受理されること。"""
        opts = TranscribeOptions(language="ja")
        assert opts.language == "ja"

    @pytest.mark.parametrize("lang", ["en", "ja", "zh", "auto", "fr", "de"])
    def test_various_language_codes_accepted(self, lang: str) -> None:
        """各種言語コード文字列を受理すること。"""
        opts = TranscribeOptions(language=lang)
        assert opts.language == lang

    def test_language_type_is_str_or_none(self) -> None:
        """language フィールドの型注釈が str | None であること。"""
        field_info = TranscribeOptions.model_fields["language"]
        # Pydantic v2: annotation を確認する
        import typing

        annotation = field_info.annotation
        args = typing.get_args(annotation)
        # str と NoneType が含まれること
        assert (
            str in args
            or annotation is str
            or annotation is type(None)
            or "str" in str(annotation)
        )


class TestModelPathField:
    """model_path フィールドの型・None 許容・str 受理を検証する。"""

    def test_model_path_none_accepted(self) -> None:
        """model_path=None を明示指定しても構築できること。"""
        opts = TranscribeOptions(model_path=None)
        assert opts.model_path is None

    def test_model_path_str_accepted(self) -> None:
        """model_path に str を渡して受理されること。"""
        opts = TranscribeOptions(model_path="/path/to/ggml-base.bin")
        assert opts.model_path == "/path/to/ggml-base.bin"

    def test_model_path_type_is_str_or_none(self) -> None:
        """model_path フィールドの型注釈が str | None であること。"""
        field_info = TranscribeOptions.model_fields["model_path"]
        import typing

        annotation = field_info.annotation
        args = typing.get_args(annotation)
        assert str in args or annotation is str or "str" in str(annotation)


class TestInitialPromptField:
    """initial_prompt フィールドの型・None 許容・str 受理を検証する。"""

    def test_initial_prompt_none_accepted(self) -> None:
        """initial_prompt=None を明示指定しても構築できること。"""
        opts = TranscribeOptions(initial_prompt=None)
        assert opts.initial_prompt is None

    def test_initial_prompt_str_accepted(self) -> None:
        """initial_prompt に str を渡して受理されること。"""
        opts = TranscribeOptions(initial_prompt="clipwright project meeting")
        assert opts.initial_prompt == "clipwright project meeting"

    def test_initial_prompt_empty_str_accepted(self) -> None:
        """initial_prompt に空文字列を渡しても受理されること。"""
        opts = TranscribeOptions(initial_prompt="")
        assert opts.initial_prompt == ""


# ===========================================================================
# 全フィールド正常指定
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """全フィールドを明示的に正常値で指定してモデルが構築できること。"""
    opts = TranscribeOptions(
        language="ja",
        model_path="/models/ggml-base.bin",
        initial_prompt="クリップライト会議",
    )
    assert opts.language == "ja"
    assert opts.model_path == "/models/ggml-base.bin"
    assert opts.initial_prompt == "クリップライト会議"


# ===========================================================================
# 共通型の再定義なし確認
# ===========================================================================


def test_transcribe_options_does_not_redefine_core_types() -> None:
    """core 共通型（MediaRef/Artifact/ToolResult）を再定義しないこと。"""
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    import clipwright_transcribe.schemas as transcribe_schemas

    assert not hasattr(transcribe_schemas, "MediaRef"), (
        "schemas.py が core の MediaRef を再定義している"
    )
    assert not hasattr(transcribe_schemas, "Artifact"), (
        "schemas.py が core の Artifact を再定義している"
    )
    assert not hasattr(transcribe_schemas, "ToolResult"), (
        "schemas.py が core の ToolResult を再定義している"
    )


# ===========================================================================
# Field description の確認（AI が読む説明として必要）
# ===========================================================================


class TestFieldDescriptions:
    """Field に description が設定されていること。"""

    def test_language_has_description(self) -> None:
        """language フィールドに description が設定されていること。"""
        field_info = TranscribeOptions.model_fields["language"]
        assert field_info.description is not None and field_info.description != ""

    def test_model_path_has_description(self) -> None:
        """model_path フィールドに description が設定されていること。"""
        field_info = TranscribeOptions.model_fields["model_path"]
        assert field_info.description is not None and field_info.description != ""

    def test_initial_prompt_has_description(self) -> None:
        """initial_prompt フィールドに description が設定されていること。"""
        field_info = TranscribeOptions.model_fields["initial_prompt"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# SR M-1 / SR L-1: 入力検証強化（language / model_path / initial_prompt）
# ===========================================================================


class TestLanguageValidation:
    """language フィールドの制約違反が ValidationError を送出すること（SR M-1）。

    実装予定の制約:
      - pattern: ^[a-zA-Z]{2,}$|^auto$
      - max_length: 10
    """

    # -----------------------------------------------------------------------
    # 有効値: 制約範囲内は受理されること（非回帰）
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("lang", ["ja", "en", "auto", "zh", "fr", "de"])
    def test_valid_language_codes_accepted(self, lang: str) -> None:
        """ISO639-1 相当の 2 文字以上英字・および "auto" は受理されること。"""
        opts = TranscribeOptions(language=lang)
        assert opts.language == lang

    def test_language_none_still_accepted(self) -> None:
        """language=None（既定）は制約追加後も引き続き受理されること。"""
        opts = TranscribeOptions(language=None)
        assert opts.language is None

    # -----------------------------------------------------------------------
    # 制約違反: max_length 超過
    # -----------------------------------------------------------------------

    def test_language_too_long_rejected(self) -> None:
        """language が max_length=10 を超える文字列 → ValidationError。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="x" * 11)

    def test_language_exactly_max_length_accepted(self) -> None:
        """language が max_length=10 ちょうど（有効英字列）は受理されること。"""
        opts = TranscribeOptions(language="abcdefghij")
        assert opts.language == "abcdefghij"

    # -----------------------------------------------------------------------
    # 制約違反: pattern 不一致（ハイフン始まり・非英字・空白）
    # -----------------------------------------------------------------------

    def test_language_hyphen_prefix_rejected(self) -> None:
        """language にハイフン始まり文字列（例: "-m"）→ ValidationError（CWE-78 対策）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="-m")

    def test_language_with_digit_rejected(self) -> None:
        """language に数字混じり（例: "ja1"）→ ValidationError（pattern 不一致）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="ja1")

    def test_language_with_space_rejected(self) -> None:
        """language に空白含む文字列（例: "ja en"）→ ValidationError（pattern 不一致）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="ja en")

    def test_language_single_char_rejected(self) -> None:
        """language が 1 文字英字（例: "j"）→ ValidationError（2 文字以上の制約）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="j")

    def test_language_empty_string_rejected(self) -> None:
        """language が空文字列（""）→ ValidationError（pattern 不一致）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="")

    @pytest.mark.parametrize(
        "invalid_lang",
        [
            "-m",  # ハイフン始まり（コマンドインジェクション対策）
            "--model",  # 長いオプション風
            "ja1",  # 数字混じり
            "ja en",  # 空白含む
            "j",  # 1 文字（短すぎる）
            "",  # 空文字列
            "ja_JP",  # アンダースコア含む
            "123",  # 数字のみ
        ],
    )
    def test_invalid_language_patterns_rejected(self, invalid_lang: str) -> None:
        """各種不正 language 文字列 → ValidationError（パラメータ化）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(language=invalid_lang)


class TestModelPathValidation:
    """model_path フィールドの制約違反が ValidationError を送出すること（SR L-1）。

    実装予定の制約:
      - max_length: 4096
    """

    # -----------------------------------------------------------------------
    # 有効値: 制約範囲内は受理されること（非回帰）
    # -----------------------------------------------------------------------

    def test_model_path_none_still_accepted(self) -> None:
        """model_path=None（既定）は制約追加後も引き続き受理されること。"""
        opts = TranscribeOptions(model_path=None)
        assert opts.model_path is None

    def test_model_path_normal_path_accepted(self) -> None:
        """通常のファイルパス文字列は受理されること。"""
        path = "/models/ggml-base.bin"
        opts = TranscribeOptions(model_path=path)
        assert opts.model_path == path

    def test_model_path_exactly_max_length_accepted(self) -> None:
        """model_path が max_length=4096 ちょうどは受理されること。"""
        path = "a" * 4096
        opts = TranscribeOptions(model_path=path)
        assert opts.model_path == path

    # -----------------------------------------------------------------------
    # 制約違反: max_length 超過
    # -----------------------------------------------------------------------

    def test_model_path_too_long_rejected(self) -> None:
        """model_path が max_length=4096 を超える → ValidationError。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(model_path="a" * 4097)

    def test_model_path_4097_chars_rejected(self) -> None:
        """model_path が 4097 文字（超過 1 文字）→ ValidationError（境界値）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(model_path="/models/" + "x" * 4089)

    def test_model_path_very_long_rejected(self) -> None:
        """model_path が極端に長い文字列 → ValidationError。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(model_path="x" * 10000)


class TestInitialPromptValidation:
    """initial_prompt フィールドの制約違反が ValidationError を送出すること（SR L-1）。

    実装予定の制約:
      - max_length: 2048
    """

    # -----------------------------------------------------------------------
    # 有効値: 制約範囲内は受理されること（非回帰）
    # -----------------------------------------------------------------------

    def test_initial_prompt_none_still_accepted(self) -> None:
        """initial_prompt=None（既定）は制約追加後も引き続き受理されること。"""
        opts = TranscribeOptions(initial_prompt=None)
        assert opts.initial_prompt is None

    def test_initial_prompt_normal_str_accepted(self) -> None:
        """通常の文字列は受理されること。"""
        prompt = "クリップライト会議の議事録"
        opts = TranscribeOptions(initial_prompt=prompt)
        assert opts.initial_prompt == prompt

    def test_initial_prompt_empty_str_still_accepted(self) -> None:
        """initial_prompt=空文字列は制約追加後も引き続き受理されること。"""
        opts = TranscribeOptions(initial_prompt="")
        assert opts.initial_prompt == ""

    def test_initial_prompt_exactly_max_length_accepted(self) -> None:
        """initial_prompt が max_length=2048 ちょうどは受理されること。"""
        prompt = "a" * 2048
        opts = TranscribeOptions(initial_prompt=prompt)
        assert opts.initial_prompt == prompt

    # -----------------------------------------------------------------------
    # 制約違反: max_length 超過
    # -----------------------------------------------------------------------

    def test_initial_prompt_too_long_rejected(self) -> None:
        """initial_prompt が max_length=2048 を超える → ValidationError。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(initial_prompt="a" * 2049)

    def test_initial_prompt_2049_chars_rejected(self) -> None:
        """initial_prompt が 2049 文字（超過 1 文字）→ ValidationError（境界値）。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(initial_prompt="x" * 2049)

    def test_initial_prompt_very_long_rejected(self) -> None:
        """initial_prompt が極端に長い文字列 → ValidationError。"""
        with pytest.raises(ValidationError):
            TranscribeOptions(initial_prompt="あ" * 5000)


class TestValidationNonRegression:
    """制約追加後も既存の正常系テストが壊れないことを確認する（契約面 100% 非回帰）。"""

    def test_all_none_defaults_still_work(self) -> None:
        """全フィールド省略（全 None 既定）でモデルが構築できること。"""
        opts = TranscribeOptions()
        assert opts.language is None
        assert opts.model_path is None
        assert opts.initial_prompt is None

    def test_all_fields_with_valid_values_still_work(self) -> None:
        """全フィールドを正常値で指定してモデルが構築できること。"""
        opts = TranscribeOptions(
            language="ja",
            model_path="/models/ggml-base.bin",
            initial_prompt="クリップライト会議",
        )
        assert opts.language == "ja"
        assert opts.model_path == "/models/ggml-base.bin"
        assert opts.initial_prompt == "クリップライト会議"

    def test_language_en_accepted(self) -> None:
        """language="en" は制約追加後も受理されること。"""
        opts = TranscribeOptions(language="en")
        assert opts.language == "en"

    def test_language_auto_accepted(self) -> None:
        """language="auto" は制約追加後も受理されること（特例許可値）。"""
        opts = TranscribeOptions(language="auto")
        assert opts.language == "auto"
