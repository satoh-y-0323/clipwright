"""test_loudness.py — Tests for the loudness.py orchestration layer.

Mock policy:
  - Patch clipwright_loudness.loudness.inspect_media to supply MediaInfo.
  - Patch clipwright_loudness.loudness.measure_loudness to avoid calling ffmpeg.
  - No real ffmpeg/ffprobe binary is invoked.

Verification points:
  (a) timeline=None: new timeline, V1/A1 full-length keep clip, loudness directive in
      timeline-level metadata, save
  (b) timeline specified: load existing + partial update preserving other directives (e.g. denoise)
  (c) non-.otio extension -> INVALID_INPUT
  (d) media not found -> FILE_NOT_FOUND (basename)
  (e) output==media/timeline -> INVALID_INPUT
  (f) output in different dir from media -> INVALID_INPUT
  (g) no video -> UNSUPPORTED, no audio -> UNSUPPORTED
  (h) timeline specified with media != timeline source -> INVALID_INPUT
  (h2) valid timeline (V1+A1 normal case) passes validation
  (i) multiple sources / 2 Video tracks -> error
  (j) mode=loudnorm/peak: target and measured are stored in timeline-level directive
  (k) U-1: when loudnorm measured is not available, skip loudness directive
      (no loudness key added to existing metadata) and return warning
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_loudness.schemas import DetectLoudnessOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0
_TEST_BIT_RATE = 8_000_000  # test bit rate constant (not asserted)

# Normal measured result mock for loudnorm mode
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

# Normal measured result mock for peak mode
_FAKE_PEAK_MEASURED = {
    "measured": {
        "max_volume_db": -18.1,
    },
    "warnings": [],
}

# measured=None (not measurable, U-1) mock
_FAKE_MEASURED_NONE = {
    "measured": None,
    "warnings": [
        "Could not retrieve loudness measured values. loudness directive will not be written."
    ],
}


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Helper to construct a MediaInfo for testing."""
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


def _make_otio_timeline(
    media_path: Path,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    num_video_tracks: int = 1,
    num_audio_tracks: int = 1,
    sources: list[str] | None = None,
) -> otio.schema.Timeline:
    """Helper to construct a test OTIO Timeline."""
    tl = otio.schema.Timeline(name="test")

    for i in range(num_video_tracks):
        track = otio.schema.Track(name=f"V{i + 1}", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)

    for i in range(num_audio_tracks):
        track = otio.schema.Track(name=f"A{i + 1}", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(track)

    if num_video_tracks > 0:
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)

        if sources is None:
            sources = [str(media_path.resolve())]

        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        )

        for url in sources:
            ref = otio.schema.ExternalReference(target_url=url)
            clip = otio.schema.Clip(
                name=media_path.name,
                media_reference=ref,
                source_range=source_range,
            )
            v1.append(clip)

    return tl


def _save_timeline_to_file(tl: otio.schema.Timeline, path: Path) -> None:
    """Save a Timeline to a real file."""
    otio.adapters.write_to_file(tl, str(path))


# ===========================================================================
# (a) timeline=None: new timeline creation
# ===========================================================================


class TestNewTimeline:
    """A new timeline must be created when timeline=None."""

    def test_new_timeline_ok_result(self, tmp_path: Path) -> None:
        """Must return a success envelope."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=None,
            )

        assert result.ok is True

    def test_new_timeline_otio_file_created(self, tmp_path: Path) -> None:
        """An .otio file must be created at the output path."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert output.exists(), "output .otio was not created."

    def test_new_timeline_v1_has_clip(self, tmp_path: Path) -> None:
        """V1 of the created timeline must contain at least one clip."""
        from clipwright.otio_utils import load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1

    def test_new_timeline_has_loudness_metadata_at_timeline_level(
        self, tmp_path: Path
    ) -> None:
        """The created timeline must have a loudness directive in timeline-level metadata (ADR-L4)."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "loudness" in meta, (
            "timeline.metadata['clipwright']['loudness'] is missing (ADR-L4)."
        )
        loudness = meta["loudness"]
        assert loudness["kind"] == "loudness"
        assert loudness["scope"] == "track"

    def test_new_timeline_artifacts_contains_otio(self, tmp_path: Path) -> None:
        """result artifacts must contain role=timeline / format=otio."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        artifacts = result.artifacts
        timeline_arts = [a for a in artifacts if a.role == "timeline"]
        assert len(timeline_arts) >= 1


# ===========================================================================
# (b) timeline specified: load existing + partial update
# ===========================================================================


