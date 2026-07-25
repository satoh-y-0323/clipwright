"""Shared fixtures for clipwright-speed tests."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import opentimelineio as otio
import pytest
from clipwright.nle_interop import conform_timeline_for_nle
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

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
# NLE mirror-sync helpers (speed test-speed-ms batch)
# ---------------------------------------------------------------------------


def _make_clip_with_available_range(
    name: str, duration_sec: float = 5.0, rate: float = 24.0
) -> otio.schema.Clip:
    """Build a Clip whose ExternalReference carries an available_range.

    conform_timeline_for_nle's mirror creation (_fill_mirror_track in core
    nle_interop.py) clones available_range onto each mirror clip's own
    ExternalReference, so NLE fixtures need it set on the source clip to get
    a comparable available_range on the resulting mirror. The plain
    _make_clip helper above (used by non-NLE fixtures) does not set it.
    """
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    ref.available_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_v1_only_timeline(
    clip_names: list[str], duration_sec: float = 5.0, rate: float = 24.0
) -> otio.schema.Timeline:
    """Build a timeline with only a V1 track (no pre-existing Audio track).

    conform_timeline_for_nle creates its own Audio track(s) from scratch when
    none exist yet; this is the shape a create-tool timeline has before its
    first NLE conform.
    """
    tl = otio.schema.Timeline(name="v1_only")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
    for name in clip_names:
        v1.append(
            _make_clip_with_available_range(name, duration_sec=duration_sec, rate=rate)
        )
    return tl


def _make_media_info(
    target_url: str,
    *,
    duration_sec: float = 5.0,
    rate: float = 24.0,
    audio_channels: list[int] | None = None,
) -> MediaInfo:
    """Hand-build a MediaInfo (1 video stream + N audio streams) for NLE conform.

    Mirrors core tests/test_nle_interop.py's ``_media_info`` helper pattern
    (hand-built, no ffprobe dependency).
    """
    streams: list[StreamInfo] = [StreamInfo(index=0, codec_type="video")]
    idx = 1
    for channels in audio_channels or []:
        streams.append(StreamInfo(index=idx, codec_type="audio", channels=channels))
        idx += 1
    return MediaInfo(
        path=target_url,
        container="mov",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
    )


def _media_infos_for_v1(
    tl: otio.schema.Timeline, audio_channels: list[int]
) -> dict[str, MediaInfo]:
    """Build a media_infos mapping keyed by each V1 clip's exact target_url.

    Keys must match ExternalReference.target_url literally (conform does a
    literal string comparison, no normalization).
    """
    infos: dict[str, MediaInfo] = {}
    for track in tl.tracks:
        if track.kind != otio.schema.TrackKind.Video:
            continue
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            ref = item.media_reference
            infos[ref.target_url] = _make_media_info(
                ref.target_url, audio_channels=audio_channels
            )
    return infos


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d).resolve()


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


@pytest.fixture
def conformed_timeline_file(tmp_dir: Path) -> Path:
    """Write a 2-clip V1 timeline conformed for NLE (single stereo audio mirror).

    No pre-existing Audio track: conform_timeline_for_nle creates A1 from
    scratch, mirroring both V1 clips onto it with a single 2-channel (stereo)
    audio stream.
    """
    tl = _make_v1_only_timeline(["clip0", "clip1"])
    media_infos = _media_infos_for_v1(tl, audio_channels=[2])
    conform_timeline_for_nle(tl, media_infos)

    path = tmp_dir / "conformed.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path


@pytest.fixture
def conformed_multistream_timeline_file(tmp_dir: Path) -> Path:
    """Write a 2-clip V1 timeline conformed with 2 mono audio streams (A1+A2).

    No pre-existing Audio track: conform_timeline_for_nle creates both A1 and
    A2 from scratch, one per 1-channel audio stream.
    """
    tl = _make_v1_only_timeline(["clip0", "clip1"])
    media_infos = _media_infos_for_v1(tl, audio_channels=[1, 1])
    conform_timeline_for_nle(tl, media_infos)

    path = tmp_dir / "conformed_multistream.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path


@pytest.fixture
def conformed_bgm_timeline_file(tmp_dir: Path) -> Path:
    """Write a timeline with a pre-existing non-mirroring (bgm) Audio track.

    A single bgm-style clip (metadata["clipwright"].kind == "bgm") is placed
    on Audio before conform runs. Since it does not mirror V1 item-for-item
    (1 item vs 2 V1 clips), conform's A1-adoption check (_a1_mirrors_v1)
    degrades to skip+warning: no mirror clips are created and the bgm clip is
    left untouched by mirroring (it is still visited by conform's all-track
    timecode shift pass, a no-op here since no start_timecode is set). V1
    clips still receive a Resolve_OTIO Link Group ID and the timeline still
    receives its idempotency marker (conform's final step runs
    unconditionally).
    """
    tl = _make_v1_only_timeline(["clip0", "clip1"])

    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(a1)
    bgm_clip = _make_clip_with_available_range("bgm", duration_sec=10.0)
    bgm_clip.metadata["clipwright"] = {
        "tool": "clipwright-bgm",
        "version": "0.0.0",
        "kind": "bgm",
    }
    a1.append(bgm_clip)

    media_infos = _media_infos_for_v1(tl, audio_channels=[2])
    conform_timeline_for_nle(tl, media_infos)

    path = tmp_dir / "conformed_bgm.otio"
    otio.adapters.write_to_file(tl, str(path))
    return path
