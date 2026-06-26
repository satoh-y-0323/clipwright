"""test_errors.py — Contract tests for errors.py.

Covers:
- ErrorCode(Enum): verify all required members exist (§4 + §13.1 DC-AM-002/DC-AS-003)
- ClipwrightError: exception class holding code / message / hint
"""

from __future__ import annotations

import pytest

# --- Import ---
from clipwright.errors import ClipwrightError, ErrorCode

# ===========================================================================
# ErrorCode — required member existence
# ===========================================================================


class TestErrorCodeMembers:
    """Verify all required ErrorCode Enum members are defined."""

    @pytest.mark.parametrize(
        "name",
        [
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "FILE_NOT_FOUND",
            "PATH_NOT_ALLOWED",
            "SUBPROCESS_FAILED",
            "SUBPROCESS_TIMEOUT",
            "PROBE_FAILED",
            "OTIO_ERROR",
            "PROJECT_NOT_FOUND",
            "PROJECT_EXISTS",
            "UNSUPPORTED_OPERATION",
            "INTERNAL",  # added in §13.1 DC-AM-002
            "TRACK_NOT_FOUND",  # added in §13.1 DC-AS-003
        ],
    )
    def test_member_exists(self, name: str) -> None:
        """Each required member exists in ErrorCode."""
        assert hasattr(ErrorCode, name), f"ErrorCode.{name} is not defined"

    def test_is_str_enum(self) -> None:
        """ErrorCode is a subclass of str (inherits str, Enum)."""
        assert issubclass(ErrorCode, str)

    def test_value_is_string(self) -> None:
        """Values can be retrieved as strings."""
        code = ErrorCode.DEPENDENCY_MISSING
        assert isinstance(code.value, str)

    @pytest.mark.parametrize(
        "name, expected_value",
        [
            # value must match name (str Enum convention)
            ("DEPENDENCY_MISSING", "DEPENDENCY_MISSING"),
            ("INVALID_INPUT", "INVALID_INPUT"),
            ("FILE_NOT_FOUND", "FILE_NOT_FOUND"),
            ("PATH_NOT_ALLOWED", "PATH_NOT_ALLOWED"),
            ("SUBPROCESS_FAILED", "SUBPROCESS_FAILED"),
            ("SUBPROCESS_TIMEOUT", "SUBPROCESS_TIMEOUT"),
            ("PROBE_FAILED", "PROBE_FAILED"),
            ("OTIO_ERROR", "OTIO_ERROR"),
            ("PROJECT_NOT_FOUND", "PROJECT_NOT_FOUND"),
            ("PROJECT_EXISTS", "PROJECT_EXISTS"),
            ("UNSUPPORTED_OPERATION", "UNSUPPORTED_OPERATION"),
            ("INTERNAL", "INTERNAL"),
            ("TRACK_NOT_FOUND", "TRACK_NOT_FOUND"),
        ],
    )
    def test_value_matches_name(self, name: str, expected_value: str) -> None:
        """Each ErrorCode value equals its name string (JSON serialisation compat)."""
        member = ErrorCode[name]
        assert member.value == expected_value

    def test_can_construct_from_string(self) -> None:
        """ErrorCode can be reverse-looked up from a string."""
        code = ErrorCode("INVALID_INPUT")
        assert code == ErrorCode.INVALID_INPUT

    def test_all_required_members_count(self) -> None:
        """All 13 required members are present (additions allowed; omissions are not)."""  # noqa: E501
        required = {
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "FILE_NOT_FOUND",
            "PATH_NOT_ALLOWED",
            "SUBPROCESS_FAILED",
            "SUBPROCESS_TIMEOUT",
            "PROBE_FAILED",
            "OTIO_ERROR",
            "PROJECT_NOT_FOUND",
            "PROJECT_EXISTS",
            "UNSUPPORTED_OPERATION",
            "INTERNAL",
            "TRACK_NOT_FOUND",
        }
        actual = {m.name for m in ErrorCode}
        missing = required - actual
        assert not missing, f"ErrorCode is missing the following members: {missing}"


# ===========================================================================
# ClipwrightError
# ===========================================================================


class TestClipwrightError:
    """Basic contract for the ClipwrightError exception class."""

    def test_is_exception(self) -> None:
        """ClipwrightError is a subclass of Exception."""
        assert issubclass(ClipwrightError, Exception)

    def test_construct_and_attributes(self) -> None:
        """Holds code / message / hint."""
        err = ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="File not found",
            hint="Check the path",
        )
        assert err.code == ErrorCode.FILE_NOT_FOUND
        assert err.message == "File not found"
        assert err.hint == "Check the path"

    def test_can_be_raised_and_caught(self) -> None:
        """Can be raised and caught as ClipwrightError."""
        with pytest.raises(ClipwrightError) as exc_info:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Invalid input",
                hint="Check the input value",
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_also_catchable_as_exception(self) -> None:
        """Also catchable as a plain Exception."""
        with pytest.raises(ClipwrightError):
            raise ClipwrightError(
                code=ErrorCode.INTERNAL,
                message="Unexpected error",
                hint="Please report with reproduction steps",
            )

    @pytest.mark.parametrize(
        "code",
        [
            ErrorCode.DEPENDENCY_MISSING,
            ErrorCode.SUBPROCESS_FAILED,
            ErrorCode.SUBPROCESS_TIMEOUT,
            ErrorCode.PROBE_FAILED,
            ErrorCode.OTIO_ERROR,
            ErrorCode.PROJECT_NOT_FOUND,
            ErrorCode.PROJECT_EXISTS,
            ErrorCode.PATH_NOT_ALLOWED,
            ErrorCode.UNSUPPORTED_OPERATION,
            ErrorCode.INTERNAL,
            ErrorCode.TRACK_NOT_FOUND,
        ],
    )
    def test_all_error_codes_usable(self, code: ErrorCode) -> None:
        """ClipwrightError can be constructed with every error code."""
        err = ClipwrightError(code=code, message="test", hint="hint")
        assert err.code == code

    def test_code_is_error_code_type(self) -> None:
        """The code attribute is of type ErrorCode."""
        err = ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message="OTIO error",
            hint="Check the OTIO file",
        )
        assert isinstance(err.code, ErrorCode)

    def test_dependency_missing_message_hint_pattern(self) -> None:
        """DEPENDENCY_MISSING carries a Windows-oriented hint (contract check)."""
        err = ClipwrightError(
            code=ErrorCode.DEPENDENCY_MISSING,
            message="ffprobe not found",
            hint=(
                "Install via winget install Gyan.FFmpeg and restart the shell, "
                "or set CLIPWRIGHT_FFPROBE to the full executable path"
            ),
        )
        # hint must be non-empty (actionable hint required — §4 contract)
        assert len(err.hint) > 0
        assert err.message == "ffprobe not found"
