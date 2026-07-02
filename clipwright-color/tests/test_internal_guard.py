"""test_internal_guard.py — INTERNAL exception boundary guard regression test.

Mirrors clipwright_wrap's TestInternalExceptionGuard and clipwright_frames
extract.py's `except Exception` guard pattern (architecture-report
20260702-223529 §1.1 / §3).

Verification points:
  - detect_color() must convert an unexpected non-ClipwrightError exception
    (a raw OSError raised from save_timeline, the terminal I/O sink) into a
    fixed-wording ErrorCode.INTERNAL result.
  - The injected exception message (which contains a full filesystem path)
    must never leak into the error message or hint (CWE-209 / SR-R-001).

Regression guard: detect_color() converts the unexpected OSError into
ErrorCode.INTERNAL via an `except Exception` guard placed after its
existing `except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_color.schemas import (
    DetectColorOptions,  # type: ignore[import-not-found]
)

FPS = 30.0
_TEST_BIT_RATE = 8_000_000
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_media_info(path: str) -> MediaInfo:
    """Construct a minimal MediaInfo (video + audio streams) for testing."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=10.0 * FPS, rate=FPS),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


def _fake_measured() -> dict[str, Any]:
    """Return a fake measure_brightness result dict (happy-path shape)."""
    return {
        "measured": {
            "yavg": 96.4,
            "ymin": 9.0,
            "ymax": 242.0,
            "sampled_frames": 12,
        },
        "warnings": [],
    }


class TestInternalExceptionGuard:
    """SR-R-001 / CWE-209: unexpected exceptions must not leak full paths."""

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        """save_timeline raising OSError(full path) must surface as
        ErrorCode.INTERNAL without leaking the injected path in message/hint.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(),
            )
            mp.setattr(
                "clipwright_color.color.save_timeline",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    OSError(_INJECTED_SECRET_PATH)
                ),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INTERNAL.value
        assert _INJECTED_SECRET_PATH not in result["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in result["error"].get("hint", "")
