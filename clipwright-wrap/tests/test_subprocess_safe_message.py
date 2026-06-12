"""test_subprocess_safe_message.py — Red tests pinning wrap's subprocess-error message to core.

TDD Red wave (round 4, SR I-1 / CR-M-001).

These tests assert that:
  1. wrap.py does NOT define a module-level local _SUBPROCESS_SAFE_MESSAGE (the local
     copy must be removed in favour of the shared core import).  This is the primary
     Red signal today.
  2. The three subprocess-failure/timeout sites in wrap.py emit messages that are
     IDENTICAL to the shared core constant SUBPROCESS_SAFE_MESSAGE (imported from
     clipwright.process), and that no absolute path leaks into the envelope.

Sites covered:
  - Timeout site (wrap.py:199):  message == f"{SUBPROCESS_SAFE_MESSAGE} (timeout)"
  - OSError launch site (wrap.py:208):  message == SUBPROCESS_SAFE_MESSAGE (bare)
  - JSON-parse-failure site (wrap.py:221):  message == SUBPROCESS_SAFE_MESSAGE (bare)

Red today because wrap.py still defines its own local _SUBPROCESS_SAFE_MESSAGE and does
NOT import the shared core constant.

Why value-equality alone is NOT enough to produce a genuine Red:
  wrap's local constant value "internal subprocess failed" is byte-identical to the core
  constant, so string comparison passes even before the migration.  Therefore the primary
  Red assertion checks that the local symbol no longer exists in the wrap module.
  The envelope message assertions (Sites 1-3) serve as post-migration contract checks.

Mocking strategy: patch 'clipwright_wrap.wrap.subprocess.run' (the subprocess.run that
wrap.py calls directly for wrap_cli) to raise TimeoutExpired / OSError or to return
invalid JSON, then call wrap_captions() and inspect the returned error envelope.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Import the shared core constant — this is the reference value for all assertions.
from clipwright.process import SUBPROCESS_SAFE_MESSAGE

import clipwright_wrap.wrap as _wrap_mod
from clipwright_wrap.schemas import WrapCaptionsOptions
from clipwright_wrap.wrap import wrap_captions

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _srt_1cue(text: str = "hello world") -> str:
    """Return a minimal 1-cue SRT string."""
    return f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n"


def _opts() -> WrapCaptionsOptions:
    """Return default WrapCaptionsOptions for Japanese."""
    return WrapCaptionsOptions(language="ja")


# ---------------------------------------------------------------------------
# Genuine Red assertion: local _SUBPROCESS_SAFE_MESSAGE must NOT exist in wrap module
# ---------------------------------------------------------------------------


def test_wrap_module_has_no_local_subprocess_safe_message() -> None:
    """wrap.py must NOT define a module-level _SUBPROCESS_SAFE_MESSAGE after migration.

    This is the primary deterministic Red signal today.  wrap.py currently defines:
        _SUBPROCESS_SAFE_MESSAGE = "internal subprocess failed"   (wrap.py:41)
    After impl-wrap that local symbol is deleted and the core import is used instead.

    Why this assertion is necessary (prior lesson):
        The local constant value is byte-identical to the core constant, so
        message-equality checks pass even BEFORE the migration.  This test catches the
        structural change (removal of the local copy) that message-equality cannot see.
    """
    assert not hasattr(_wrap_mod, "_SUBPROCESS_SAFE_MESSAGE"), (
        "wrap.py still defines a module-level _SUBPROCESS_SAFE_MESSAGE. "
        "Remove it and import SUBPROCESS_SAFE_MESSAGE from clipwright.process instead."
    )


# ---------------------------------------------------------------------------
# Site 1: Timeout → message == f"{SUBPROCESS_SAFE_MESSAGE} (timeout)"
# ---------------------------------------------------------------------------


def test_timeout_message_equals_core_constant_with_timeout_suffix(
    tmp_path: Path,
) -> None:
    """Timeout site emits exactly f'{SUBPROCESS_SAFE_MESSAGE} (timeout)'.

    Drives the subprocess.TimeoutExpired branch (wrap.py:196-204) by making
    subprocess.run raise TimeoutExpired, then asserts the returned error envelope
    message equals the shared core constant with the '(timeout)' suffix.

    Red reason: wrap.py uses its own local _SUBPROCESS_SAFE_MESSAGE, not the core
    import.  After impl-wrap the local copy is removed and core is imported.
    """
    # Arrange — write a real SRT file so wrap_captions can proceed to the subprocess step
    srt_file = tmp_path / "input.srt"
    out_file = tmp_path / "output.srt"
    srt_file.write_text(_srt_1cue(), encoding="utf-8")

    # Act — patch subprocess.run inside the wrap module to raise TimeoutExpired
    with patch("clipwright_wrap.wrap.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=[sys.executable, "-m", "clipwright_wrap.wrap_cli"],
            timeout=30.0,
        )
        result: dict[str, Any] = wrap_captions(str(srt_file), str(out_file), _opts())

    # Assert — envelope must carry the sanitised message, not raw stderr/path
    assert result["ok"] is False, "Expected error envelope for timeout"
    msg: str = result["error"]["message"]

    expected_message = f"{SUBPROCESS_SAFE_MESSAGE} (timeout)"
    assert msg == expected_message, (
        f"Timeout message {msg!r} != expected {expected_message!r}. "
        "wrap.py must use the core SUBPROCESS_SAFE_MESSAGE constant."
    )

    # No absolute path must leak into the message
    assert str(srt_file) not in msg, (
        f"Absolute path leaked into timeout message: {msg!r}"
    )
    assert str(tmp_path) not in msg, (
        f"Absolute path leaked into timeout message: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Site 2: OSError (launch failure) → message == SUBPROCESS_SAFE_MESSAGE (bare)
# ---------------------------------------------------------------------------


def test_oserror_message_equals_core_constant_bare(tmp_path: Path) -> None:
    """OSError launch-failure site emits exactly SUBPROCESS_SAFE_MESSAGE (bare).

    Drives the OSError branch (wrap.py:205-213) by making subprocess.run raise
    OSError, then asserts the returned error envelope message equals the bare
    shared core constant (no suffix).

    Red reason: same as above — local copy in wrap.py is not the core import.
    """
    # Arrange
    srt_file = tmp_path / "input.srt"
    out_file = tmp_path / "output.srt"
    srt_file.write_text(_srt_1cue(), encoding="utf-8")

    # Act — patch subprocess.run to raise OSError (e.g. executable not found)
    with patch("clipwright_wrap.wrap.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("No such file or directory: /fake/python")
        result = wrap_captions(str(srt_file), str(out_file), _opts())

    # Assert
    assert result["ok"] is False, "Expected error envelope for OSError"
    msg = result["error"]["message"]

    assert msg == SUBPROCESS_SAFE_MESSAGE, (
        f"OSError message {msg!r} != expected {SUBPROCESS_SAFE_MESSAGE!r}. "
        "wrap.py must use the core SUBPROCESS_SAFE_MESSAGE constant."
    )

    # No absolute path must leak
    assert str(srt_file) not in msg, (
        f"Absolute path leaked into OSError message: {msg!r}"
    )
    assert str(tmp_path) not in msg, (
        f"Absolute path leaked into OSError message: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Site 3: JSON parse failure → message == SUBPROCESS_SAFE_MESSAGE (bare)
# ---------------------------------------------------------------------------


def test_json_parse_failure_message_equals_core_constant_bare(tmp_path: Path) -> None:
    """JSON-parse-failure site emits exactly SUBPROCESS_SAFE_MESSAGE (bare).

    Drives the json.JSONDecodeError branch (wrap.py:216-223) by making
    subprocess.run return a CompletedProcess with invalid JSON stdout, then asserts
    the returned error envelope message equals the bare shared core constant.

    Red reason: same as above — local copy in wrap.py is not the core import.
    """
    # Arrange
    srt_file = tmp_path / "input.srt"
    out_file = tmp_path / "output.srt"
    srt_file.write_text(_srt_1cue(), encoding="utf-8")

    # Build a fake CompletedProcess with corrupt JSON output
    fake_proc = MagicMock(spec=subprocess.CompletedProcess)
    fake_proc.returncode = 0
    fake_proc.stdout = "NOT VALID JSON {{{"
    fake_proc.stderr = ""

    # Act — patch subprocess.run to return the corrupt-output process
    with patch("clipwright_wrap.wrap.subprocess.run", return_value=fake_proc):
        result = wrap_captions(str(srt_file), str(out_file), _opts())

    # Assert
    assert result["ok"] is False, "Expected error envelope for JSON parse failure"
    msg = result["error"]["message"]

    assert msg == SUBPROCESS_SAFE_MESSAGE, (
        f"JSON-parse-failure message {msg!r} != expected {SUBPROCESS_SAFE_MESSAGE!r}. "
        "wrap.py must use the core SUBPROCESS_SAFE_MESSAGE constant."
    )

    # No absolute path must leak
    assert str(srt_file) not in msg, (
        f"Absolute path leaked into JSON-parse-failure message: {msg!r}"
    )
    assert str(tmp_path) not in msg, (
        f"Absolute path leaked into JSON-parse-failure message: {msg!r}"
    )
