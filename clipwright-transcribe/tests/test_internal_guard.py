"""test_internal_guard.py — INTERNAL boundary guard regression test for
clipwright_transcribe.transcribe.transcribe_media.

Target: except Exception 境界ガード横断展開（CWE-209 パス露出対称化）
Architecture: .claude/reports/architecture-report-20260702-223529.md §1.1 / §2.2(A') / §3

Verifies that an unexpected non-ClipwrightError exception raised deep inside
transcribe_media() (here: save_timeline() raising a raw OSError containing an
absolute path) is converted at the public boundary to a fixed-wording
ErrorCode.INTERNAL result, and that the injected full path never leaks into
the returned message/hint (CWE-209).

Mirrors clipwright-wrap's TestInternalExceptionGuard. This test targets the
public boundary transcribe_media() only; the existing `except Exception` in
the internal helper _detect_backend() (backend-detection graceful-degradation,
transcribe.py:237) is out of scope and is not touched by this test or by the
guard it verifies.

Regression guard: transcribe_media() converts the injected OSError into a
fixed, path-free ErrorCode.INTERNAL result via a broad `except Exception`
guard; this test locks that behavior in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo, ToolResult

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import transcribe_media

from ._whisper_run import _whisper_run

FPS = 30.0

# Injected exception message: absolute-path-shaped string with a sensitive
# fragment, used to assert non-leakage into the returned envelope.
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_media_info(path: str) -> MediaInfo:
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=10.0 * FPS, rate=FPS),
        streams=streams,
        bit_rate=8_000_000,
    )


def _opts(**kwargs: Any) -> TranscribeOptions:
    return TranscribeOptions(**kwargs)


class TestInternalExceptionGuard:
    """F-1 / SR-R-001 / CWE-209: transcribe_media() must convert unexpected
    non-ClipwrightError exceptions to ErrorCode.INTERNAL with fixed, path-free
    wording."""

    def test_save_timeline_oserror_returns_internal_without_full_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"fake")
        model = tmp_path / "ggml-base.bin"
        model.write_bytes(b"fake-model")
        output = tmp_path / "out.otio"

        monkeypatch.setattr(
            "clipwright_transcribe.transcribe.inspect_media",
            lambda _media: _make_media_info(str(media)),
        )
        monkeypatch.setattr(
            "clipwright_transcribe.transcribe._run_whisper",
            lambda *args, **kwargs: _whisper_run(
                [{"start_sec": 0.0, "end_sec": 1.0, "text": "hi"}]
            ),
        )

        def _raise_oserror(*_args: Any, **_kwargs: Any) -> None:
            raise OSError(_INJECTED_SECRET_PATH)

        monkeypatch.setattr(
            "clipwright_transcribe.transcribe.save_timeline", _raise_oserror
        )

        result: ToolResult = transcribe_media(
            str(media), str(output), _opts(model_path=str(model))
        )
        data = result.model_dump()

        assert data["ok"] is False
        assert data["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in data["error"]["message"]
        assert _INJECTED_SECRET_PATH not in data["error"]["hint"]
