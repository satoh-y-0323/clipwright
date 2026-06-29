"""test_schemas.py — Contract tests for WrapCaptionsOptions (contract coverage target: 100%).

Pins the WrapCaptionsOptions spec from architecture WR-AD-05.

Language allowlist (T-1/T-2):
  CJK/Thai: ja / zh-hans / zh-hant / th  (budoux phrase-boundary segmentation)
  Latin: en / es / fr / de / it / pt / nl  (in-process whitespace word segmentation)
  regex: LANGUAGE_PATTERN from languages.py

Apply transcribe SR M-1 style input validation (pattern / max_length) to WrapCaptionsOptions.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_wrap.schemas import WrapCaptionsOptions  # noqa: E402

# ===========================================================================
# Default construction
# ===========================================================================


class TestWrapCaptionsOptionsDefaults:
    """Model can be constructed with all fields omitted, and each field has its default value."""

    def test_build_with_no_args(self) -> None:
        """Model can be constructed with no arguments."""
        opts = WrapCaptionsOptions()
        assert opts is not None

    def test_language_default_is_ja(self) -> None:
        """Default value of language is 'ja' (Japanese) (WR-AD-05)."""
        opts = WrapCaptionsOptions()
        assert opts.language == "ja"

    def test_max_chars_default_is_16(self) -> None:
        """Default value of max_chars is 16 (Japanese subtitle convention ~16 full-width chars) (WR-AD-05)."""
        opts = WrapCaptionsOptions()
        assert opts.max_chars == 16

    def test_max_lines_default_is_2(self) -> None:
        """Default value of max_lines is 2 (WR-AD-05)."""
        opts = WrapCaptionsOptions()
        assert opts.max_lines == 2


# ===========================================================================
# language field
# ===========================================================================


class TestLanguageField:
    """Verify type, default, valid values, and constraint violations for the language field (DC-AM-005 gate)."""

    # -----------------------------------------------------------------------
    # Valid values: the 4 confirmed-loadable languages must be accepted
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("lang", ["ja", "zh-hans", "zh-hant", "th"])
    def test_valid_languages_accepted(self, lang: str) -> None:
        """All 4 spike-confirmed loadable languages are accepted (DC-AM-005)."""
        opts = WrapCaptionsOptions(language=lang)
        assert opts.language == lang

    def test_language_ja_accepted(self) -> None:
        """language='ja' is accepted."""
        opts = WrapCaptionsOptions(language="ja")
        assert opts.language == "ja"

    def test_language_zh_hans_accepted(self) -> None:
        """language='zh-hans' is accepted."""
        opts = WrapCaptionsOptions(language="zh-hans")
        assert opts.language == "zh-hans"

    def test_language_zh_hant_accepted(self) -> None:
        """language='zh-hant' is accepted."""
        opts = WrapCaptionsOptions(language="zh-hant")
        assert opts.language == "zh-hant"

    def test_language_th_accepted(self) -> None:
        """language='th' is accepted."""
        opts = WrapCaptionsOptions(language="th")
        assert opts.language == "th"

    # -----------------------------------------------------------------------
    # Constraint violations: pattern mismatch (language not supported by budoux)
    # -----------------------------------------------------------------------

    def test_language_en_accepted(self) -> None:
        """language='en' is accepted (English is a supported space-delimited Latin language; T-1)."""
        opts = WrapCaptionsOptions(language="en")
        assert opts.language == "en"

    @pytest.mark.parametrize("lang", ["en", "es", "fr", "de", "it", "pt", "nl"])
    def test_latin_languages_accepted(self, lang: str) -> None:
        """All 7 space-delimited Latin-script languages are accepted by the updated pattern (T-1)."""
        opts = WrapCaptionsOptions(language=lang)
        assert opts.language == lang

    def test_language_xx_rejected(self) -> None:
        """language='xx' raises ValidationError due to pattern mismatch (unknown language)."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="xx")

    def test_language_empty_rejected(self) -> None:
        """language='' raises ValidationError."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="")

    def test_language_zh_rejected(self) -> None:
        """language='zh' (without -hans/-hant suffix) raises ValidationError."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="zh")

    def test_language_JP_rejected(self) -> None:
        """language='JP' (uppercase) raises ValidationError (case-sensitive)."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="JP")

    def test_language_ja_JP_rejected(self) -> None:
        """language='ja-JP' (locale format) raises ValidationError."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="ja-JP")

    @pytest.mark.parametrize(
        "invalid_lang",
        [
            "xx",  # unknown language
            "",  # empty string
            "zh",  # incomplete Chinese code (neither zh-hans nor zh-hant)
            "JP",  # uppercase
            "ja-JP",  # locale format
            "ko",  # Korean (not in CJK or space-delimited allowlist)
        ],
    )
    def test_invalid_languages_rejected(self, invalid_lang: str) -> None:
        """Languages not in the LANGUAGE_PATTERN allowlist → ValidationError (parametrised)."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language=invalid_lang)

    # -----------------------------------------------------------------------
    # max_length constraint (reject values that are too long)
    # -----------------------------------------------------------------------

    def test_language_too_long_rejected(self) -> None:
        """language exceeding max_length → ValidationError."""
        # Longer than ja/zh-hans/zh-hant/th (also does not match pattern)
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="x" * 20)

    # -----------------------------------------------------------------------
    # Field description
    # -----------------------------------------------------------------------

    def test_language_has_description(self) -> None:
        """The language field has a description set (AI-facing explanation)."""
        field_info = WrapCaptionsOptions.model_fields["language"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# max_chars field
# ===========================================================================


class TestMaxCharsField:
    """Verify type, default, boundary values, and constraint violations for max_chars (WR-AD-05/WR-AD-14)."""

    # -----------------------------------------------------------------------
    # Valid values
    # -----------------------------------------------------------------------

    def test_max_chars_positive_accepted(self) -> None:
        """A positive integer passed to max_chars is accepted."""
        opts = WrapCaptionsOptions(max_chars=10)
        assert opts.max_chars == 10

    def test_max_chars_1_accepted(self) -> None:
        """max_chars=1 (minimum value for gt=0) is accepted (boundary value)."""
        opts = WrapCaptionsOptions(max_chars=1)
        assert opts.max_chars == 1

    def test_max_chars_16_default_accepted(self) -> None:
        """max_chars=16 (default value) is accepted."""
        opts = WrapCaptionsOptions(max_chars=16)
        assert opts.max_chars == 16

    def test_max_chars_large_value_accepted(self) -> None:
        """A large positive integer passed to max_chars is accepted."""
        opts = WrapCaptionsOptions(max_chars=1000)
        assert opts.max_chars == 1000

    # -----------------------------------------------------------------------
    # Constraint violations: gt=0 violation
    # -----------------------------------------------------------------------

    def test_max_chars_zero_rejected(self) -> None:
        """max_chars=0 → ValidationError due to gt=0 constraint."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_chars=0)

    def test_max_chars_negative_rejected(self) -> None:
        """max_chars=-1 → ValidationError due to gt=0 constraint."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_chars=-1)

    def test_max_chars_large_negative_rejected(self) -> None:
        """A large negative integer passed to max_chars → ValidationError."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_chars=-100)

    # -----------------------------------------------------------------------
    # Type
    # -----------------------------------------------------------------------

    def test_max_chars_type_is_int(self) -> None:
        """The type of the max_chars field is int."""
        opts = WrapCaptionsOptions()
        assert isinstance(opts.max_chars, int)

    # -----------------------------------------------------------------------
    # Field description
    # -----------------------------------------------------------------------

    def test_max_chars_has_description(self) -> None:
        """The max_chars field has a description set (AI-facing explanation)."""
        field_info = WrapCaptionsOptions.model_fields["max_chars"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# max_lines field
# ===========================================================================


class TestMaxLinesField:
    """Verify type, default, boundary values, and constraint violations for max_lines (WR-AD-05)."""

    # -----------------------------------------------------------------------
    # Valid values
    # -----------------------------------------------------------------------

    def test_max_lines_positive_accepted(self) -> None:
        """A positive integer passed to max_lines is accepted."""
        opts = WrapCaptionsOptions(max_lines=3)
        assert opts.max_lines == 3

    def test_max_lines_1_accepted(self) -> None:
        """max_lines=1 (minimum value for gt=0) is accepted (boundary value)."""
        opts = WrapCaptionsOptions(max_lines=1)
        assert opts.max_lines == 1

    def test_max_lines_2_default_accepted(self) -> None:
        """max_lines=2 (default value) is accepted."""
        opts = WrapCaptionsOptions(max_lines=2)
        assert opts.max_lines == 2

    # -----------------------------------------------------------------------
    # Constraint violations: gt=0 violation
    # -----------------------------------------------------------------------

    def test_max_lines_zero_rejected(self) -> None:
        """max_lines=0 → ValidationError due to gt=0 constraint."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_lines=0)

    def test_max_lines_negative_rejected(self) -> None:
        """max_lines=-1 → ValidationError due to gt=0 constraint."""
        with pytest.raises(ValidationError):
            WrapCaptionsOptions(max_lines=-1)

    # -----------------------------------------------------------------------
    # Type
    # -----------------------------------------------------------------------

    def test_max_lines_type_is_int(self) -> None:
        """The type of the max_lines field is int."""
        opts = WrapCaptionsOptions()
        assert isinstance(opts.max_lines, int)

    # -----------------------------------------------------------------------
    # Field description
    # -----------------------------------------------------------------------

    def test_max_lines_has_description(self) -> None:
        """The max_lines field has a description set (AI-facing explanation)."""
        field_info = WrapCaptionsOptions.model_fields["max_lines"]
        assert field_info.description is not None and field_info.description != ""


# ===========================================================================
# All fields specified with valid values
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """Model can be constructed with all fields explicitly set to valid values."""
    opts = WrapCaptionsOptions(language="ja", max_chars=13, max_lines=3)
    assert opts.language == "ja"
    assert opts.max_chars == 13
    assert opts.max_lines == 3


def test_all_fields_zh_hans() -> None:
    """All-field construction with zh-hans succeeds."""
    opts = WrapCaptionsOptions(language="zh-hans", max_chars=20, max_lines=2)
    assert opts.language == "zh-hans"
    assert opts.max_chars == 20
    assert opts.max_lines == 2


# ===========================================================================
# Confirm no redefinition of shared types
# ===========================================================================


# ===========================================================================
# T-2: LANGUAGE_PATTERN covers the expected CJK + Latin allowlist
# ===========================================================================


def test_language_pattern_covers_cjk_and_latin() -> None:
    """LANGUAGE_PATTERN accepts all CJK and Latin allowlist codes (T-2, AC-6)."""
    import re

    from clipwright_wrap.languages import (
        CJK_LANGUAGES,
        LANGUAGE_PATTERN,
        SPACE_DELIMITED_LANGUAGES,
    )

    for lang in CJK_LANGUAGES + SPACE_DELIMITED_LANGUAGES:
        assert re.fullmatch(LANGUAGE_PATTERN, lang) is not None, (
            f"Expected {lang!r} to match LANGUAGE_PATTERN"
        )


def test_language_pattern_rejects_unsupported_codes() -> None:
    """LANGUAGE_PATTERN rejects codes outside the allowlist (T-2, AC-3)."""
    import re

    from clipwright_wrap.languages import LANGUAGE_PATTERN

    for lang in ("ko", "xx", "", "zh", "JP", "ja-JP"):
        assert re.fullmatch(LANGUAGE_PATTERN, lang) is None, (
            f"Expected {lang!r} to NOT match LANGUAGE_PATTERN"
        )


def test_wrap_captions_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core shared types (MediaRef/Artifact/ToolResult) (naming convention)."""
    import clipwright_wrap.schemas as wrap_schemas

    assert not hasattr(wrap_schemas, "MediaRef"), "schemas.py redefines core MediaRef"
    assert not hasattr(wrap_schemas, "Artifact"), "schemas.py redefines core Artifact"
    assert not hasattr(wrap_schemas, "ToolResult"), (
        "schemas.py redefines core ToolResult"
    )
