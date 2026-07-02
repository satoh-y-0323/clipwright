"""INTERNAL exception guard regression tests for clipwright-reframe (architecture-report
§2.2, systen A').

reframe() must convert an unexpected non-ClipwrightError exception (e.g. a raw
OSError raised from save_timeline) into an ErrorCode.INTERNAL envelope with a
fixed, path-free message/hint. This mirrors clipwright_frames.extract.extract_frames()
and clipwright_wrap's TestInternalExceptionGuard pattern.

Only the public boundary reframe() is exercised here. The existing
`except Exception` inside the _run_track_cli() helper (reframe.py:321, 375) is a
graceful-degradation path for motion-tracking failures and is explicitly out of
scope for this guard (architecture-report §2.2).

Regression guard: reframe() converts the injected OSError into a fixed,
path-free ErrorCode.INTERNAL envelope via a broad `except Exception` guard at
its public boundary; this test locks that behavior in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_reframe.reframe import reframe
from clipwright_reframe.schemas import ReframeOptions

_FPS = 30.0
_DURATION_SEC = 10.0

# A secret-looking absolute path injected into the OSError message. Must never
# surface in the returned error envelope (CWE-209).
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_media_info(path: str, *, has_video: bool = True) -> MediaInfo:
    """Build a minimal MediaInfo for monkeypatching inspect_media (video required)."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    streams.append(StreamInfo(index=1, codec_type="audio", codec_name="aac"))
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=_DURATION_SEC * _FPS, rate=_FPS),
        streams=streams,
        bit_rate=8_000_000,
    )


class TestInternalExceptionGuard:
    """F-1/SR-R-001: unexpected non-ClipwrightError exceptions at the reframe()
    public boundary must be converted to ErrorCode.INTERNAL with a fixed,
    path-free message/hint (CWE-209).
    """

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        """save_timeline() raising OSError(full path) must not leak the path and
        must surface as ErrorCode.INTERNAL, not propagate as a raw exception.
        """
        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy media")
        output_path = tmp_path / "out.otio"

        opts = ReframeOptions(target_w=1080, target_h=1920, mode="pad")

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch(
                "clipwright_reframe.reframe.save_timeline",
                side_effect=OSError(_INJECTED_SECRET_PATH),
            ),
        ):
            result: Any = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        data = result if isinstance(result, dict) else result.model_dump()

        assert data["ok"] is False
        assert data["error"]["code"] == ErrorCode.INTERNAL.value
        message = data["error"].get("message", "")
        hint = data["error"].get("hint", "")
        assert _INJECTED_SECRET_PATH not in message
        assert _INJECTED_SECRET_PATH not in hint
        assert "secret" not in message
        assert "secret" not in hint

    def test_run_track_cli_internal_except_is_out_of_scope(
        self, tmp_path: Path
    ) -> None:
        """Sanity check: an exception raised deep inside CentreKeyframe construction
        (already caught by _run_track_cli's own `except Exception` at reframe.py:375)
        must continue to degrade gracefully to a constant-center track with ok=True.

        This documents that the new public-boundary guard must not interfere with
        the existing, out-of-scope _run_track_cli graceful-degradation except blocks.
        """
        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy media")
        output_path = tmp_path / "out.otio"

        opts = ReframeOptions(target_w=1080, target_h=1920, mode="track")

        fake_result = type(
            "FakeCompletedProcess", (), {"stdout": '{"track": [{"bad": "data"}]}'}
        )()

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch(
                "clipwright_reframe.reframe._process.run",
                return_value=fake_result,
            ),
        ):
            result: Any = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        data = result if isinstance(result, dict) else result.model_dump()
        assert data["ok"] is True
        assert any(
            "static center track" in w.lower() or "invalid keyframe" in w.lower()
            for w in data.get("warnings", [])
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
