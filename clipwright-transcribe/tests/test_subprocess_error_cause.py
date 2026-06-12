"""test_subprocess_error_cause.py — Red tests for exception-chain hardening (SR-R-001 [NL-1]).

Pins that the two `raise _sanitize_subprocess_error(exc) from exc` sites in
_run_whisper() emit `__cause__ is None` after the planned change to `from None`.

RED TODAY: both sites use `from exc`, so __cause__ is the original ClipwrightError.
GREEN AFTER IMPL: `from None` makes __cause__ None (PEP 3134).

Sites under test:
  - L220 (ffmpeg WAV extraction path): _extract_wav raises ClipwrightError
  - L226 (whisper run path): run() raises ClipwrightError after _extract_wav succeeds

Mock strategy follows existing TestRunWhisperAdapter in test_transcribe.py:
  - Patch module-level `resolve_tool` to return dummy paths.
  - Patch module-level `_extract_wav` (ffmpeg site) or `run` (whisper site).
  - No real binaries are invoked.
"""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import _run_whisper

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEAKED_PATH = "/secret/abs/path/to/media.mp4"


def _fake_resolve(name: str, env_var: str | None = None) -> str:
    """Dummy resolve_tool that returns a plausible path without hitting the FS."""
    return f"/bin/{name}"


def _opts() -> TranscribeOptions:
    return TranscribeOptions()


# ---------------------------------------------------------------------------
# Site 1: ffmpeg WAV extraction path (L220)
# ---------------------------------------------------------------------------


class TestFfmpegSiteCause:
    """_extract_wav raises -> _sanitize_subprocess_error(exc) must be raised from None."""

    def test_ffmpeg_cause_is_none(self) -> None:
        """__cause__ of the sanitised error must be None (Red: currently the original exc).

        AAA:
          Arrange: mock resolve_tool + _extract_wav to raise a path-leaking ClipwrightError.
          Act:     call _run_whisper inside pytest.raises.
          Assert:  __cause__ is None (fails today because `from exc` keeps the original).
        """
        ffmpeg_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=f"ffmpeg failed: {_LEAKED_PATH}: exit 1",
            hint="check ffmpeg",
        )

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_transcribe.transcribe._extract_wav",
                side_effect=ffmpeg_exc,
            ),
            pytest.raises(ClipwrightError) as excinfo,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")

        # Red assertion: currently fails because __cause__ == ffmpeg_exc (not None)
        assert excinfo.value.__cause__ is None

    def test_ffmpeg_site_code_preserved(self) -> None:
        """Sanitised error at ffmpeg site retains SUBPROCESS_FAILED code.

        This assertion must be Green both before and after the impl wave — it guards
        that _sanitize_subprocess_error does not drop the error code.
        """
        ffmpeg_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=f"ffmpeg failed: {_LEAKED_PATH}: exit 1",
            hint="check ffmpeg",
        )

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_transcribe.transcribe._extract_wav",
                side_effect=ffmpeg_exc,
            ),
            pytest.raises(ClipwrightError) as excinfo,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")

        assert excinfo.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_ffmpeg_site_no_path_leak(self) -> None:
        """Sanitised error at ffmpeg site must not expose the leaked absolute path.

        Guards against regression: even after `from None`, the message must remain
        sanitised (no raw path in the user-visible error).
        """
        ffmpeg_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=f"ffmpeg failed: {_LEAKED_PATH}: exit 1",
            hint="check ffmpeg",
        )

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_transcribe.transcribe._extract_wav",
                side_effect=ffmpeg_exc,
            ),
            pytest.raises(ClipwrightError) as excinfo,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")

        assert _LEAKED_PATH not in excinfo.value.message


# ---------------------------------------------------------------------------
# Site 2: whisper run path (L226)
# ---------------------------------------------------------------------------


def _make_extract_wav_success() -> Any:
    """Return a no-op _extract_wav mock (ffmpeg site succeeds)."""

    def _impl(ffmpeg: str, media: str, output_path: str, timeout: float) -> None:
        # Write nothing; the WAV path existence is not checked before whisper runs.
        return None

    return _impl


class TestWhisperSiteCause:
    """run() raises for whisper -> _sanitize_subprocess_error(exc) must be raised from None."""

    def test_whisper_cause_is_none(self) -> None:
        """__cause__ of the sanitised error must be None (Red: currently the original exc).

        AAA:
          Arrange: mock resolve_tool + _extract_wav (success) + run to raise
                   a path-leaking ClipwrightError on the whisper invocation.
          Act:     call _run_whisper inside pytest.raises.
          Assert:  __cause__ is None (fails today because `from exc` keeps the original).
        """
        whisper_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"timeout waiting for whisper: {_LEAKED_PATH}: 300s exceeded",
            hint="try a shorter clip",
        )

        def _raise_on_whisper(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:  # whisper invocation
                raise whisper_exc
            # Any other run call (none expected here) succeeds
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_transcribe.transcribe._extract_wav",
                side_effect=_make_extract_wav_success(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=_raise_on_whisper,
            ),
            pytest.raises(ClipwrightError) as excinfo,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")

        # Red assertion: currently fails because __cause__ == whisper_exc (not None)
        assert excinfo.value.__cause__ is None

    def test_whisper_site_code_preserved(self) -> None:
        """Sanitised error at whisper site retains SUBPROCESS_TIMEOUT code."""
        whisper_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"timeout: {_LEAKED_PATH}",
            hint="try a shorter clip",
        )

        def _raise_on_whisper(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                raise whisper_exc
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_transcribe.transcribe._extract_wav",
                side_effect=_make_extract_wav_success(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=_raise_on_whisper,
            ),
            pytest.raises(ClipwrightError) as excinfo,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")

        assert excinfo.value.code == ErrorCode.SUBPROCESS_TIMEOUT

    def test_whisper_site_no_path_leak(self) -> None:
        """Sanitised error at whisper site must not expose the leaked absolute path."""
        whisper_exc = ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"timeout: {_LEAKED_PATH}",
            hint="try a shorter clip",
        )

        def _raise_on_whisper(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                raise whisper_exc
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_transcribe.transcribe._extract_wav",
                side_effect=_make_extract_wav_success(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=_raise_on_whisper,
            ),
            pytest.raises(ClipwrightError) as excinfo,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")

        assert _LEAKED_PATH not in excinfo.value.message
