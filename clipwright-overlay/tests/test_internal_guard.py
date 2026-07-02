"""INTERNAL boundary guard regression test for clipwright-overlay.

Covers architecture-report-20260702-223529.md §2.3 (系統B・dict返し): add_overlay
must catch unexpected (non-ClipwrightError) exceptions raised by terminal I/O
(save_timeline) and convert them to a fixed-wording INTERNAL error rather than
letting the raw exception (and any embedded filesystem path) propagate or leak
into the returned envelope (CWE-209).

add_overlay returns a plain dict (ToolResult.model_dump()); this test asserts
against that dict shape directly (no isinstance check needed — see
architecture-report §3.2 for the shared assert template).

Regression guard: add_overlay converts the injected OSError into a fixed,
path-free INTERNAL error via an `except Exception` guard after its existing
`except ClipwrightError` block; this test locks that behavior in.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest
from _imgbytes import DUMMY_PNG_BYTES as _DUMMY_PNG_BYTES

import clipwright_overlay.overlay as overlay_mod
from clipwright_overlay.overlay import add_overlay
from clipwright_overlay.schemas import AddOverlayOptions

_RATE = 24.0

# Fixed injected string: absolute-path-looking + sensitive segment, used to prove
# CWE-209 non-exposure (must not appear anywhere in the returned message/hint).
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _make_clip(
    name: str, duration_sec: float = 10.0, rate: float = _RATE
) -> otio.schema.Clip:
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_v1_timeline(rate: float = _RATE) -> otio.schema.Timeline:
    tl = otio.schema.Timeline(name="test_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    v1.append(_make_clip("clip0", rate=rate))
    return tl


class TestInternalExceptionGuard:
    """OSError from save_timeline must surface as INTERNAL, not propagate raw."""

    def test_save_timeline_os_error_returns_internal_without_path_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange: valid minimal fixture (options with valid values, existing
        # image file) so execution reaches the terminal save_timeline call
        # without tripping any early-return / validation branch.
        tl = _make_v1_timeline()
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        img_path = tmp_path / "logo.png"
        img_path.write_bytes(_DUMMY_PNG_BYTES)

        output_path = tmp_path / "output.otio"

        options = AddOverlayOptions(
            image_path=str(img_path),
            start_sec=1.0,
            duration_sec=3.0,
            x="(W-w)/2",
            y="(H-h)/2",
            scale=1.0,
            opacity=1.0,
            fade_in_sec=0.0,
            fade_out_sec=0.0,
        )

        def _raise_os_error(timeline_obj: object, path: str) -> None:
            raise OSError(_INJECTED_SECRET_PATH)

        monkeypatch.setattr(overlay_mod, "save_timeline", _raise_os_error)

        # Act
        result = add_overlay(
            timeline=str(timeline_path),
            output=str(output_path),
            options=options,
        )

        # Assert: add_overlay returns a dict (ToolResult.model_dump()) directly.
        assert result["ok"] is False
        assert result["error"]["code"] == "INTERNAL"
        # CWE-209: the injected path must not leak into message or hint.
        assert _INJECTED_SECRET_PATH not in result["error"]["message"]
        assert _INJECTED_SECRET_PATH not in result["error"].get("hint", "")