class TestExistingTimeline:
    """When timeline=path, the existing timeline must be loaded and updated."""

    def test_existing_timeline_loudness_metadata_updated(self, tmp_path: Path) -> None:
        """loudness directive must be appended to an existing timeline."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is True
        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        assert "loudness" in meta

    def test_existing_timeline_other_metadata_preserved(self, tmp_path: Path) -> None:
        """Other directives in the existing timeline (e.g. denoise) must be preserved."""
        from clipwright.otio_utils import (
            get_clipwright_metadata,
            load_timeline,
            set_clipwright_metadata,
        )

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        # Write an existing denoise directive
        set_clipwright_metadata(
            tl,
            {
                "denoise": {
                    "kind": "denoise",
                    "backend": "afftdn",
                    "tool": "clipwright-noise",
                    "version": "0.1.0",
                    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
                }
            },
        )
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        # denoise must still be present
        assert "denoise" in meta, (
            "Existing denoise directive was lost during loudness update."
        )


# ===========================================================================
# (c) non-.otio extension -> INVALID_INPUT
# ===========================================================================


class TestInvalidExtension:
    """INVALID_INPUT must be returned when output has a non-.otio extension."""

    @pytest.mark.parametrize("ext", [".mp4", ".json", ".txt", ".otioz", ""])
    def test_non_otio_extension_returns_invalid_input(
        self, tmp_path: Path, ext: str
    ) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / f"out{ext}"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# (c2) output parent directory does not exist -> INVALID_INPUT
# ===========================================================================


class TestOutputParentDirNotFound:
    """INVALID_INPUT must be returned when the output parent directory does not exist."""

    def test_output_parent_dir_not_exist_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "nonexistent_dir" / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT
        assert "output directory" in result.error.message


# ===========================================================================
# (d) media not found -> FILE_NOT_FOUND (basename)
# ===========================================================================


class TestMediaNotFound:
    """FILE_NOT_FOUND must be returned when the media file does not exist."""

    def test_missing_media_returns_file_not_found(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "nonexistent.mp4"
        output = tmp_path / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.FILE_NOT_FOUND

    def test_missing_media_message_contains_only_basename(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND message must not contain a directory path (DC-GP-005)."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "missing_video.mp4"
        output = tmp_path / "out.otio"
        full_dir = str(tmp_path)

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.FILE_NOT_FOUND
        error_msg = result.error.message
        assert full_dir not in error_msg, (
            f"DC-GP-005: absolute directory path '{full_dir}' found in message."
        )
        assert "missing_video.mp4" in error_msg


# ===========================================================================
# (e) output == media / output == timeline -> INVALID_INPUT
# ===========================================================================


class TestOutputConflict:
    """INVALID_INPUT must be returned when output is the same path as media or timeline."""

    def test_output_equals_media_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        result = detect_loudness(
            str(media), str(media), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_output_equals_timeline_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")

        result = detect_loudness(
            str(media),
            str(timeline_path),
            DetectLoudnessOptions(),
            timeline=str(timeline_path),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# (f) output in different dir from media -> INVALID_INPUT
# ===========================================================================


class TestOutputDifferentDir:
    """INVALID_INPUT must be returned when output is in a different directory from media."""

    def test_output_in_different_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_output_different_dir_hint_no_absolute_path(self, tmp_path: Path) -> None:
        """The same-dir error hint must not contain an absolute path (CWE-209)."""
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "media_src_dir"
        media_dir.mkdir()
        out_dir = tmp_path / "output_dir"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        hint = result.error.hint
        assert str(media_dir) not in hint
        assert str(tmp_path) not in hint


# ===========================================================================
# (g) no video -> UNSUPPORTED / no audio -> UNSUPPORTED
# ===========================================================================


class TestStreamRequirements:
    """Both video and audio streams are required."""

    def test_no_video_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=False, has_audio=True)

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_no_audio_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=True, has_audio=False)

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.UNSUPPORTED_OPERATION


# ===========================================================================
# (h) timeline specified with media != timeline source -> INVALID_INPUT
# ===========================================================================


class TestTimelineSourceMismatch:
    """INVALID_INPUT must be returned when the timeline source differs from media."""

    def test_different_source_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        other_media = tmp_path / "other_video.mp4"
        other_media.write_bytes(b"other")
        tl = _make_otio_timeline(other_media)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_mismatch_message_contains_basename_only(self, tmp_path: Path) -> None:
        """Mismatch error message must not contain an absolute path (DC-GP-005)."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        other_media = tmp_path / "other_video.mp4"
        other_media.write_bytes(b"other")
        tl = _make_otio_timeline(other_media)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        full_dir = str(tmp_path)
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is False
        assert result.error is not None
        error_msg = result.error.message
        assert full_dir not in error_msg


# ===========================================================================
# (h2) valid timeline (V1+A1 normal case) passes validation
# ===========================================================================


class TestTimelineSourceMatchPositive:
    """Must not incorrectly raise INVALID_INPUT when loading a timeline with the same media."""

    def test_same_source_timeline_passes_validation(self, tmp_path: Path) -> None:
        """A timeline created from the same media must pass validation (path normalization comparison)."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "silence.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is True, (
            f"Same-media timeline incorrectly returned INVALID_INPUT."
            f" error={result.error}"
        )

    def test_v1_a1_timeline_passes_validation(self, tmp_path: Path) -> None:
        """A V1+A1 (1 Video + 1 Audio) timeline must pass validation."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media, num_video_tracks=1, num_audio_tracks=1)
        timeline_path = tmp_path / "v1a1.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is True, (
            f"V1+A1 normal timeline returned INVALID_INPUT. error={result.error}"
        )


