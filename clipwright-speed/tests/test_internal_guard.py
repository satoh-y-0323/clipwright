"""INTERNAL boundary guard regression test for clipwright-speed.

Verifies that set_speed() converts unexpected non-ClipwrightError exceptions
(e.g. a raw OSError raised deep inside save_timeline) into a fixed-wording
ErrorCode.INTERNAL envelope, without leaking the raised exception's message
(which may contain an absolute filesystem path) into the response (CWE-209).

Mirrors clipwright-wrap's TestInternalExceptionGuard pattern. set_speed()
converts the unexpected OSError into ErrorCode.INTERNAL via an
`except Exception` guard placed after its existing
`except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clipwright_speed.schemas import SetSpeedOptions
from clipwright_speed.speed import set_speed

_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


class TestInternalExceptionGuard:
    """set_speed() must convert unexpected exceptions to ErrorCode.INTERNAL
    with a fixed, path-free message/hint at the tool boundary."""

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, simple_timeline_file: Path, tmp_dir: Path, mocker: Any
    ) -> None:
        """save_timeline() raising OSError(full path) must not leak the path
        and must surface as ErrorCode.INTERNAL."""
        mocker.patch(
            "clipwright_speed.speed.save_timeline",
            side_effect=OSError(_INJECTED_SECRET_PATH),
        )

        output = str(tmp_dir / "output.otio")
        options = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), output, options)
        data = result if isinstance(result, dict) else result.model_dump()

        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"]["message"]
        assert _INJECTED_SECRET_PATH not in data["error"]["hint"]
