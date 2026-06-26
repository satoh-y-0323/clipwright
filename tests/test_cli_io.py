"""test_cli_io.py — Tests for the shared UTF-8 I/O helper in core.

Target: clipwright.cli_io.force_utf8_io()
Design: DRY refactor of the per-CLI _force_utf8_io() helper (CR L-2 / SR I-1).
        The shared helper now lives in core as a PUBLIC function
        clipwright.cli_io.force_utf8_io(); wrap_cli and vad_cli import it.

This file is the canonical home for the deterministic behavioral checks that
previously lived (duplicated) in each CLI's test_cli_utf8.py.

Verification perspectives:
  (1) force_utf8_io() reconfigures sys.stdin/sys.stdout to UTF-8.
  (2) force_utf8_io() guards against streams lacking a reconfigure() method
      (e.g. pytest capture objects) without raising AttributeError.

Both streams' .encoding are reconfigured to "utf-8" and the guard is safe.
"""

from __future__ import annotations

import io
import sys


def test_force_utf8_io_reconfigures_stdin_stdout() -> None:
    """Verify force_utf8_io() reconfigures sys.stdin and sys.stdout to UTF-8.

    Steps:
      1. Build fake TextIOWrapper streams with non-UTF-8 encoding (cp932),
         simulating a child process whose stdio was inherited as cp932.
      2. Monkeypatch sys.stdin/sys.stdout to those fake streams.
      3. Call clipwright.cli_io.force_utf8_io().
      4. Assert both streams' .encoding attribute is now "utf-8".
    """
    from clipwright import cli_io

    assert hasattr(cli_io, "force_utf8_io"), (
        "clipwright.cli_io has no force_utf8_io() function"
    )
    assert callable(cli_io.force_utf8_io), "force_utf8_io is not callable"

    # Fake streams starting at cp932; reconfigure() must flip them to utf-8.
    fake_stdin = io.TextIOWrapper(io.BytesIO(), encoding="cp932")
    fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp932")

    assert fake_stdin.encoding == "cp932", (
        "test setup: fake_stdin encoding should start as cp932"
    )
    assert fake_stdout.encoding == "cp932", (
        "test setup: fake_stdout encoding should start as cp932"
    )

    orig_stdin = sys.stdin
    orig_stdout = sys.stdout
    try:
        sys.stdin = fake_stdin  # type: ignore[assignment]
        sys.stdout = fake_stdout  # type: ignore[assignment]

        cli_io.force_utf8_io()

        assert sys.stdin.encoding == "utf-8", (
            f"sys.stdin.encoding should be 'utf-8' after force_utf8_io(), "
            f"got {sys.stdin.encoding!r}"
        )
        assert sys.stdout.encoding == "utf-8", (
            f"sys.stdout.encoding should be 'utf-8' after force_utf8_io(), "
            f"got {sys.stdout.encoding!r}"
        )
    finally:
        sys.stdin = orig_stdin
        sys.stdout = orig_stdout


def test_force_utf8_io_guards_against_missing_reconfigure() -> None:
    """Verify force_utf8_io() tolerates streams without a reconfigure() method.

    Guard handling: pytest may replace sys.stdout with a capture object that has
    no reconfigure() method. force_utf8_io() must use a getattr() guard so that
    calling it in that situation does not raise AttributeError.

    Calling force_utf8_io() against capture-like streams is safe.
    """
    from clipwright import cli_io

    class FakeStreamNoReconfigure:
        encoding: str = "utf-8"

    fake_stream = FakeStreamNoReconfigure()

    orig_stdin = sys.stdin
    orig_stdout = sys.stdout
    try:
        sys.stdin = fake_stream  # type: ignore[assignment]
        sys.stdout = fake_stream  # type: ignore[assignment]

        # Must not raise AttributeError thanks to the getattr() guard.
        cli_io.force_utf8_io()

        assert True, "force_utf8_io() handled missing reconfigure() without error"
    finally:
        sys.stdin = orig_stdin
        sys.stdout = orig_stdout
