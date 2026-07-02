"""test_internal_guard.py — INTERNAL boundary guard regression test.

Target: detect_silence() at clipwright-silence's public tool boundary must
convert unexpected non-ClipwrightError exceptions (e.g. a raw OSError raised
by save_timeline) into ErrorCode.INTERNAL with fixed, path-free wording
(SR-R-001 / CWE-209). Mirrors clipwright-wrap's TestInternalExceptionGuard
and the canonical pattern already shipped in frames/wrap/scene/transition/
stabilize (architecture-report-20260702-223529.md §1.1 / §3).

Regression guard: detect_silence() converts the unexpected OSError into
ErrorCode.INTERNAL via an `except Exception` guard placed after its
existing `except ClipwrightError` block.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.schemas import DetectSilenceOptions

FPS = 30.0
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Minimal valid MediaInfo with one video and one audio stream."""
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    duration = RationalTimeModel(value=duration_sec * rate, rate=rate)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _fake_run_ok(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
    """Successful mock for the silencedetect subprocess: no silence detected."""
    return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _opts() -> DetectSilenceOptions:
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=0.0,
        min_keep_duration=0.0,
    )


class TestInternalExceptionGuard:
    """Regression guard: detect_silence() must convert unexpected exceptions
    to ErrorCode.INTERNAL with fixed wording so that internal paths are never
    exposed via FastMCP's str(exc) (CWE-209).
    """

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        """save_timeline() raising OSError(full path) must not leak the path
        and must surface as ErrorCode.INTERNAL.
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok),
            patch(
                "clipwright_silence.detect.save_timeline",
                side_effect=OSError(_INJECTED_SECRET_PATH),
            ),
        ):
            result = detect_silence(media, output, _opts())

        data = result.model_dump()
        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in data["error"].get("hint", "")
