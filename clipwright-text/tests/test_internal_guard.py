"""test_internal_guard.py — INTERNAL exception boundary guard regression test.

Mirrors clipwright_wrap's TestInternalExceptionGuard and clipwright_frames
extract.py's `except Exception` guard pattern (architecture-report
20260702-223529 §1.1 / §3).

Verification points:
  - add_text() must convert an unexpected non-ClipwrightError exception
    (a raw OSError raised from save_timeline, the terminal I/O sink) into a
    fixed-wording ErrorCode.INTERNAL result.
  - The injected exception message (which contains a full filesystem path)
    must never leak into the error message or hint (CWE-209 / SR-R-001).

Regression guard: add_text() converts the injected OSError into a fixed,
path-free ErrorCode.INTERNAL result via an `except Exception` guard after its
existing `except ClipwrightError` block; this test locks that behavior in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clipwright_text.schemas import AddTextOptions  # type: ignore[import-not-found]

_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


class TestInternalExceptionGuard:
    """SR-R-001 / CWE-209: unexpected exceptions must not leak full paths."""

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, timeline_file: Path, output_path: Path
    ) -> None:
        """save_timeline raising OSError(full path) must surface as
        ErrorCode.INTERNAL without leaking the injected path in message/hint.

        options is a valid AddTextOptions (text/start_sec/duration_sec set)
        so the options-is-None early return (before the try block) is not
        triggered; execution reaches the try/except boundary and calls
        save_timeline at the end of the normal (non-idempotent) path.
        """
        from clipwright_text.text import add_text  # type: ignore[import-not-found]

        opts = AddTextOptions(text="hello world", start_sec=1.0, duration_sec=2.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_text.text.save_timeline",
                lambda *args: (_ for _ in ()).throw(OSError(_INJECTED_SECRET_PATH)),
            )
            result = add_text(
                timeline=str(timeline_file),
                output=str(output_path),
                options=opts,
            )

        data: dict[str, Any] = result.model_dump()
        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in data["error"].get("hint", "")
