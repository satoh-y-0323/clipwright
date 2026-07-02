"""test_internal_guard.py — INTERNAL exception boundary guard regression test.

Mirrors clipwright_wrap's TestInternalExceptionGuard and clipwright_frames
extract.py's `except Exception` guard pattern (architecture-report
20260702-223529 §1.1 / §3).

Verification points:
  - add_bgm() must convert an unexpected non-ClipwrightError exception
    (a raw OSError raised from save_timeline, the terminal I/O sink) into a
    fixed-wording ErrorCode.INTERNAL result.
  - The injected exception message (which contains a full filesystem path)
    must never leak into the error message or hint (CWE-209 / SR-R-001).

Regression guard: add_bgm() converts the unexpected OSError into
ErrorCode.INTERNAL via an `except Exception` guard placed after its
existing `except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from clipwright.errors import ErrorCode

from clipwright_bgm.schemas import BgmOptions

_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


class TestInternalExceptionGuard:
    """SR-R-001 / CWE-209: unexpected exceptions must not leak full paths."""

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """save_timeline raising OSError(full path) must surface as
        ErrorCode.INTERNAL without leaking the injected path in message/hint.
        """
        import opentimelineio as otio
        from clipwright.otio_utils import save_timeline

        from clipwright_bgm.bgm import add_bgm

        tl = otio.schema.Timeline(name="test_timeline")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)

        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        save_timeline(tl, str(timeline_path))

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_bgm.bgm.inspect_media",
                lambda p: media_info_bgm,
            )
            mp.setattr(
                "clipwright_bgm.bgm.save_timeline",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    OSError(_INJECTED_SECRET_PATH)
                ),
            )
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INTERNAL.value
        assert _INJECTED_SECRET_PATH not in result["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in result["error"].get("hint", "")
