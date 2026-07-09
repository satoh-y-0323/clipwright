"""test_available_range.py — Red tests for ExternalReference.available_range wiring.

Context (bug under test):
  _add_full_clip() in stabilize.py builds an otio.schema.ExternalReference and
  a full-length otio.schema.Clip, but never sets
  ExternalReference.available_range. Sibling tools already wire this for the
  accumulate case via clipwright.otio_utils.add_clip() (ADR-4), which sets
  ref.available_range from MediaRef.available_range when present. stabilize's
  full clip covers the entire source duration (source_range == the whole
  media), so available_range should be present and describe that same full
  span; downstream tools that trust available_range for extension/overlap
  checks currently see None instead.

Mock policy (mirrors test_stabilize.py F-1):
  - Patch clipwright_stabilize.stabilize.inspect_media to inject stream/duration.
  - Patch clipwright_stabilize.stabilize.run_vidstabdetect to control analyze output.
  - No real ffmpeg/ffprobe binary or real libvidstab is invoked.
  - OTIO timeline I/O uses tmp_path with real save_timeline / read_from_file.

Verification points:
  (1) ExternalReference.available_range must not be None after detect_shake
      with timeline=None (new-timeline full-clip path).
  (2) available_range must describe the same full-length span as source_range
      (start_time == 0, duration == full media duration) — for the
      full-length clip, source_range and available_range coincide.
  (3) source_range must be contained within available_range
      (TimeRange.contains), the general invariant this field exists to encode.
  (4) The same wiring must also hold on the timeline=<path with empty V1
      track> branch, which reuses _add_full_clip via
      _load_and_validate_timeline.

Requirements: available_range parity with clipwright.otio_utils.add_clip (ADR-4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

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
    """Construct a MediaInfo for testing (mirrors test_stabilize.py helper)."""
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


def _fake_analyze_result(
    trf_abs: Path, severity: float | None = 0.35
) -> dict[str, Any]:
    """Return a fake run_vidstabdetect result dict (mirrors test_stabilize.py helper)."""
    trf_abs.write_bytes(b"TRF1dummy")
    return {
        "trf_path": str(trf_abs),
        "severity": severity,
        "warnings": [],
    }


def _first_v1_clip(output: Path) -> otio.schema.Clip:
    """Read the output timeline and return the sole clip on the V1 track."""
    tl = otio.adapters.read_from_file(str(output))
    video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    assert len(video_tracks) == 1
    clips = [item for item in video_tracks[0] if isinstance(item, otio.schema.Clip)]
    assert len(clips) == 1
    return clips[0]


# ===========================================================================
# (1)-(3) New timeline (timeline=None) full-clip path
# ===========================================================================


class TestAvailableRangeNewTimeline:
    """_add_full_clip must wire ExternalReference.available_range for the
    full-length clip created when timeline=None."""

    def test_available_range_is_set(self, tmp_path: Path) -> None:
        """ExternalReference.available_range must not be None (currently unset)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p), duration_sec=10.0, rate=FPS),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        clip = _first_v1_clip(output)
        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        assert ref.available_range is not None, (
            "ExternalReference.available_range must be set for the full-length"
            " clip written by _add_full_clip; got None."
        )

    def test_available_range_matches_full_duration(self, tmp_path: Path) -> None:
        """available_range must span the entire media duration (start=0, full dur)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        duration_sec = 12.5
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p), duration_sec=duration_sec, rate=FPS),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        clip = _first_v1_clip(output)
        available_range = clip.media_reference.available_range
        assert available_range is not None

        expected_duration = otio.opentime.RationalTime(duration_sec * FPS, FPS)
        assert available_range.start_time == otio.opentime.RationalTime(0.0, FPS), (
            f"available_range.start_time must be 0, got {available_range.start_time}"
        )
        assert available_range.duration.almost_equal(expected_duration), (
            "available_range.duration must equal the full media duration:"
            f" expected {expected_duration}, got {available_range.duration}"
        )

    def test_source_range_within_available_range(self, tmp_path: Path) -> None:
        """clip.source_range must be contained within available_range (⊆)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p), duration_sec=8.0, rate=FPS),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        clip = _first_v1_clip(output)
        available_range = clip.media_reference.available_range
        assert available_range is not None, (
            "available_range must be set before the containment check can be"
            " meaningful."
        )
        assert available_range.contains(clip.source_range), (
            "source_range must be contained within available_range:"
            f" source_range={clip.source_range}, available_range={available_range}"
        )


# ===========================================================================
# (4) Existing timeline with an empty V1 track also goes through _add_full_clip
# ===========================================================================


class TestAvailableRangeExistingEmptyTimeline:
    """timeline=<path> with an empty V1 track reuses _add_full_clip and must
    wire available_range the same way as the new-timeline path."""

    def test_available_range_set_on_empty_v1_load_path(self, tmp_path: Path) -> None:
        """An existing timeline with an empty V1 track must still get available_range."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Existing timeline with an empty V1 track (no clips yet).
        tl = otio.schema.Timeline()
        track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)
        timeline_path = tmp_path / "existing.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p), duration_sec=6.0, rate=FPS),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is True
        clip = _first_v1_clip(output)
        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        assert ref.available_range is not None, (
            "The empty-V1-track load path (_load_and_validate_timeline ->"
            " _add_full_clip) must also wire available_range; got None."
        )
