"""test_detect_nle.py -- Resolve NLE interop conform wiring for clipwright-silence.

Verifies that detect_silence routes its freshly built timeline through
clipwright.nle_interop.conform_timeline_for_nle before saving (ADR-NI-3, §5,
ADR-NI-9 / FR-9), using the exact target_url string it wrote onto each clip as
the media_infos key.

Aspects:
  1. Timecode-less material: the V1 keep clips keep their original (unshifted)
     source_range and no global_start_time is set (behaviour-unchanged side,
     NFR-1); audio mirroring still runs.
  2. Material carrying a start_timecode plus an audio stream: the saved .otio
     gains a shifted global_start_time, the timeline-level Resolve_OTIO marker,
     and a mirrored Audio track.
  3. Real round-trip key-match (ADR-NI-9): run the real detect_silence (only
     inspect_media + subprocess seams mocked), reload the saved .otio, and
     assert Resolve_OTIO / global_start_time are present with no "not found in
     media_infos" nor "unused key" warning -- proving the write-side and
     conform-side target_url strings agree.

inspect_media and the silencedetect run() seam are patched (mirrors
test_detect.py); no real ffmpeg/ffprobe binary is invoked.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.nle_interop import RESOLVE_OTIO_KEY
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.detect import detect_silence
from clipwright_silence.schemas import DetectSilenceOptions

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


def _stderr_one_silence() -> str:
    """One silence interval (2.0s..5.0s) so keep ranges are (0..2) and (5..10)."""
    return (
        "[silencedetect @ 0xabcdef] silence_start: 2.000000\n"
        "[silencedetect @ 0xabcdef] silence_end: 5.000000 | silence_duration: 3.000000"
    )


def _fake_run_ok(stderr: str) -> Any:
    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


def _opts() -> DetectSilenceOptions:
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=0.0,
        min_keep_duration=0.0,
    )


def _run(media_info: MediaInfo, media: str, output: str) -> Any:
    with (
        patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ),
        patch(
            "clipwright_silence.detect.resolve_tool",
            side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
        ),
        patch(
            "clipwright_silence.detect.run",
            side_effect=_fake_run_ok(_stderr_one_silence()),
        ),
    ):
        return detect_silence(media, output, _opts())


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

    result = _run(
        _make_media_info(media, start_timecode=None, audio_channels=2), media, output
    )

    assert result["ok"] is True
    timeline = load_timeline(output)

    clips = _v1_clips(timeline)
    assert len(clips) == 2
    # First keep range starts at 0.0s, unshifted (no timecode origin applied).
    assert clips[0].source_range.start_time.value == 0.0

    assert timeline.global_start_time is None

    audio_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio]
    assert len(audio_tracks) == 1


# ===========================================================================
# 2. Timecode + audio stream: global_start_time / Resolve_OTIO / Audio track
# ===========================================================================


def test_timecode_source_shifts_and_stamps_resolve_metadata(tmp_path: Path) -> None:
    media = str(tmp_path / "video.mp4")
    Path(media).touch()
    output = str(tmp_path / "out.otio")

    result = _run(
        _make_media_info(media, start_timecode="01:00:00:00", audio_channels=2),
        media,
        output,
    )

    assert result["ok"] is True
    timeline = load_timeline(output)

    clips = _v1_clips(timeline)
    assert len(clips) == 2
    # First keep starts at 0.0 -> shifted additively to the timecode origin.
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
    media = str(tmp_path / "video.mp4")
    Path(media).touch()
    output = str(tmp_path / "out.otio")

    result = _run(
        _make_media_info(media, start_timecode="01:00:00:00", audio_channels=1),
        media,
        output,
    )

    assert result["ok"] is True

    joined = " ".join(result["warnings"]).lower()
    assert "not found in media_infos" not in joined
    assert "unused" not in joined

    timeline = load_timeline(output)
    assert timeline.metadata.get(RESOLVE_OTIO_KEY) is not None
    assert timeline.global_start_time is not None
    assert timeline.global_start_time.value == TC_ONE_HOUR_FRAMES
