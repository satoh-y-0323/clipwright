"""test_subprocess_safe_message.py — Tests for SUBPROCESS_SAFE_MESSAGE DRY consolidation.

Pins transcribe's _sanitize_subprocess_error output to the SHARED core helper
safe_subprocess_message (SR I-1 / CR-M-001 round 4).

Verifies that:
  - _sanitize_subprocess_error uses safe_subprocess_message from clipwright.process.
  - No local _SUBPROCESS_SAFE_MESSAGE constant is defined in clipwright_transcribe.transcribe.
"""

from __future__ import annotations

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import SUBPROCESS_SAFE_MESSAGE, safe_subprocess_message

import clipwright_transcribe.transcribe as transcribe_module
from clipwright_transcribe.transcribe import _sanitize_subprocess_error


def _make_exc(
    code: ErrorCode, *, path: str = "/abs/path/to/media.mp4"
) -> ClipwrightError:
    """Build a ClipwrightError that simulates a raw subprocess failure message.

    The message intentionally contains an absolute path so the no-path-leak
    assertion is load-bearing.
    """
    return ClipwrightError(
        code=code,
        message=f"subprocess failed: {path}: exit status 1",
        hint="try again",
    )


class TestSanitizeSubprocessError:
    """Verify _sanitize_subprocess_error produces the SHARED core helper output."""

    def test_subprocess_failed_equals_safe_message(self) -> None:
        """SUBPROCESS_FAILED site emits a message equal to safe_subprocess_message(exc)."""
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED)
        sanitised = _sanitize_subprocess_error(exc)
        expected = safe_subprocess_message(sanitised)
        assert sanitised.message == expected

    def test_subprocess_timeout_equals_safe_message(self) -> None:
        """SUBPROCESS_TIMEOUT site emits a message equal to safe_subprocess_message(exc)."""
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT)
        sanitised = _sanitize_subprocess_error(exc)
        expected = safe_subprocess_message(sanitised)
        assert sanitised.message == expected

    def test_subprocess_failed_contains_core_constant(self) -> None:
        """Sanitised SUBPROCESS_FAILED message starts with the core SUBPROCESS_SAFE_MESSAGE."""
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED)
        sanitised = _sanitize_subprocess_error(exc)
        assert sanitised.message.startswith(SUBPROCESS_SAFE_MESSAGE)

    def test_subprocess_timeout_contains_core_constant(self) -> None:
        """Sanitised SUBPROCESS_TIMEOUT message starts with the core SUBPROCESS_SAFE_MESSAGE."""
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT)
        sanitised = _sanitize_subprocess_error(exc)
        assert sanitised.message.startswith(SUBPROCESS_SAFE_MESSAGE)

    def test_no_absolute_path_leak_failed(self) -> None:
        """Sanitised SUBPROCESS_FAILED message must not contain the raw absolute path."""
        abs_path = "/abs/path/to/media.mp4"
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED, path=abs_path)
        sanitised = _sanitize_subprocess_error(exc)
        assert abs_path not in sanitised.message

    def test_no_absolute_path_leak_timeout(self) -> None:
        """Sanitised SUBPROCESS_TIMEOUT message must not contain the raw absolute path."""
        abs_path = "/abs/path/to/media.mp4"
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT, path=abs_path)
        sanitised = _sanitize_subprocess_error(exc)
        assert abs_path not in sanitised.message

    def test_other_code_unchanged(self) -> None:
        """Errors with codes other than SUBPROCESS_FAILED/TIMEOUT are returned unchanged."""
        exc = ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="file /path/file.mp4 not found",
            hint="check the path",
        )
        result = _sanitize_subprocess_error(exc)
        assert result is exc


class TestNoLocalSubprocessSafeMessage:
    """Assert that the local _SUBPROCESS_SAFE_MESSAGE copy does NOT exist.

    Pins that the DRY consolidation (SR I-1 / CR-M-001) is complete:
    clipwright_transcribe.transcribe must use safe_subprocess_message from
    clipwright.process and must not define a redundant local constant.
    """

    def test_no_local_subprocess_safe_message(self) -> None:
        """clipwright_transcribe.transcribe must NOT define a module-level _SUBPROCESS_SAFE_MESSAGE.

        The presence of this attribute indicates the module still uses a local copy
        rather than the shared core constant, violating SR I-1 / CR-M-001.
        """
        assert not hasattr(transcribe_module, "_SUBPROCESS_SAFE_MESSAGE"), (
            "clipwright_transcribe.transcribe still defines a local _SUBPROCESS_SAFE_MESSAGE. "
            "Remove it and use `from clipwright.process import safe_subprocess_message` instead."
        )
