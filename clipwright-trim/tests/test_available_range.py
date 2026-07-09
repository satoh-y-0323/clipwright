"""test_available_range.py — ADR-3 available_range wiring contract for trim output.

★ADR-3 contract: trim carves out sub-ranges of a media file. Each keep Clip's
media_reference.available_range describes the *whole* media asset
(TimeRange(start=0, duration=media_info.duration)), never the clip's own
source_range. Downstream tools (e.g. clipwright-render, clipwright-speed) rely
on available_range to know how much headroom exists around a given clip's
source_range (e.g. for retiming / padding). Flowing source_range into
available_range would silently claim each clip has *no* headroom beyond its
own kept segment, which is wrong.

Verification aspects:
  (1) available_range.start_time.value == 0 and available_range.duration ==
      media_info.duration (RationalTime comparison, video-basis duration).
  (2) source_range is a subset of available_range:
      source_range.start_time + source_range.duration <= available_range.duration
      (the core regression-prevention assertion for this issue).
  (3) available_range is a single fixed value derived from media_info.duration,
      identical across all keep clips regardless of each clip's own
      source_range (i.e. not source_range flowed into available_range).

Mocking policy mirrors test_trim.py: clipwright_trim.trim.inspect_media is
patched with a synthetic MediaInfo; no real ffprobe binary is invoked.

Implementation note: trim.py's _trim_inner passes MediaRef(..., available_range=
TimeRangeModel(start_time=RationalTimeModel(value=0, rate=rate),
duration=media_info.duration)) so add_clip() (see clipwright.otio_utils.add_clip)
wires ExternalReference.available_range accordingly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_trim.schemas import TrimOptions, TrimRange
from clipwright_trim.trim import trim_media

# ===========================================================================
# Constants
# ===========================================================================

FPS = 30.0
DURATION_SEC = 10.0


# ===========================================================================
# Helpers (mirrors test_trim.py)
# ===========================================================================


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = DURATION_SEC,
    rate: float = FPS,
) -> MediaInfo:
    """Construct a synthetic MediaInfo for mocking inspect_media."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    duration = (
        RationalTimeModel(value=duration_sec * rate, rate=rate)
        if duration_sec is not None
        else None
    )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _keep_opts(*ranges: tuple[float, float], padding: float = 0.0) -> TrimOptions:
    """Build a TrimOptions with keep ranges."""
    return TrimOptions(
        keep=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
        padding_sec=padding,
    )


def _clips_of(output: str) -> list[otio.schema.Clip]:
    """Load the timeline and return the V1 Clip items."""
    tl = load_timeline(output)
    v1 = tl.tracks[0]
    return [it for it in v1 if isinstance(it, otio.schema.Clip)]


# ===========================================================================
# (1) available_range == TimeRange(start=0, duration=media_info.duration)
# ===========================================================================


class TestAvailableRangeMatchesMediaDuration:
    """available_range must describe the whole media asset, not the clip's segment."""

    def test_single_keep_range_available_range_start_zero(self, tmp_path: Path) -> None:
        """available_range.start_time must be zero, regardless of the keep range's own start."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            # keep range starts at 2.0s, well after t=0.
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 1
        ref = clips[0].media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        assert ref.available_range is not None, (
            "available_range is unset — ADR-3 requires it to be wired from "
            "media_info.duration"
        )
        assert ref.available_range.start_time == otio.opentime.RationalTime(
            value=0.0, rate=rate
        )

    def test_single_keep_range_available_range_duration_matches_media_duration(
        self, tmp_path: Path
    ) -> None:
        """available_range.duration must equal media_info.duration (video-basis)."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 1
        ref = clips[0].media_reference
        assert ref.available_range is not None
        assert ref.available_range.duration == otio.opentime.RationalTime(
            value=10.0 * rate, rate=rate
        )

    def test_available_range_duration_matches_full_10s_media_regardless_of_short_keep(
        self, tmp_path: Path
    ) -> None:
        """Even a short keep range (1.0s) must report the full 10.0s media duration.

        This is the direct regression check for the bug: naively flowing
        source_range into available_range would produce a 1.0s available_range
        here instead of 10.0s.
        """
        rate = 25.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((4.0, 5.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 1
        ref = clips[0].media_reference
        assert ref.available_range is not None
        assert ref.available_range.duration == otio.opentime.RationalTime(
            value=10.0 * rate, rate=rate
        ), (
            "available_range.duration must be the full media duration, not the keep segment length"
        )


# ===========================================================================
# (2) source_range must be a subset of available_range (core regression guard)
# ===========================================================================


class TestSourceRangeSubsetOfAvailableRange:
    """source_range.start + source_range.duration <= available_range.duration."""

    def test_keep_range_source_range_fits_within_available_range(
        self, tmp_path: Path
    ) -> None:
        """The kept segment must lie entirely within the reported available_range."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((6.0, 9.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 1
        clip = clips[0]
        ref = clip.media_reference
        assert ref.available_range is not None
        source_end = clip.source_range.start_time + clip.source_range.duration
        available_end = ref.available_range.start_time + ref.available_range.duration
        assert source_end <= available_end, (
            "source_range must be fully contained within available_range "
            f"(source_range ends at {source_end}, available_range ends at {available_end})"
        )

    def test_two_keep_ranges_both_source_ranges_fit_within_available_range(
        self, tmp_path: Path
    ) -> None:
        """With multiple keep ranges, every clip's source_range must fit within its
        own available_range (each clip references the same full-media
        available_range)."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 3.0), (6.0, 9.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 2
        for clip in clips:
            ref = clip.media_reference
            assert ref.available_range is not None
            source_end = clip.source_range.start_time + clip.source_range.duration
            available_end = (
                ref.available_range.start_time + ref.available_range.duration
            )
            assert source_end <= available_end


# ===========================================================================
# (3) available_range is a fixed value derived from media_info.duration,
#     not flowed from each clip's own source_range
# ===========================================================================


class TestAvailableRangeNotFlowedFromSourceRange:
    """available_range must be identical across clips and independent of
    each clip's own source_range (i.e. NOT a copy of source_range)."""

    def test_two_keep_ranges_share_identical_available_range(
        self, tmp_path: Path
    ) -> None:
        """Two keep clips with different source_range values must share the exact
        same available_range (both equal to the full media duration)."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 3.0), (6.0, 9.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 2

        # Sanity: the two clips have distinct source_range durations/starts
        # (2.0s and 3.0s respectively) — if available_range merely copied
        # source_range, the two available_range values below would differ too.
        assert clips[0].source_range != clips[1].source_range

        ref0 = clips[0].media_reference
        ref1 = clips[1].media_reference
        assert ref0.available_range is not None
        assert ref1.available_range is not None
        assert ref0.available_range == ref1.available_range
        assert ref0.available_range.duration == otio.opentime.RationalTime(
            value=10.0 * rate, rate=rate
        )

    def test_available_range_is_not_equal_to_clip_source_range(
        self, tmp_path: Path
    ) -> None:
        """A keep clip whose source_range is shorter than the full media duration
        must NOT have available_range == source_range (i.e. available_range was
        not flowed from source_range)."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            # keep (2.0, 5.0) -> source_range.duration == 3.0s != media duration 10.0s
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        clips = _clips_of(output)
        assert len(clips) == 1
        clip = clips[0]
        ref = clip.media_reference
        assert ref.available_range is not None
        assert ref.available_range != clip.source_range, (
            "available_range must not be a copy of source_range (ADR-3)"
        )
