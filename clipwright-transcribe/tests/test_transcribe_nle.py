"""test_transcribe_nle.py -- Resolve NLE interop conform wiring for transcribe.

Verifies that transcribe_media routes its freshly built timeline through
clipwright.nle_interop.conform_timeline_for_nle before saving (ADR-NI-3, §5,
ADR-NI-9 / FR-9), using the exact target_url string it wrote onto the
full-length clip as the media_infos key.

Aspects:
  1. Timecode-less material: the V1 full-length clip keeps its original
     (unshifted) source_range and no global_start_time is set
     (behaviour-unchanged side, NFR-1); audio mirroring still runs.
  2. Material carrying a start_timecode plus an audio stream: the saved .otio
     gains a shifted global_start_time, the timeline-level Resolve_OTIO marker,
     and a mirrored Audio track.
  3. Real round-trip key-match (ADR-NI-9): run the real transcribe_media (only
     inspect_media + _run_whisper mocked), reload the saved .otio, and assert
     Resolve_OTIO / global_start_time are present with no "not found in
     media_infos" nor "unused key" warning -- proving the write-side and
     conform-side target_url strings agree.

inspect_media and _run_whisper are patched (mirrors test_transcribe.py); no
real ffmpeg/whisper binary is invoked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.nle_interop import RESOLVE_OTIO_KEY
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_transcribe.captions import Segment
from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import transcribe_media

from ._whisper_run import _whisper_run

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


def _seg(start_sec: float, end_sec: float, text: str) -> Segment:
    return {"start_sec": start_sec, "end_sec": end_sec, "text": text}


def _make_paths(tmp_path: Path) -> tuple[str, str, str]:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"fake-model")
    output = tmp_path / "out.otio"
    return str(media), str(output), str(model)


def _run(media_info: MediaInfo, media: str, output: str, model: str) -> Any:
    with (
        patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=media_info,
        ),
        patch(
            "clipwright_transcribe.transcribe._run_whisper",
            return_value=_whisper_run([_seg(0.0, 1.0, "hi")]),
        ),
    ):
        return transcribe_media(media, output, TranscribeOptions(model_path=model))


def _v1_clips(timeline: otio.schema.Timeline) -> list[otio.schema.Clip]:
    return [it for it in timeline.tracks[0] if isinstance(it, otio.schema.Clip)]


# ===========================================================================
# 1. Timecode-less material: shift inert, audio mirroring still runs
# ===========================================================================


def test_no_timecode_leaves_source_range_and_global_start_unchanged(
    tmp_path: Path,
) -> None:
    media, output, model = _make_paths(tmp_path)

    result = _run(
        _make_media_info(media, start_timecode=None, audio_channels=2),
        media,
        output,
        model,
    )

    assert result.ok is True
    timeline = otio.adapters.read_from_file(output)

    clips = _v1_clips(timeline)
    assert len(clips) == 1
    # Full-length clip source_range starts at 0.0, unshifted (no timecode).
    assert clips[0].source_range.start_time.value == 0.0

    assert timeline.global_start_time is None

    audio_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio]
    assert len(audio_tracks) == 1


# ===========================================================================
# 2. Timecode + audio stream: global_start_time / Resolve_OTIO / Audio track
# ===========================================================================


def test_timecode_source_shifts_and_stamps_resolve_metadata(tmp_path: Path) -> None:
    media, output, model = _make_paths(tmp_path)

    result = _run(
        _make_media_info(media, start_timecode="01:00:00:00", audio_channels=2),
        media,
        output,
        model,
    )

    assert result.ok is True
    timeline = otio.adapters.read_from_file(output)

    clips = _v1_clips(timeline)
    assert len(clips) == 1
    # Full-length clip start 0.0 -> shifted additively to the timecode origin.
    assert clips[0].source_range.start_time.value == TC_ONE_HOUR_FRAMES

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
    media, output, model = _make_paths(tmp_path)

    result = _run(
        _make_media_info(media, start_timecode="01:00:00:00", audio_channels=1),
        media,
        output,
        model,
    )

    assert result.ok is True

    joined = " ".join(result.warnings).lower()
    assert "not found in media_infos" not in joined
    assert "unused" not in joined

    timeline = otio.adapters.read_from_file(output)
    assert timeline.metadata.get(RESOLVE_OTIO_KEY) is not None
    assert timeline.global_start_time is not None
    assert timeline.global_start_time.value == TC_ONE_HOUR_FRAMES