# ===========================================================================
# (i) multiple sources / 2 Video tracks -> error
# ===========================================================================


class TestTimelineValidation:
    """Timeline structural validation."""

    def test_multiple_sources_returns_error(self, tmp_path: Path) -> None:
        """An error must be returned when V1 contains clips from multiple sources."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        other = tmp_path / "other.mp4"
        tl = _make_otio_timeline(
            media,
            sources=[str(media.resolve()), str(other.resolve())],
        )
        timeline_path = tmp_path / "multi_src.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code in (
            ErrorCode.UNSUPPORTED_OPERATION,
            ErrorCode.INVALID_INPUT,
        )

    def test_two_video_tracks_returns_invalid_input(self, tmp_path: Path) -> None:
        """INVALID_INPUT must be returned when the timeline has two Video tracks."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media, num_video_tracks=2, num_audio_tracks=0)
        timeline_path = tmp_path / "two_video.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# (j) mode=loudnorm/peak: target and measured are stored in timeline-level directive
# ===========================================================================


class TestLoudnessModeMetadata:
    """Target and measured must be correctly stored in timeline-level metadata for each mode."""

    def test_loudnorm_mode_target_in_metadata(self, tmp_path: Path) -> None:
        """loudnorm mode: target (I/TP/LRA) must be present in timeline metadata."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness["mode"] == "loudnorm"
        assert "target" in loudness
        target = loudness["target"]
        # Default I/TP/LRA values must be present
        assert "i" in target or "I" in target

    def test_loudnorm_mode_measured_in_metadata(self, tmp_path: Path) -> None:
        """loudnorm mode: measured must be present in timeline metadata."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness.get("measured") is not None, (
            "loudnorm success path: measured is not present in timeline metadata."
        )
        measured = loudness["measured"]
        assert "input_i" in measured

    def test_peak_mode_target_in_metadata(self, tmp_path: Path) -> None:
        """peak mode: target (peak_db) must be present in timeline metadata."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_PEAK_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="peak"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness["mode"] == "peak"
        assert "target" in loudness
        target = loudness["target"]
        assert "peak_db" in target

    def test_peak_mode_measured_in_metadata(self, tmp_path: Path) -> None:
        """peak mode: measured (max_volume_db) must be present in timeline metadata."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_PEAK_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="peak"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness.get("measured") is not None
        measured = loudness["measured"]
        assert "max_volume_db" in measured


# ===========================================================================
# (k) U-1: when loudnorm measured is not available, skip loudness directive and return warning
# ===========================================================================


class TestU1MeasuredNone:
    """U-1: when measured=None, the loudness directive must not be written and a warning must be returned."""

    def test_loudnorm_measured_none_no_loudness_in_metadata(
        self, tmp_path: Path
    ) -> None:
        """When measured=None, the loudness key must not be added to timeline metadata (U-1)."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_MEASURED_NONE,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        # ok=True (detect itself succeeds)
        assert result.ok is True, (
            "U-1: detect must succeed (ok=True) even when measured=None."
        )

        # The timeline file itself must still be created
        assert output.exists(), (
            "U-1: the timeline file must be created even when measured=None."
        )
        # The loudness key must not be added to the timeline
        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "loudness" not in meta, (
            "U-1: loudness directive must not be written to timeline metadata"
            " when measured=None (DC-AM-003)."
        )

    def test_loudnorm_measured_none_warning_in_result(self, tmp_path: Path) -> None:
        """When measured=None, result warnings must contain a warning (U-1)."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_MEASURED_NONE,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        warnings = result.warnings
        assert len(warnings) > 0, (
            "U-1: result.warnings must contain a warning when measured=None (DC-AM-003)."
        )


# ===========================================================================
# SR L-2: _load_and_validate_timeline boundary check (source outside timeline parent dir)
# ===========================================================================


class TestTimelineSourceBoundaryCheck:
    """SR L-2: target_url in the timeline must be within the timeline parent directory."""

    def test_source_outside_timeline_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """PATH_NOT_ALLOWED must be returned when target_url points outside the timeline parent dir (SR-r2 L-1)."""
        import opentimelineio as otio

        from clipwright_loudness.loudness import detect_loudness

        # timeline is saved in a subdir; source points to a different directory
        subdir = tmp_path / "project"
        subdir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        media = subdir / "video.mp4"
        media.write_bytes(b"dummy")

        outside_media = outside_dir / "other.mp4"
        outside_media.write_bytes(b"dummy")

        # Save a timeline in subdir with a clip pointing to outside_media
        tl = otio.schema.Timeline(name="test")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)
        ref = otio.schema.ExternalReference(target_url=str(outside_media.resolve()))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 30.0),
            duration=otio.opentime.RationalTime(300.0, 30.0),
        )
        clip = otio.schema.Clip(
            name="other.mp4",
            media_reference=ref,
            source_range=source_range,
        )
        track.append(clip)
        timeline_path = subdir / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = subdir / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is False
        assert result.error is not None
        # Out-of-boundary path: expect PATH_NOT_ALLOWED as in render.py (SR-r2 L-1)
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED
