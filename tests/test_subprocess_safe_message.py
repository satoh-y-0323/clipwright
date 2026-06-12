"""test_subprocess_safe_message.py — Red tests for SR-NEW / SR L-1 DRY fix.

Targets the two new public symbols to be added to clipwright.process:

  SUBPROCESS_SAFE_MESSAGE  (module-level constant)
  safe_subprocess_message(exc) -> str  (helper function)

These consolidate the duplicated _SUBPROCESS_SAFE_MESSAGE string that currently
lives independently in detect.py:44 and vad_cli.py:39 (SR I-1 [SR-NEW]).

Design:
  - Neither symbol exists in process.py today → ImportError on every test →
    genuine deterministic Red.
  - Once the developer adds the constant and helper to process.py the tests
    become Green.

Red classification: deterministic-Red (ImportError — symbols not yet exported).
"""

from __future__ import annotations

from clipwright.errors import ClipwrightError, ErrorCode

# ---------------------------------------------------------------------------
# Import the two new public symbols.
# Both imports fail today (process.py does not expose them yet) so every test
# in this file is deterministic-Red.
# ---------------------------------------------------------------------------
from clipwright.process import SUBPROCESS_SAFE_MESSAGE, safe_subprocess_message

# ===========================================================================
# SUBPROCESS_SAFE_MESSAGE constant
# ===========================================================================


class TestSubprocessSafeMessageConstant:
    """Verify the canonical value of SUBPROCESS_SAFE_MESSAGE."""

    def test_constant_value_is_internal_subprocess_failed(self) -> None:
        """SUBPROCESS_SAFE_MESSAGE must equal 'internal subprocess failed'.

        This exact string is already used by detect.py:44 and vad_cli.py:39.
        The constant must not change the established value on consolidation.

        Arrange: import the constant from clipwright.process.
        Act:     read the value.
        Assert:  value == "internal subprocess failed".
        """
        assert SUBPROCESS_SAFE_MESSAGE == "internal subprocess failed"

    def test_constant_is_a_string(self) -> None:
        """SUBPROCESS_SAFE_MESSAGE must be a str (JSON-serialisable)."""
        assert isinstance(SUBPROCESS_SAFE_MESSAGE, str)

    def test_constant_is_not_empty(self) -> None:
        """SUBPROCESS_SAFE_MESSAGE must be non-empty (a genuinely informative token)."""
        assert len(SUBPROCESS_SAFE_MESSAGE) > 0


# ===========================================================================
# safe_subprocess_message helper — SUBPROCESS_FAILED variant
# ===========================================================================


class TestSafeSubprocessMessageSubprocessFailed:
    """safe_subprocess_message(exc) returns the safe message for SUBPROCESS_FAILED."""

    def _make_subprocess_failed_exc(
        self, message: str = "ffmpeg failed: /abs/secret/x.mp4 No such file"
    ) -> ClipwrightError:
        """Build a ClipwrightError(SUBPROCESS_FAILED) with a path-embedding message.

        Mirrors the ClipwrightError that core run() raises on a non-zero ffmpeg exit
        (process.py:118-127): the message is built from raw stderr which can contain
        absolute input paths.
        """
        return ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=message,
            hint="Check the command arguments, input file path, and tool version.",
        )

    def test_returns_safe_message_constant_prefix(self) -> None:
        """safe_subprocess_message returns a string containing SUBPROCESS_SAFE_MESSAGE.

        Arrange: ClipwrightError(SUBPROCESS_FAILED, message with absolute path).
        Act:     call safe_subprocess_message(exc).
        Assert:  returned string contains SUBPROCESS_SAFE_MESSAGE.
        """
        exc = self._make_subprocess_failed_exc()
        result = safe_subprocess_message(exc)
        assert SUBPROCESS_SAFE_MESSAGE in result

    def test_returns_exact_format_code_suffix(self) -> None:
        """safe_subprocess_message returns the safe message with code suffix.

        Format: f"{SUBPROCESS_SAFE_MESSAGE} (code: {exc.code})".

        Arrange: ClipwrightError(SUBPROCESS_FAILED).
        Act:     call safe_subprocess_message(exc).
        Assert:  result == SUBPROCESS_SAFE_MESSAGE + " (code: " + str(SUBPROCESS_FAILED)
        """
        exc = self._make_subprocess_failed_exc()
        expected = f"{SUBPROCESS_SAFE_MESSAGE} (code: {ErrorCode.SUBPROCESS_FAILED})"
        result = safe_subprocess_message(exc)
        assert result == expected

    def test_does_not_contain_absolute_path(self) -> None:
        """safe_subprocess_message must NOT include the absolute path from exc.message.

        This is the core security property: raw ffmpeg stderr (which can contain
        the absolute input path) must not surface in the safe message.

        Arrange: ClipwrightError whose message embeds '/abs/secret/x.mp4'.
        Act:     call safe_subprocess_message(exc).
        Assert:  '/abs/secret/x.mp4' is NOT in the returned string.
        """
        leaked_path = "/abs/secret/x.mp4"
        exc = self._make_subprocess_failed_exc(
            message=f"ffmpeg failed: {leaked_path} No such file or directory"
        )
        result = safe_subprocess_message(exc)
        assert leaked_path not in result, (
            f"Absolute path {leaked_path!r} must not appear in the safe message; "
            "safe_subprocess_message must return only the generic token."
        )

    def test_returns_string_type(self) -> None:
        """safe_subprocess_message always returns str."""
        exc = self._make_subprocess_failed_exc()
        result = safe_subprocess_message(exc)
        assert isinstance(result, str)


# ===========================================================================
# safe_subprocess_message helper — SUBPROCESS_TIMEOUT variant
# ===========================================================================


class TestSafeSubprocessMessageSubprocessTimeout:
    """safe_subprocess_message(exc) returns the safe message for SUBPROCESS_TIMEOUT."""

    def _make_subprocess_timeout_exc(self) -> ClipwrightError:
        """Build a ClipwrightError(SUBPROCESS_TIMEOUT).

        Mirrors what core run() raises on TimeoutExpired (process.py:110-116).
        The message includes the tool name but not a media absolute path in most
        cases; however the same safe-message policy applies for consistency.
        """
        return ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message="Command timed out after 60.0 seconds: /abs/secret/path/ffmpeg",
            hint="Increase the timeout value or check the size of the input file.",
        )

    def test_timeout_returns_safe_message_constant_prefix(self) -> None:
        """safe_subprocess_message includes SUBPROCESS_SAFE_MESSAGE for TIMEOUT variant.

        Arrange: ClipwrightError(SUBPROCESS_TIMEOUT).
        Act:     call safe_subprocess_message(exc).
        Assert:  returned string contains SUBPROCESS_SAFE_MESSAGE.
        """
        exc = self._make_subprocess_timeout_exc()
        result = safe_subprocess_message(exc)
        assert SUBPROCESS_SAFE_MESSAGE in result

    def test_timeout_returns_exact_format_code_suffix(self) -> None:
        """safe_subprocess_message returns the safe message with code suffix (TIMEOUT).

        Format: f"{SUBPROCESS_SAFE_MESSAGE} (code: {exc.code})".

        Arrange: ClipwrightError(SUBPROCESS_TIMEOUT).
        Act:     call safe_subprocess_message(exc).
        Assert:  result == SUBPROCESS_SAFE_MESSAGE + " (code: SUBPROCESS_TIMEOUT)"
        """
        exc = self._make_subprocess_timeout_exc()
        expected = f"{SUBPROCESS_SAFE_MESSAGE} (code: {ErrorCode.SUBPROCESS_TIMEOUT})"
        result = safe_subprocess_message(exc)
        assert result == expected

    def test_timeout_does_not_contain_leaked_path(self) -> None:
        """safe_subprocess_message must not include any path from the timeout message.

        Arrange: ClipwrightError(SUBPROCESS_TIMEOUT) whose message embeds an
                 absolute path (edge case: tool binary is at an absolute path).
        Act:     call safe_subprocess_message(exc).
        Assert:  absolute path is NOT in the returned string.
        """
        leaked_path = "/abs/secret/path/ffmpeg"
        exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"Command timed out after 60.0 seconds: {leaked_path}",
            hint="Increase the timeout value or check the size of the input file.",
        )
        result = safe_subprocess_message(exc)
        assert leaked_path not in result, (
            f"Absolute path {leaked_path!r} must not appear in the timeout "
            "safe message."
        )
