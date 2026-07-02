"""test_internal_guard.py — INTERNAL boundary guard regression test.

Verifies that an unexpected exception raised deep inside render_timeline (via
load_timeline, the terminal I/O call reached after the timeline-existence
check but before any ClipwrightError-wrapped I/O) is caught by a broad
``except Exception`` boundary guard and converted to a fixed-wording INTERNAL
error, instead of propagating the raw exception (which may embed absolute
filesystem paths) to the caller (SR-R-001 / CWE-209).

The fixture is deliberately subtitle-free and single-source so that it does
not trip the subtitle overwrite-collision pre-check that runs *before* the
try/except block in render_timeline (that pre-check intentionally raises
ClipwrightError directly and is out of scope here; see architecture-report
§2.4 / ADR-EG-5).

Regression guard: render_timeline converts the injected OSError into a fixed,
path-free INTERNAL error via a broad ``except Exception`` guard after
``except PydanticValidationError``; this test locks that behavior in.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

FPS = 30.0

# A secret-looking absolute path injected into the OSError message. Must never
# surface in the returned error envelope (CWE-209).
_INJECTED_SECRET_PATH = "C:\\secret\\clipwright\\out.otio"


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    """Write a single-video-track, subtitle-free OTIO timeline to disk."""
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    otio.adapters.write_to_file(tl, str(path))


class TestInternalExceptionGuard:
    """render_timeline must convert unexpected exceptions to a fixed-wording
    INTERNAL error without leaking injected path details (CWE-209)."""

    def test_unexpected_exception_returns_internal_without_path_leak(
        self, tmp_path: Path
    ) -> None:
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.load_timeline",
            side_effect=OSError(_INJECTED_SECRET_PATH),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INTERNAL"
        assert _INJECTED_SECRET_PATH not in result["error"]["message"]
        assert _INJECTED_SECRET_PATH not in result["error"]["hint"]
