"""Shared fixtures for clipwright-text tests."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import opentimelineio as otio
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(
    name: str, duration_sec: float = 5.0, rate: float = 24.0
) -> otio.schema.Clip:
    """Build a simple Clip with an ExternalReference and source_range."""
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_timeline_with_video() -> otio.schema.Timeline:
    """Build a timeline with V1 video track containing two clips."""
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    v1.append(_make_clip("clip0"))
    v1.append(_make_clip("clip1"))
    return tl


def _make_audio_only_timeline() -> otio.schema.Timeline:
    """Build a timeline with only an audio track (no video clips).

    Used for UNSUPPORTED_OPERATION tests where no V1 video track exists.
    """
    tl = otio.schema.Timeline(name="audio_only")
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(a1)
    a1.append(_make_clip("audio_clip"))
    return tl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d).resolve()


@pytest.fixture
def timeline_file(tmp_dir: Path) -> Path:
    """Write a 2-clip V1 timeline to a temp .otio file; return the path."""
    tl = _make_timeline_with_video()
    path = tmp_dir / "timeline.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path


@pytest.fixture
def output_path(tmp_dir: Path) -> Path:
    """Return an output .otio path inside tmp_dir (not yet created)."""
    return tmp_dir / "output.otio"


@pytest.fixture
def audio_only_timeline_file(tmp_dir: Path) -> Path:
    """Write an audio-only timeline; return the path.

    Used for UNSUPPORTED_OPERATION tests: no video track present.
    """
    tl = _make_audio_only_timeline()
    path = tmp_dir / "audio_only.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path
