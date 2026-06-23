"""Shared fixtures for clipwright-transition tests.

ffprobe search order:
  1. PATH (shutil.which)
  2. Environment variable CLIPWRIGHT_FFPROBE

ffmpeg search order (used only for sample media generation in integration tests):
  1. PATH (shutil.which)
  2. Environment variable CLIPWRIGHT_FFMPEG

Integration tests are skipped only when the required binary is not found.
Follows the same resolution policy as clipwright-sequence/tests/conftest.py.

clipwright-transition itself does not invoke ffprobe or ffmpeg at annotation time
(pure OTIO manipulation). The fixtures below are provided for completeness and for
any future integration tests that exercise the full sequence → transition → render
pipeline in-process.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import opentimelineio as otio
import pytest


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary in PATH then env_var. Returns None if neither is found."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


@pytest.fixture(scope="session")
def ffprobe_path() -> str | None:
    """Path to the ffprobe executable. None if not found."""
    return _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")


@pytest.fixture(scope="session")
def ffmpeg_path() -> str | None:
    """Path to the ffmpeg executable. None if not found."""
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def require_ffprobe(ffprobe_path: str | None) -> str:
    """For integration tests: skip if ffprobe is not found. Returns the path."""
    if ffprobe_path is None:
        pytest.skip(
            "ffprobe not found on PATH. "
            "Add ffprobe to PATH or set the CLIPWRIGHT_FFPROBE environment variable to the full path."
        )
    return ffprobe_path


@pytest.fixture
def require_ffmpeg(ffmpeg_path: str | None) -> str:
    """For integration tests: skip if ffmpeg is not found. Returns the path."""
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg not found on PATH. "
            "Add ffmpeg to PATH or set the CLIPWRIGHT_FFMPEG environment variable to the full path."
        )
    return ffmpeg_path


def _make_clip(
    path: str,
    start_sec: float,
    duration_sec: float,
    rate: float = 30.0,
) -> otio.schema.Clip:
    """Create a minimal OTIO Clip with a known source range."""
    return otio.schema.Clip(
        name=Path(path).stem,
        media_reference=otio.schema.ExternalReference(target_url=path),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(start_sec * rate, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )


@pytest.fixture
def two_clip_timeline(tmp_path: Path) -> otio.schema.Timeline:
    """Return a minimal two-clip OTIO timeline (pure OTIO, no real media files).

    Clip durations: 10s each at 30 fps. Suitable for testing add_transition
    without requiring ffprobe or ffmpeg.
    """
    tl = otio.schema.Timeline(name="test_sequence")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(_make_clip(str(tmp_path / "clip_a.mp4"), 0.0, 10.0))
    track.append(_make_clip(str(tmp_path / "clip_b.mp4"), 0.0, 10.0))
    tl.tracks.append(track)
    return tl


@pytest.fixture
def three_clip_timeline(tmp_path: Path) -> otio.schema.Timeline:
    """Return a minimal three-clip OTIO timeline (pure OTIO, no real media files).

    Clip durations: 10s each at 30 fps. Useful for testing multiple boundaries.
    """
    tl = otio.schema.Timeline(name="test_sequence_3")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(_make_clip(str(tmp_path / "clip_a.mp4"), 0.0, 10.0))
    track.append(_make_clip(str(tmp_path / "clip_b.mp4"), 0.0, 10.0))
    track.append(_make_clip(str(tmp_path / "clip_c.mp4"), 0.0, 10.0))
    tl.tracks.append(track)
    return tl


@pytest.fixture
def timeline_file(two_clip_timeline: otio.schema.Timeline, tmp_path: Path) -> Path:
    """Save two_clip_timeline to a temporary .otio file and return its path."""
    otio_path = tmp_path / "input.otio"
    otio.adapters.write_to_file(two_clip_timeline, str(otio_path))
    return otio_path


@pytest.fixture
def output_otio(tmp_path: Path) -> Path:
    """Return a path for the output OTIO file (does not exist yet)."""
    return tmp_path / "output.otio"
