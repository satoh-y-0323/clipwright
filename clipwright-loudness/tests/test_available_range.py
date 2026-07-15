"""test_available_range.py — ADR-3/ADR-4 available_range wiring for _add_full_clip.

GitHub Issue #1 follow-up: clipwright-loudness's _add_full_clip constructs its
own otio.schema.ExternalReference directly (Pattern B) instead of routing
through clipwright.otio_utils.append_clip. This means the core ADR-4 fix
(MediaRef.available_range -> ExternalReference.available_range) does not
automatically apply here; loudness.py must set it itself.

loudness is a full-length tool: the single keep clip's source_range already
spans the whole media (0..media_info.duration), so available_range may reuse
the same TimeRange as source_range (no separate computation needed).

Mock policy:
  - Patch clipwright_loudness.loudness.inspect_media to supply MediaInfo.
  - Patch clipwright_loudness.loudness.measure_loudness to avoid calling ffmpeg.
  - No real ffmpeg/ffprobe binary is invoked.

Verification points:
  (a) V1 clip's media_reference.available_range must be set (not None).
  (b) available_range must equal the full-length source_range
      (start=0, duration=media_info.duration) — source_range subset holds
      with equality for full-length clips.
  (c) source_range ⊆ available_range must hold generically
      (source_range.start + source_range.duration <= available_range.start
      + available_range.duration), independent of point (b)'s exact-match
      assumption.
  (d) A1 clip's media_reference.available_range must also be set (_add_full_clip
      builds an equivalent, but distinct, ExternalReference per track).

This file is new (does not modify test_loudness.py) to avoid write conflicts
in the parallel wave.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_loudness.schemas import DetectLoudnessOptions

FPS = 30.0
_TEST_BIT_RATE = 8_000_000

_FAKE_LOUDNORM_MEASURED = {
    "measured": {
        "input_i": -21.75,
        "input_tp": -18.06,
        "input_lra": 0.0,
        "input_thresh": -31.75,
        "target_offset": 0.03,
    },
    "warnings": [],
}


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Helper to construct a MediaInfo with both video and audio streams."""
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


def _run_new_timeline_detect(tmp_path: Path) -> otio.schema.Timeline:
    """Run detect_loudness with timeline=None and load back the resulting OTIO."""
    from clipwright.otio_utils import load_timeline

    from clipwright_loudness.loudness import detect_loudness

    media = tmp_path / "video.mp4"
    media.write_bytes(b"dummy")
    output = tmp_path / "out.otio"
    media_info = _make_media_info(str(media))

    with (
        patch("clipwright_loudness.loudness.inspect_media", return_value=media_info),
        patch(
            "clipwright_loudness.loudness.measure_loudness",
            return_value=_FAKE_LOUDNORM_MEASURED,
        ),
    ):
        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

    assert result.ok is True, f"detect_loudness failed unexpectedly: {result.error}"
    return load_timeline(str(output))


class TestAvailableRangeWiring:
    """ADR-4 (Pattern B): _add_full_clip must wire available_range."""

    def test_v1_clip_available_range_is_set(self, tmp_path: Path) -> None:
        """V1 clip's media_reference.available_range must not be None."""
        tl = _run_new_timeline_detect(tmp_path)
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "V1 must contain at least one clip."

        clip = clips[0]
        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        assert ref.available_range is not None, (
            "ADR-4: ExternalReference.available_range must be wired for the"
            " full-length keep clip (loudness.py._add_full_clip)."
        )

    def test_a1_clip_available_range_is_set(self, tmp_path: Path) -> None:
        """A1 clip's media_reference.available_range must also not be None."""
        tl = _run_new_timeline_detect(tmp_path)
        a1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio)
        clips = [it for it in a1 if isinstance(it, otio.schema.Clip)]
        assert clips, "A1 must contain at least one clip."

        clip = clips[0]
        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        assert ref.available_range is not None, (
            "ADR-4: A1 clip's ExternalReference.available_range must also be"
            " wired (_add_full_clip builds a distinct instance per track)."
        )

    def test_available_range_equals_full_duration(self, tmp_path: Path) -> None:
        """For full-length clips, available_range equals the full-media
        source_range: start=0, duration=media_info.duration (30fps * 10s)."""
        tl = _run_new_timeline_detect(tmp_path)
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clip = next(it for it in v1 if isinstance(it, otio.schema.Clip))

        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        available_range = ref.available_range
        assert available_range is not None

        expected_start = otio.opentime.RationalTime(0.0, FPS)
        expected_duration = otio.opentime.RationalTime(10.0 * FPS, FPS)
        assert available_range.start_time == expected_start
        assert available_range.duration == expected_duration

    def test_source_range_subset_of_available_range(self, tmp_path: Path) -> None:
        """source_range must be contained within available_range
        (source_range.start + duration <= available_range.start + duration),
        the core regression-prevention invariant of ADR-3/GitHub Issue #1."""
        tl = _run_new_timeline_detect(tmp_path)
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clip = next(it for it in v1 if isinstance(it, otio.schema.Clip))

        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        available_range = ref.available_range
        assert available_range is not None

        source_range = clip.source_range
        assert source_range is not None

        source_end = source_range.start_time + source_range.duration
        available_end = available_range.start_time + available_range.duration

        assert source_range.start_time >= available_range.start_time
        assert source_end <= available_end
