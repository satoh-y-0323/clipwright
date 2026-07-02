"""test_internal_guard.py — INTERNAL boundary guard regression test for trim_media.

Mirrors clipwright-wrap's TestInternalExceptionGuard: an unexpected non-
ClipwrightError exception raised by a terminal IO call (save_timeline) must be
converted to ErrorCode.INTERNAL with fixed wording, without leaking the
injected full path via message/hint (CWE-209 / SR-R-001).

Regression guard: trim_media() converts the unexpected OSError into
ErrorCode.INTERNAL via an `except Exception` guard placed after its
existing `except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_trim.schemas import TrimOptions, TrimRange
from clipwright_trim.trim import trim_media

FPS = 30.0
DURATION_SEC = 10.0

_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_media_info(path: str) -> MediaInfo:
    """Construct a synthetic MediaInfo for mocking inspect_media (normal path)."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    duration = RationalTimeModel(value=DURATION_SEC * FPS, rate=FPS)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _keep_opts() -> TrimOptions:
    return TrimOptions(keep=[TrimRange(start_sec=2.0, end_sec=5.0)])


class TestInternalExceptionGuard:
    """SR-R-001 / CWE-209: unexpected exceptions must not leak absolute paths.

    Regression guard: trim_media() must convert an unexpected OSError raised
    by save_timeline() into ErrorCode.INTERNAL with fixed wording so that the
    injected full path is never exposed via message/hint.
    """

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        """save_timeline() raising OSError(full path) must not leak the path
        and must surface as ErrorCode.INTERNAL (SR-R-001)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with (
            patch(
                "clipwright_trim.trim.inspect_media",
                return_value=_make_media_info(path=media),
            ),
            patch(
                "clipwright_trim.trim.save_timeline",
                side_effect=OSError(_INJECTED_SECRET_PATH),
            ),
        ):
            result = trim_media(media, output, _keep_opts())

        data: dict[str, Any] = (
            result if isinstance(result, dict) else result.model_dump()
        )

        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        message = data["error"].get("message", "")
        hint = data["error"].get("hint", "")
        assert _INJECTED_SECRET_PATH not in message
        assert _INJECTED_SECRET_PATH not in hint
