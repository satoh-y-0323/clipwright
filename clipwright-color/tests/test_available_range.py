"""test_available_range.py — TDD Red: ADR-4 available_range wiring for clipwright-color.

clipwright-color creates a full-length keep clip (_add_full_clip) when writing a
new timeline. Per otio_utils.add_clip's ADR-4 contract (see
src/clipwright/otio_utils.py and tests/test_otio_utils.py
TestAvailableRange), MediaRef.available_range is wired into
ExternalReference.available_range so downstream tools (trim/render/etc.) know
the full extent of the source media, not just the portion currently referenced
by source_range.

color.py's _add_full_clip currently builds the Clip/ExternalReference manually
(otio.schema.ExternalReference(target_url=target_url)) WITHOUT setting
available_range, so this test is expected to fail (Red) until color.py is
updated to populate it — either by reusing otio_utils.add_clip with a MediaRef
carrying available_range, or by setting
ExternalReference.available_range directly.

Verification points:
  (1) ExternalReference.available_range must be set (not None) on the clip
      written by a fresh (timeline=None) detect_color call (full-length case).
  (2) available_range must fully contain source_range (source_range subset of
      available_range): available.start <= source.start and
      source.end_time_exclusive <= available.end_time_exclusive.
  (3) For the full-length clip case (_add_full_clip), available_range must be
      exactly equal to source_range (same start=0 and same duration), since the
      whole media file is referenced.

Mock policy: mirrors test_color.py — patch clipwright_color.color.inspect_media
and clipwright_color.color.measure_brightness. No real ffmpeg/ffprobe invoked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_color.schemas import (
    DetectColorOptions,  # type: ignore[import-not-found]
)

FPS = 30.0
_TEST_BIT_RATE = 8_000_000


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Construct a MediaInfo for testing (mirrors test_color.py helper)."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


def _fake_measured(yavg: float = 96.4) -> dict[str, Any]:
    """Return a fake measure_brightness result dict (mirrors test_color.py)."""
    return {
        "measured": {
            "yavg": yavg,
            "ymin": 9.0,
            "ymax": 242.0,
            "sampled_frames": 12,
        },
        "warnings": [],
    }


def _run_detect_color_full_length(
    tmp_path: Path,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> otio.schema.Clip:
    """Run detect_color with timeline=None and return the written V1 clip.

    This exercises the _add_full_clip path (new timeline creation).
    """
    from clipwright_color.color import (
        detect_color,  # type: ignore[import-not-found]
    )

    media = tmp_path / "video.mp4"
    media.write_bytes(b"dummy")
    output = tmp_path / "out.otio"
    opts = DetectColorOptions(target_luma=128.0)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "clipwright_color.color.inspect_media",
            lambda p: _make_media_info(str(p), duration_sec=duration_sec, rate=rate),
        )
        mp.setattr(
            "clipwright_color.color.measure_brightness",
            lambda media_path, options: _fake_measured(yavg=96.4),
        )
        result = detect_color(
            media=str(media), output=str(output), options=opts, timeline=None
        )

    assert result["ok"] is True, (
        f"detect_color must succeed. error={result.get('error')}"
    )

    tl = otio.adapters.read_from_file(str(output))
    video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    assert len(video_tracks) == 1
    clips = [item for item in video_tracks[0] if isinstance(item, otio.schema.Clip)]
    assert len(clips) == 1
    return clips[0]


# ===========================================================================
# (1) available_range must be set (ADR-4)
# ===========================================================================


class TestAvailableRangeSet:
    """ADR-4: ExternalReference.available_range must be populated on write."""

    def test_available_range_is_not_none(self, tmp_path: Path) -> None:
        """Full-length clip's media_reference.available_range must not be None."""
        clip = _run_detect_color_full_length(tmp_path)

        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        assert ref.available_range is not None, (
            "ADR-4: ExternalReference.available_range must be set on the clip"
            " written by _add_full_clip; got None (feature not implemented)."
        )


# ===========================================================================
# (2) source_range subset of available_range
# ===========================================================================


class TestSourceRangeSubsetOfAvailableRange:
    """source_range must be fully contained within available_range."""

    def test_source_range_is_subset_of_available_range(self, tmp_path: Path) -> None:
        """available.start <= source.start and source.end_exclusive <= available.end_exclusive."""
        clip = _run_detect_color_full_length(tmp_path, duration_sec=10.0, rate=FPS)

        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        available_range = ref.available_range
        assert available_range is not None, (
            "available_range must be set before subset containment can be checked."
        )

        source_range = clip.source_range
        assert available_range.start_time <= source_range.start_time, (
            "source_range start must not precede available_range start"
            f" (available={available_range.start_time},"
            f" source={source_range.start_time})."
        )
        assert (
            source_range.end_time_exclusive() <= available_range.end_time_exclusive()
        ), (
            "source_range end must not exceed available_range end"
            f" (available_end={available_range.end_time_exclusive()},"
            f" source_end={source_range.end_time_exclusive()})."
        )


# ===========================================================================
# (3) Full-length clip: available_range == source_range
# ===========================================================================


class TestFullLengthAvailableRangeMatchesSourceRange:
    """_add_full_clip references the whole media file, so available_range must
    equal source_range exactly (same start=0 and same duration/rate)."""

    def test_available_range_equals_source_range_for_full_length_clip(
        self, tmp_path: Path
    ) -> None:
        """available_range must equal source_range for a full-length keep clip."""
        clip = _run_detect_color_full_length(tmp_path, duration_sec=10.0, rate=FPS)

        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        available_range = ref.available_range
        assert available_range is not None

        source_range = clip.source_range
        assert available_range.start_time == source_range.start_time, (
            f"available_range.start_time ({available_range.start_time}) must equal"
            f" source_range.start_time ({source_range.start_time}) for a"
            " full-length clip."
        )
        assert available_range.duration == source_range.duration, (
            f"available_range.duration ({available_range.duration}) must equal"
            f" source_range.duration ({source_range.duration}) for a full-length"
            " clip."
        )

    def test_available_range_duration_matches_media_duration(
        self, tmp_path: Path
    ) -> None:
        """available_range.duration must reflect the full media duration (5.0s @ FPS)."""
        clip = _run_detect_color_full_length(tmp_path, duration_sec=5.0, rate=FPS)

        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        available_range = ref.available_range
        assert available_range is not None

        expected_duration = otio.opentime.RationalTime(5.0 * FPS, FPS)
        assert available_range.duration == expected_duration, (
            f"available_range.duration must equal {expected_duration}"
            f" (5.0s @ {FPS}fps), got {available_range.duration}."
        )
