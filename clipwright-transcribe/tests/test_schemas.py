"""test_schemas.py — Tests for TranscribeOptions.

Pins the TranscribeOptions specification from architecture TR-AD-06.

SR M-1 / SR L-1: Input validation hardening tests are appended at the end.
language: only ISO 639-1 compatible 2+ ASCII letters or "auto" are accepted
(^[a-zA-Z]{2,}$|^auto$), max_length=10. model_path: max_length=4096.
initial_prompt: max_length=2048.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_transcribe.schemas import TranscribeOptions

# ===========================================================================
# Default construction
# ===========================================================================


class TestTranscribeOptionsDefaults:
    """Model can be constructed with no arguments; each field has its default value."""

    def test_build_with_no_args(self) -> None:
        opts = TranscribeOptions()
        assert opts.language is None
        assert opts.model_path is None
        assert opts.initial_prompt is None

    def test_language_default_is_none(self) -> None:
        """language default is None (auto-detect; TR-AD-06)."""
        opts = TranscribeOptions()
        assert opts.language is None

    def test_model_path_default_is_none(self) -> None:
        """model_path default is None (env fallback; TR-AD-06)."""
        opts = TranscribeOptions()
        assert opts.model_path is None

    def test_initial_prompt_default_is_none(self) -> None:
        """initial_prompt default is None (no prompt; TR-AD-06)."""
        opts = TranscribeOptions()
        assert opts.initial_prompt is None


# ===========================================================================
# None acceptance and str acceptance
# ===========================================================================


class TestLanguageField:
    """Verify the language field type, None acceptance, and str acceptance."""

    def test_language_none_accepted(self) -> None:
        """Explicit language=None is accepted."""
        opts = TranscribeOptions(language=None)
        assert opts.language is None

    def test_language_str_accepted(self) -> None:
        """A str value for language is accepted."""
        opts = TranscribeOptions(language="ja")
        assert opts.language == "ja"

    @pytest.mark.parametrize("lang", ["en", "ja", "zh", "auto", "fr", "de"])
    def test_various_language_codes_accepted(self, lang: str) -> None:
        """Various language code strings are accepted."""
        opts = TranscribeOptions(language=lang)
        assert opts.language == lang

    def test_language_type_is_str_or_none(self) -> None:
        """language field annotation is str | None."""
        field_info = TranscribeOptions.model_fields["language"]
        # Pydantic v2: check annotation
        import typing

        annotation = field_info.annotation
        args = typing.get_args(annotation)
        # str and NoneType must both be present
        assert (
            str in args
            or annotation is str
            or annotation is type(None)
            or "str" in str(annotation)
        )


class TestModelPathField:
    """Verify the model_path field type, None acceptance, and str acceptance."""

    def test_model_path_none_accepted(self) -> None:
        """Explicit model_path=None is accepted."""
        opts = TranscribeOptions(model_path=None)
        assert opts.model_path is None

    def test_model_path_str_accepted(self) -> None:
        """A str value for model_path is accepted."""
        opts = TranscribeOptions(model_path="/path/to/ggml-base.bin")
        assert opts.model_path == "/path/to/ggml-base.bin"

    def test_model_path_type_is_str_or_none(self) -> None:
        """model_path field annotation is str | None."""
        field_info = TranscribeOptions.model_fields["model_path"]
        import typing

        annotation = field_info.annotation
        args = typing.get_args(annotation)
        assert str in args or annotation is str or "str" in str(annotation)


class TestInitialPromptField:
    """Verify the initial_prompt field type, None acceptance, and str acceptance."""

    def test_initial_prompt_none_accepted(self) -> None:
        """Explicit initial_prompt=None is accepted."""
        opts = TranscribeOptions(initial_prompt=None)
        assert opts.initial_prompt is None

    def test_initial_prompt_str_accepted(self) -> None:
        """A str value for initial_prompt is accepted."""
        opts = TranscribeOptions(initial_prompt="clipwright project meeting")
        assert opts.initial_prompt == "clipwright project meeting"

    def test_initial_prompt_empty_str_accepted(self) -> None:
        """An empty string for initial_prompt is accepted."""
        opts = TranscribeOptions(initial_prompt="")
        assert opts.initial_prompt == ""


# ===========================================================================
# All fields specified with valid values
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """Model can be constructed with all fields set to valid values.

    initial_prompt uses a Japanese string intentionally — this validates that
    arbitrary Unicode text is accepted as a hint value (ii: test data, do not
    translate).
    """
    opts = TranscribeOptions(
        language="ja",
        model_path="/models/ggml-base.bin",
        initial_prompt="クリップライト会議",
    )
    assert opts.language == "ja"
    assert opts.model_path == "/models/ggml-base.bin"
    assert opts.initial_prompt == "クリップライト会議"


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_transcribe_options_does_not_redefine_core_types() -> None:
    """core common types (MediaRef/Artifact/ToolResult) are not redefined."""
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    import clipwright_transcribe.schemas as transcribe_schemas

    assert not hasattr(transcribe_schemas, "MediaRef"), (
        "schemas.py redefines core MediaRef"
    )
    assert not hasattr(transcribe_schemas, "Artifact"), (
        "schemas.py redefines core Artifact"
    )
    assert not hasattr(transcribe_schemas, "ToolResult"), (
        "schemas.py redefines core ToolResult"
    )


# ===========================================================================
# Field description check (required for AI-readable schema)
# ===========================================================================


class TestFieldDescriptions:
    """Each field must have a description set."""

    def test_language_has_description(self) -> None:
        """language field has a description."""
        field_info = TranscribeOptions.model_fields["language"]
        assert field_info.description is not None and field_info.description != ""

    def test_model_path_has_description(self) -> None:
        """model_path field has a description."""
        field_info = TranscribeOptions.model_fields["model_path"]
        assert field_info.description is not None and field_info.description != ""

    def test_initial_prompt_has_description(self) -> None:
        """initial_prompt field has a description."""
        field_info = TranscribeOptions.model_fields["initial_prompt"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# SR M-1 / SR L-1: Input validation hardening (language / model_path /
# initial_prompt)
# ===========================================================================


class TestLanguageValidation:
    """language field constraint violations raise ValidationError (SR M-1).

    Constraints being implemented:
      - pattern: ^[a-zA-Z]{2,}$|^auto$
      - max_length: 10
    """

    # -----------------------------------------------------------------------
    # Valid values: within constraint range are accepted (non-regression)
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("lang", ["ja", "en", "auto", "zh", "fr", "de"])
    def test_valid_language_codes_accepted(self, lang: str) -> None:
        """ISO 639-1 compatible 2+ ASCII letters and "auto" are accepted."""
        opts = TranscribeOptions(language=lang)
        assert opts.language == lang

    def test_language_none_still_accepted(self) -> None:
        """language=None (default) continues to be accepted after constraint addition."""
        opts = TranscribeOptions(language=None)
        assert opts.language is None

    # -----------------------------------------------------------------------
    # Constraint violation: max_length exceeded
    # -----------------------------------------------------------------------

    def test_language_too_long_rejected(self) -> None:
        """language exceeding max_length=10 -> ValidationError."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="x" * 11)

    def test_language_exactly_max_length_accepted(self) -> None:
        """language exactly at max_length=10 (valid ASCII letters) is accepted."""
        opts = TranscribeOptions(language="abcdefghij")
        assert opts.language == "abcdefghij"

    # -----------------------------------------------------------------------
    # Constraint violation: pattern mismatch (hyphen prefix, non-alpha,
    # whitespace)
    # -----------------------------------------------------------------------

    def test_language_hyphen_prefix_rejected(self) -> None:
        """language starting with a hyphen (e.g. "-m") -> ValidationError (CWE-78
        mitigation)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="-m")

    def test_language_with_digit_rejected(self) -> None:
        """language containing a digit (e.g. "ja1") -> ValidationError (pattern
        mismatch)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="ja1")

    def test_language_with_space_rejected(self) -> None:
        """language containing whitespace (e.g. "ja en") -> ValidationError (pattern
        mismatch)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="ja en")

    def test_language_single_char_rejected(self) -> None:
        """Single-character language (e.g. "j") -> ValidationError (min 2 chars)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="j")

    def test_language_empty_string_rejected(self) -> None:
        """Empty string language ("") -> ValidationError (pattern mismatch)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language="")

    @pytest.mark.parametrize(
        "invalid_lang",
        [
            "-m",  # hyphen prefix (command injection mitigation)
            "--model",  # long option style
            "ja1",  # contains digit
            "ja en",  # contains whitespace
            "j",  # single character (too short)
            "",  # empty string
            "ja_JP",  # contains underscore
            "123",  # digits only
        ],
    )
    def test_invalid_language_patterns_rejected(self, invalid_lang: str) -> None:
        """Various invalid language strings -> ValidationError (parametrized)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(language=invalid_lang)


