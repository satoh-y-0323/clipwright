"""test_internal_guard.py — INTERNAL boundary guard regression test for build_sequence.

architecture-report-20260702-223529.md §1.1 / §2.1 / §3: build_sequence is a
"system A" (standard) boundary. It must convert unexpected non-ClipwrightError
exceptions raised deep inside the call (e.g. from save_timeline) into a fixed,
path-free ErrorCode.INTERNAL envelope (CWE-209 / SR-R-001).

This mirrors clipwright-wrap's TestInternalExceptionGuard (see
clipwright-wrap/tests/test_wrap.py) as the reference pattern for this
suite-wide except Exception guard rollout.

Injection point: clipwright_sequence.sequence.save_timeline, the terminal I/O
call reached only after all validation succeeds. It can raise a raw OSError
that is not wrapped in ClipwrightError, so it is not swallowed by the existing
`except ClipwrightError` branch — making it the correct probe for the broad
`except Exception` guard.

Regression guard: build_sequence converts the injected OSError into a fixed,
path-free ErrorCode.INTERNAL envelope; this test locks that behavior in.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

FPS = 30.0
DURATION_SEC = 10.0

# A secret-looking absolute path injected into the OSError message. Must never
# surface in the returned error envelope (CWE-209).
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_media_info(path: str) -> MediaInfo:
    """Minimal valid MediaInfo for a single-video-stream source (happy path)."""
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=DURATION_SEC * FPS, rate=FPS),
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
        bit_rate=8_000_000,
    )


class TestInternalExceptionGuard:
    """build_sequence must convert an unexpected save_timeline OSError into a
    fixed-wording ErrorCode.INTERNAL envelope without leaking the injected
    absolute path in message/hint (architecture-report-20260702-223529.md
    §1.1 / §3.2).
    """

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path
    ) -> None:
        media = str(tmp_path / "source.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [SequenceClip(media=media, start_sec=0.0, end_sec=5.0)]

        with (
            patch(
                "clipwright_sequence.sequence.inspect_media",
                return_value=_make_media_info(path=media),
            ),
            patch(
                "clipwright_sequence.sequence.save_timeline",
                side_effect=OSError(_INJECTED_SECRET_PATH),
            ),
        ):
            result = build_sequence(clips=clips, output=output)

        data = result if isinstance(result, dict) else result.model_dump()

        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"].get("message", "")
        assert _INJECTED_SECRET_PATH not in data["error"].get("hint", "")
