"""test_schemas.py — TranscribeOptions の Red テスト。

architecture TR-AD-06 の TranscribeOptions 仕様を観点に固定する。
このファイルは schemas.py が存在しない段階で import 失敗により
機能未実装として失敗することを意図した Red テスト群。
"""

from __future__ import annotations

import pytest

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
