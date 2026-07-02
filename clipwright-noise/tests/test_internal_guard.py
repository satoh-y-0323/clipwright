"""test_internal_guard.py — INTERNAL boundary guard regression test for detect_noise.

Architecture: architecture-report-20260702-223529.md §1.1 / §3 (systen A, standard
error_result-direct boundary). Mirrors clipwright_wrap's TestInternalExceptionGuard
and clipwright_frames.extract.extract_frames()'s `except Exception` guard.

This test injects a raw OSError (not wrapped in ClipwrightError) at the terminal
save_timeline() IO call inside detect_noise()'s success path, and verifies that:
  - the boundary converts it into ErrorCode.INTERNAL (not an unhandled traceback)
  - the injected absolute path never leaks into the error message/hint (CWE-209)

Regression guard: detect_noise() converts the unexpected OSError into
ErrorCode.INTERNAL via an `except Exception` guard placed after its
existing `except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo, ToolResult

from clipwright_noise.schemas import DetectNoiseOptions

FPS = 30.0
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"
_FAKE_MEASURE_RESULT = {
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
    "measured_noise_floor_db": -50.0,
    "warnings": [],
}


def _make_media_info(path: str) -> MediaInfo:
    """Minimal MediaInfo with both video and audio streams."""
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=10.0 * FPS, rate=FPS),
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
        bit_rate=8_000_000,
    )


def _d(result: ToolResult) -> dict:  # type: ignore[type-arg]
    return result.model_dump()


class TestInternalExceptionGuard:
    """Regression guard: detect_noise() must convert unexpected exceptions to
    ErrorCode.INTERNAL with fixed wording so that internal paths are never
    exposed via a raw OSError (CWE-209).
    """

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        """save_timeline() raising OSError(full path) must not leak the path
        and must surface as ErrorCode.INTERNAL.
        """
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_noise.noise.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
            patch(
                "clipwright_noise.noise.save_timeline",
                side_effect=OSError(_INJECTED_SECRET_PATH),
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=None,
            )

        data = _d(result)
        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in data["error"].get("hint", "")
