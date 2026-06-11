"""conftest.py — Shared fixtures for clipwright-bgm tests.

Fixture list:
  - tmp_timeline_dir: Creates a temporary directory under tmp_path for timeline/bgm files
  - bgm_audio_file: Dummy BGM file with the allowed extension .mp3
  - timeline_otio_path: .otio file path (file does not exist yet)
  - output_otio_path: Output .otio file path (file does not exist yet)
  - simple_timeline: OTIO Timeline with only two tracks: V1/A1
  - media_info_bgm: MediaInfo for BGM (duration=30.0s, rate=48000, audio stream only)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

# ---------------------------------------------------------------------------
# Directories and file paths
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_timeline_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for placing timeline and bgm files in the same location."""
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def bgm_audio_file(tmp_timeline_dir: Path) -> Path:
    """Generate and return a dummy BGM file with the allowed extension .mp3."""
    path = tmp_timeline_dir / "bgm.mp3"
    path.write_bytes(b"dummy bgm audio")
    return path


@pytest.fixture
def timeline_otio_path(tmp_timeline_dir: Path) -> Path:
    """Input .otio timeline file path (file does not exist yet, before write)."""
    return tmp_timeline_dir / "timeline.otio"


@pytest.fixture
def output_otio_path(tmp_timeline_dir: Path) -> Path:
    """Output .otio timeline file path (file does not exist yet, before write)."""
    return tmp_timeline_dir / "output.otio"


# ---------------------------------------------------------------------------
# OTIO Timeline
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_timeline() -> otio.schema.Timeline:
    """Return a Timeline with two tracks: V1 (Video) and A1 (Audio).

    Used as input for add_bgm success path tests. Clips are empty
    (kind=='bgm' clips are added manually in re-invocation detection tests).
    """
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    return tl


# ---------------------------------------------------------------------------
# MediaInfo mock values
# ---------------------------------------------------------------------------

BGM_DURATION_SEC = 30.0
BGM_RATE = 48000.0


@pytest.fixture
def media_info_bgm() -> MediaInfo:
    """MediaInfo for a BGM file (1 audio stream, 30 seconds).

    Used as the mock return value for inspect_media.
    duration is represented as RationalTimeModel(value=30.0 * 48000, rate=48000).
    """
    return MediaInfo(
        path="bgm.mp3",
        container="mp3",
        duration=RationalTimeModel(value=BGM_DURATION_SEC * BGM_RATE, rate=BGM_RATE),
        streams=[
            StreamInfo(
                index=0,
                codec_type="audio",
                codec_name="mp3",
                sample_rate=48000,
                channels=2,
            )
        ],
        bit_rate=320_000,
    )


def make_bgm_timeline(
    timeline_dir: Path,
    bgm_path: Path,
    bgm_duration_sec: float = BGM_DURATION_SEC,
    bgm_rate: float = BGM_RATE,
) -> otio.schema.Timeline:
    """Build and return a timeline containing a BGM clip (expected structure after add_bgm).

    Assembles OTIO manually without calling add_bgm.
    Intended for comparing against the expected structure in unit tests.
    """
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, bgm_rate),
        duration=otio.opentime.RationalTime(bgm_duration_sec * bgm_rate, bgm_rate),
    )
    ref = otio.schema.ExternalReference(target_url=str(bgm_path))
    bgm_metadata: dict[str, Any] = {
        "clipwright": {
            "tool": "clipwright-bgm",
            "version": "0.1.0",
            "kind": "bgm",
            "volume_db": -6.0,
            "fade_in_sec": 0.0,
            "fade_out_sec": 0.0,
            "ducking": {
                "enabled": False,
                "threshold": 0.05,
                "ratio": 4.0,
            },
        }
    }
    bgm_clip = otio.schema.Clip(
        name=bgm_path.name,
        media_reference=ref,
        source_range=source_range,
        metadata=bgm_metadata,
    )
    a2.append(bgm_clip)

    tl.tracks.append(v1)
    tl.tracks.append(a1)
    tl.tracks.append(a2)
    return tl
