"""test_trim_nle.py -- Resolve NLE interop conform wiring for clipwright-trim.

Verifies that trim_media routes its freshly built timeline through
clipwright.nle_interop.conform_timeline_for_nle before saving (ADR-NI-3, §5,
ADR-NI-9 / FR-9), using the exact target_url string it wrote onto each clip as
the media_infos key.

Aspects:
  1. Timecode-less material: the V1 keep clips keep their original (unshifted)
     source_range and no global_start_time is set -- the shift path is inert,
     only audio mirroring runs (behaviour-unchanged side, NFR-1).
  2. Material carrying a start_timecode plus an audio stream: the saved .otio
     gains a shifted global_start_time, the timeline-level Resolve_OTIO marker,
     and a mirrored Audio track.
  3. Real round-trip key-match (ADR-NI-9): run the real trim_media (only
     inspect_media mocked), reload the saved .otio, and assert Resolve_OTIO /
     global_start_time are present with no "not found in media_infos" nor
     "unused key" warning -- proving the write-side and conform-side target_url
     strings agree.

inspect_media is patched with a synthetic MediaInfo (mirrors test_trim.py); no
real ffprobe binary is invoked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.nle_interop import RESOLVE_OTIO_KEY
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_trim.schemas import TrimOptions, TrimRange
from clipwright_trim.trim import trim_media

FPS = 30.0
DURATION_SEC = 10.0
# from_timecode("01:00:00:00", 30) == 1h * 3600s * 30fps.
TC_ONE_HOUR_FRAMES = 3600.0 * FPS


def _make_media_info(
    path: str,
    *,
    duration_sec: float = DURATION_SEC,
    rate: float = FPS,
    start_timecode: str | None = None,
    audio_channels: int | None = None,
) -> MediaInfo:
    """Construct a synthetic MediaInfo for mocking inspect_media."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
    ]
    if audio_channels is not None:
        streams.append(
            StreamInfo(
                index=1,
                codec_type="audio",
                codec_name="aac",
                channels=audio_channels,
            )
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=8_000_000,
        start_timecode=start_timecode,
    )


def _keep_opts(*ranges: tuple[float, float]) -> TrimOptions:
    return TrimOptions(
        keep=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
        padding_sec=0.0,
    )


def _v1_clips(timeline: otio.schema.Timeline) -> list[otio.schema.Clip]:
    return [it for it in timeline.tracks[0] if isinstance(it, otio.schema.Clip)]


# ===========================================================================
# 1. Timecode-less material: shift inert, audio mirroring still runs
# ===========================================================================


def test_no_timecode_leaves_source_range_and_global_start_unchanged(
    tmp_path: Path,
) -> None:
    media = str(tmp_path / "video.mp4")
    Path(media).touch()
    output = str(tmp_path / "out.otio")

    with patch(
        "clipwright_trim.trim.inspect_media",
        return_value=_make_media_info(media, start_timecode=None, audio_channels=2),
    ):
        result = trim_media(media, output, _keep_opts((2.0, 5.0)))

    assert result.ok is True
    timeline = load_timeline(output)

    clips = _v1_clips(timeline)
    assert len(clips) == 1
    # keep start 2.0s at 30fps -> 60.0, unshifted (no timecode origin applied).
    assert clips[0].source_range.start_time.value == 60.0

    # No timecode -> no global_start_time.
    assert timeline.global_start_time is None

    # Audio mirroring still runs (behaviour-unchanged side only for the shift).
    audio_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio]
    assert len(audio_tracks) == 1


# ===========================================================================
# 2. Timecode + audio stream: global_start_time / Resolve_OTIO / Audio track
# ===========================================================================


def test_timecode_source_shifts_and_stamps_resolve_metadata(tmp_path: Path) -> None:
    media = str(tmp_path / "video.mp4")
    Path(media).touch()
    output = str(tmp_path / "out.otio")

    with patch(
        "clipwright_trim.trim.inspect_media",
        return_value=_make_media_info(
            media, start_timecode="01:00:00:00", audio_channels=2
        ),
    ):
        result = trim_media(media, output, _keep_opts((2.0, 5.0)))

    assert result.ok is True
    timeline = load_timeline(output)

    clips = _v1_clips(timeline)
    assert len(clips) == 1
    # 60.0 (keep start) + 108000 (timecode origin) shifted additively (ADR-NI-1).
    assert clips[0].source_range.start_time.value == 60.0 + TC_ONE_HOUR_FRAMES

    assert timeline.global_start_time is not None
    assert timeline.global_start_time.value == TC_ONE_HOUR_FRAMES
    assert timeline.global_start_time.rate == FPS

    assert timeline.metadata.get(RESOLVE_OTIO_KEY) is not None

    audio_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio]
    assert len(audio_tracks) == 1
    assert audio_tracks[0].metadata[RESOLVE_OTIO_KEY]["Audio Type"] == "Stereo"


# ===========================================================================
# 3. Real OTIO round-trip: write-side / conform-side target_url key agreement
#    (ADR-NI-9)
# ===========================================================================


def test_roundtrip_target_url_key_matches_no_mismatch_warning(tmp_path: Path) -> None:
    media = str(tmp_path / "video.mp4")
    Path(media).touch()
    output = str(tmp_path / "out.otio")

    with patch(
        "clipwright_trim.trim.inspect_media",
        return_value=_make_media_info(
            media, start_timecode="01:00:00:00", audio_channels=1
        ),
    ):
        result = trim_media(media, output, _keep_opts((2.0, 5.0)))

    assert result.ok is True

    # The conform key generated at write time must match the key regenerated
    # for the media_infos mapping: if it did not, conform would either warn the
    # clip was "not found in media_infos" or that the media_infos key was
    # "unused". Neither may appear.
    joined = " ".join(result.warnings).lower()
    assert "not found in media_infos" not in joined
    assert "unused" not in joined

    timeline = load_timeline(output)
    assert timeline.metadata.get(RESOLVE_OTIO_KEY) is not None
    assert timeline.global_start_time is not None
    assert timeline.global_start_time.value == TC_ONE_HOUR_FRAMES
