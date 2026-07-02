"""test_internal_guard.py — Regression guard for unexpected-exception handling
at the detect_loudness() tool boundary (CWE-209 path exposure symmetry
horizontal rollout).

Mirrors clipwright_wrap's TestInternalExceptionGuard pattern (see
clipwright-wrap/tests/test_wrap.py).

Verification point:
  detect_loudness() must convert an unexpected non-ClipwrightError exception
  (e.g. OSError raised by save_timeline) into ErrorCode.INTERNAL with fixed,
  path-free wording, instead of letting the exception propagate or leaking
  the injected absolute path via str(exc) in the message/hint.

Regression guard: detect_loudness() converts the unexpected OSError into
ErrorCode.INTERNAL via an `except Exception` guard placed after its
existing `except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_loudness.schemas import DetectLoudnessOptions

FPS = 30.0
_TEST_BIT_RATE = 8_000_000
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"

_FAKE_LOUDNORM_MEASURED = {
    "measured": {
        "input_i": -21.75,
        "input_tp": -18.06,
        "input_lra": 0.0,
        "input_thresh": -31.75,
        "target_offset": 0.03,
    },
    "warnings": [],
}


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Helper to construct a MediaInfo for testing (video + audio present)."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


class TestInternalExceptionGuard:
    """F-1 / SR-R-001: unexpected non-ClipwrightError exceptions raised while
    saving the timeline must be converted to ErrorCode.INTERNAL with a fixed,
    path-free message/hint at the detect_loudness() tool boundary.
    """

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        """save_timeline() raising OSError(full path) must not leak the
        injected path via the error envelope and must surface as
        ErrorCode.INTERNAL.
        """
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
            patch(
                "clipwright_loudness.loudness.save_timeline",
                side_effect=OSError(_INJECTED_SECRET_PATH),
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=None,
            )

        data = result.model_dump()

        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in data["error"].get("hint", "")
