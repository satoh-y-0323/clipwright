"""Shared fixtures for clipwright-speed tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import opentimelineio as otio
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(name: str, duration_sec: float = 5.0, rate: float = 24.0) -> otio.schema.Clip:
    """Build a simple Clip with an ExternalReference and source_range."""
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_gap(duration_sec: float = 2.0, rate: float = 24.0) -> otio.schema.Gap:
    """Build a Gap with the given duration."""
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Gap(source_range=sr)


def _make_timeline_with_gap() -> otio.schema.Timeline:
    """Build a timeline with clips and a gap: [Clip0, Gap, Clip1, Clip2].

    Clip-only index space (gaps excluded):
      clip_index=0 -> Clip0
      clip_index=1 -> Clip1
      clip_index=2 -> Clip2
    """
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)

    v1.append(_make_clip("clip0"))
    v1.append(_make_gap())
    v1.append(_make_clip("clip1"))
    v1.append(_make_clip("clip2"))

    return tl


def _make_simple_timeline(n_clips: int = 2) -> otio.schema.Timeline:
    """Build a timeline with n_clips clips and no gaps."""
    tl = otio.schema.Timeline(name="simple_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)

    for i in range(n_clips):
        v1.append(_make_clip(f"clip{i}"))

    return tl


def _make_audio_only_timeline() -> otio.schema.Timeline:
    """Build a timeline with only an audio track (no video clips)."""
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
        yield Path(d)


@pytest.fixture
def simple_timeline_file(tmp_dir: Path) -> Path:
    """Write a simple 2-clip timeline to a temp .otio file; return the path."""
    tl = _make_simple_timeline(n_clips=2)
    path = tmp_dir / "simple.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path


@pytest.fixture
def gap_timeline_file(tmp_dir: Path) -> Path:
    """Write a [Clip0, Gap, Clip1, Clip2] timeline; return the path."""
    tl = _make_timeline_with_gap()
    path = tmp_dir / "gap_tl.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path


@pytest.fixture
def audio_only_timeline_file(tmp_dir: Path) -> Path:
    """Write an audio-only timeline; return the path."""
    tl = _make_audio_only_timeline()
    path = tmp_dir / "audio_only.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path