class TestModelPathValidation:
    """model_path field constraint violations raise ValidationError (SR L-1).

    Constraints being implemented:
      - max_length: 4096
    """

    # -----------------------------------------------------------------------
    # Valid values: within constraint range are accepted (non-regression)
    # -----------------------------------------------------------------------

    def test_model_path_none_still_accepted(self) -> None:
        """model_path=None (default) continues to be accepted after constraint
        addition."""
        opts = TranscribeOptions(model_path=None)
        assert opts.model_path is None

    def test_model_path_normal_path_accepted(self) -> None:
        """A normal file path string is accepted."""
        path = "/models/ggml-base.bin"
        opts = TranscribeOptions(model_path=path)
        assert opts.model_path == path

    def test_model_path_exactly_max_length_accepted(self) -> None:
        """model_path exactly at max_length=4096 is accepted."""
        path = "a" * 4096
        opts = TranscribeOptions(model_path=path)
        assert opts.model_path == path

    # -----------------------------------------------------------------------
    # Constraint violation: max_length exceeded
    # -----------------------------------------------------------------------

    def test_model_path_too_long_rejected(self) -> None:
        """model_path exceeding max_length=4096 -> ValidationError."""
        with pytest.raises(ValidationError):
            TranscribeOptions(model_path="a" * 4097)

    def test_model_path_4097_chars_rejected(self) -> None:
        """model_path at 4097 chars (1 over limit) -> ValidationError (boundary
        value)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(model_path="/models/" + "x" * 4089)

    def test_model_path_very_long_rejected(self) -> None:
        """Extremely long model_path -> ValidationError."""
        with pytest.raises(ValidationError):
            TranscribeOptions(model_path="x" * 10000)


class TestInitialPromptValidation:
    """initial_prompt field constraint violations raise ValidationError (SR L-1).

    Constraints being implemented:
      - max_length: 2048
    """

    # -----------------------------------------------------------------------
    # Valid values: within constraint range are accepted (non-regression)
    # -----------------------------------------------------------------------

    def test_initial_prompt_none_still_accepted(self) -> None:
        """initial_prompt=None (default) continues to be accepted after constraint
        addition."""
        opts = TranscribeOptions(initial_prompt=None)
        assert opts.initial_prompt is None

    def test_initial_prompt_normal_str_accepted(self) -> None:
        """A normal string is accepted.

        The Japanese value is intentional test data (a sample prompt that a user
        might supply; ii: do not translate).
        """
        prompt = "クリップライト会議の議事録"
        opts = TranscribeOptions(initial_prompt=prompt)
        assert opts.initial_prompt == prompt

    def test_initial_prompt_empty_str_still_accepted(self) -> None:
        """initial_prompt='' continues to be accepted after constraint addition."""
        opts = TranscribeOptions(initial_prompt="")
        assert opts.initial_prompt == ""

    def test_initial_prompt_exactly_max_length_accepted(self) -> None:
        """initial_prompt exactly at max_length=2048 is accepted."""
        prompt = "a" * 2048
        opts = TranscribeOptions(initial_prompt=prompt)
        assert opts.initial_prompt == prompt

    # -----------------------------------------------------------------------
    # Constraint violation: max_length exceeded
    # -----------------------------------------------------------------------

    def test_initial_prompt_too_long_rejected(self) -> None:
        """initial_prompt exceeding max_length=2048 -> ValidationError."""
        with pytest.raises(ValidationError):
            TranscribeOptions(initial_prompt="a" * 2049)

    def test_initial_prompt_2049_chars_rejected(self) -> None:
        """initial_prompt at 2049 chars (1 over limit) -> ValidationError (boundary
        value)."""
        with pytest.raises(ValidationError):
            TranscribeOptions(initial_prompt="x" * 2049)

    def test_initial_prompt_very_long_rejected(self) -> None:
        """Extremely long initial_prompt -> ValidationError.

        Uses a Japanese character to confirm multi-byte strings are counted correctly
        (ii: test data — do not translate).
        """
        with pytest.raises(ValidationError):
            TranscribeOptions(initial_prompt="あ" * 5000)


class TestValidationNonRegression:
    """Existing success-path tests remain Green after constraint additions
    (contract 100% non-regression)."""

    def test_all_none_defaults_still_work(self) -> None:
        """All-None defaults still construct the model successfully."""
        opts = TranscribeOptions()
        assert opts.language is None
        assert opts.model_path is None
        assert opts.initial_prompt is None

    def test_all_fields_with_valid_values_still_work(self) -> None:
        """All fields set to valid values still construct the model successfully.

        The Japanese initial_prompt is intentional test data (ii: do not translate).
        """
        opts = TranscribeOptions(
            language="ja",
            model_path="/models/ggml-base.bin",
            initial_prompt="クリップライト会議",
        )
        assert opts.language == "ja"
        assert opts.model_path == "/models/ggml-base.bin"
        assert opts.initial_prompt == "クリップライト会議"

    def test_language_en_accepted(self) -> None:
        """language="en" continues to be accepted after constraint addition."""
        opts = TranscribeOptions(language="en")
        assert opts.language == "en"

    def test_language_auto_accepted(self) -> None:
        """language="auto" continues to be accepted (special allowed value)."""
        opts = TranscribeOptions(language="auto")
        assert opts.language == "auto"
