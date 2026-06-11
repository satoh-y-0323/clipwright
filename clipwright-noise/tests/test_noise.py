"""test_noise.py — Tests for the noise.py orchestration layer.

Mock strategy:
  - Patch clipwright_noise.noise.inspect_media to supply MediaInfo.
  - Patch clipwright_noise.noise.measure_noise to avoid calling astats.
  - No actual ffmpeg/ffprobe binaries are called.

Verification points (v3 design §1.1 / DC-AS-002 / B-4 / B-5 / DC-GP-003 / DC-GP-005):
  (a) timeline=None: new timeline, V1 full-length keep clip (target_url=absolute path), denoise annotation, save
  (b) timeline specified: load existing + partial update preserving existing annotations
  (c) Extension other than .otio → INVALID_INPUT
  (d) media not found → FILE_NOT_FOUND (basename only; DC-GP-005)
  (e) output==media → INVALID_INPUT / output==timeline → INVALID_INPUT
  (f) output in different dir from media → INVALID_INPUT (DC-AS-002)
  (g) no video → UNSUPPORTED / no audio → UNSUPPORTED
  (h) timeline specified with media ≠ timeline source → INVALID_INPUT
  (h2) Positive path: loading a silence-origin real timeline with the same media passes (B-4)
  (i) Multiple sources in timeline → UNSUPPORTED / Two Video tracks → INVALID_INPUT
  (i2) V1+A1 normal timeline passes (B-5)
  (j) backend=deepfilternet → params={} annotation + warning containing "render not supported"
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_noise.schemas import DetectNoiseOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0
_FAKE_MEASURE_RESULT = {
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
    "measured_noise_floor_db": -50.0,
    "warnings": [],
}
_FAKE_MEASURE_RESULT_DFN = {
    "params": {},
    "measured_noise_floor_db": -50.0,
    "warnings": [],
}


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Helper to build a MediaInfo for testing."""
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
        bit_rate=8_000_000,
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
    """Helper to build a test OTIO Timeline.

    When sources is specified, clips with multiple sources are added to V1.
    When sources=None, a single clip with media_path.resolve() is added.
    """
    tl = otio.schema.Timeline(name="test")

    # Add num_video_tracks Video tracks
    for i in range(num_video_tracks):
        track = otio.schema.Track(name=f"V{i + 1}", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)

    # Add num_audio_tracks Audio tracks
    for i in range(num_audio_tracks):
        track = otio.schema.Track(name=f"A{i + 1}", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(track)

    # Add clips to V1
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
    """Save a Timeline to an actual file."""
    otio.adapters.write_to_file(tl, str(path))


# ===========================================================================
# (a) timeline=None: new timeline generation
# ===========================================================================


class TestNewTimeline:
    """Verify that a new timeline is generated when timeline=None."""

    def test_new_timeline_ok_result(self, tmp_path: Path) -> None:
        """A success envelope must be returned."""
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_noise.noise.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=None,
            )

        assert result["ok"] is True

    def test_new_timeline_otio_file_created(self, tmp_path: Path) -> None:
        """An .otio file must be created at the output path."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        assert output.exists(), "Output .otio was not created."

    def test_new_timeline_v1_has_clip(self, tmp_path: Path) -> None:
        """The generated timeline's V1 must contain at least one clip."""
        from clipwright.otio_utils import load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1

    def test_new_timeline_clip_target_url_is_absolute(self, tmp_path: Path) -> None:
        """The V1 clip target_url must be the absolute path of the media file (DC-AS-002)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        abs_media = str(media.resolve())
        for clip in clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)
            # target_url must contain the absolute path (compare via resolve())
            ref_path = Path(clip.media_reference.target_url)
            try:
                resolved = str(ref_path.resolve())
            except OSError:
                resolved = str(ref_path.absolute())
            assert resolved == abs_media

    def test_new_timeline_has_denoise_metadata(self, tmp_path: Path) -> None:
        """The generated timeline's metadata["clipwright"]["denoise"] must be set."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "denoise" in meta, (
            "timeline.metadata['clipwright']['denoise'] is missing."
        )
        denoise = meta["denoise"]
        assert denoise["kind"] == "denoise"
        assert denoise["backend"] == "afftdn"

    def test_new_timeline_artifacts_contains_otio(self, tmp_path: Path) -> None:
        """result artifacts must include role=timeline / format=otio."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        artifacts = result.get("artifacts", [])
        timeline_arts = [
            a
            for a in artifacts
            if (
                (isinstance(a, dict) and a.get("role") == "timeline")
                or (hasattr(a, "role") and a.role == "timeline")
            )
        ]
        assert len(timeline_arts) >= 1


# ===========================================================================
# (b) timeline specified: load existing timeline + partial update
# ===========================================================================


class TestExistingTimeline:
    """Verify that an existing timeline is loaded and updated when timeline=path."""

    def test_existing_timeline_denoise_metadata_updated(self, tmp_path: Path) -> None:
        """The denoise annotation must be appended to an existing timeline."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Create a timeline similar to one generated by silence
        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        assert "denoise" in meta

    def test_existing_timeline_other_metadata_preserved(self, tmp_path: Path) -> None:
        """Non-denoise annotations on the existing timeline must be preserved."""
        from clipwright.otio_utils import (
            get_clipwright_metadata,
            load_timeline,
            set_clipwright_metadata,
        )

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        # Write existing annotations (e.g., silence_intervals generated by silence)
        set_clipwright_metadata(tl, {"silence_intervals": [{"start": 2.0, "end": 4.0}]})
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        # silence_intervals must be preserved
        assert "silence_intervals" in meta, (
            "Existing silence_intervals was lost after the denoise update."
        )


# ===========================================================================
# (c) Extension other than .otio → INVALID_INPUT
# ===========================================================================


class TestInvalidExtension:
    """INVALID_INPUT must be returned when output has an extension other than .otio."""

    @pytest.mark.parametrize("ext", [".mp4", ".json", ".txt", ".otioz", ""])
    def test_non_otio_extension_returns_invalid_input(
        self, tmp_path: Path, ext: str
    ) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / f"out{ext}"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (d) media not found → FILE_NOT_FOUND (basename only; DC-GP-005)
# ===========================================================================


class TestMediaNotFound:
    """FILE_NOT_FOUND must be returned when the media file does not exist."""

    def test_missing_media_returns_file_not_found(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "nonexistent.mp4"
        output = tmp_path / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_missing_media_message_contains_only_basename(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND message must not contain a directory path (DC-GP-005)."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "missing_video.mp4"
        output = tmp_path / "out.otio"
        full_dir = str(tmp_path)

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg, (
            f"DC-GP-005: Absolute directory path '{full_dir}' is present in the message."
        )
        assert "missing_video.mp4" in error_msg


# ===========================================================================
# (e) output == media / output == timeline → INVALID_INPUT
# ===========================================================================


class TestOutputConflict:
    """INVALID_INPUT must be returned when output is the same path as media or timeline."""

    def test_output_equals_media_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        # output and media have the same path
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")

        result = detect_noise(
            str(media), str(media), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_equals_timeline_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")

        # output == timeline
        result = detect_noise(
            str(media),
            str(timeline_path),  # output = timeline
            DetectNoiseOptions(),
            timeline=str(timeline_path),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (f) output in different dir from media → INVALID_INPUT (DC-AS-002)
# ===========================================================================


class TestOutputDifferentDir:
    """INVALID_INPUT must be returned when output is in a different directory from media (DC-AS-002)."""

    def test_output_in_different_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_different_dir_hint_does_not_contain_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """The same-dir error hint must not contain an absolute path (SR-M-2 / CWE-209)."""
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "media_src_dir"
        media_dir.mkdir()
        out_dir = tmp_path / "output_dir"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        hint = result["error"].get("hint", "")
        # hint must not contain an absolute directory path (CWE-209)
        assert str(media_dir) not in hint, (
            f"SR-M-2: Absolute media directory path '{media_dir}' is present in the hint."
        )
        assert str(tmp_path) not in hint, (
            f"SR-M-2: tmp_path '{tmp_path}' is present in the hint."
        )


# ===========================================================================
# (g) no video → UNSUPPORTED / no audio → UNSUPPORTED
# ===========================================================================


class TestStreamRequirements:
    """Both video and audio are required (ADR-N8 / DC-AS-003)."""

    def test_no_video_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=False, has_audio=True)

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_no_audio_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=True, has_audio=False)

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION


# ===========================================================================
# (h) timeline specified with media ≠ timeline source → INVALID_INPUT
# ===========================================================================


class TestTimelineSourceMismatch:
    """INVALID_INPUT must be returned when the timeline source differs from media (DC-AM-003)."""

    def test_different_source_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Create a timeline with a different file as its source
        other_media = tmp_path / "other_video.mp4"
        other_media.write_bytes(b"other")
        tl = _make_otio_timeline(other_media)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_mismatch_message_contains_basename_only(self, tmp_path: Path) -> None:
        """The mismatch error message must not contain absolute paths (DC-GP-005)."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

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

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg


# ===========================================================================
# (h2) Positive path: silence-origin real timeline + same media passes (B-4)
# ===========================================================================


class TestTimelineSourceMatchPositive:
    """Verify that loading a same-media timeline does not produce a spurious INVALID_INPUT (B-4)."""

    def test_same_source_timeline_passes_validation(self, tmp_path: Path) -> None:
        """Loading a silence-origin OTIO timeline with the same media must pass (B-4).

        Verifies that Path.resolve() normalized comparison does not produce false positives.
        """
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Create a timeline with media.resolve() absolute path as its source
        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "silence.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"B-4: Same-media timeline produced a spurious INVALID_INPUT."
            f" error={result.get('error')}"
        )


# ===========================================================================
# (i) Multiple sources → UNSUPPORTED / Two Video tracks → INVALID_INPUT
# ===========================================================================


class TestTimelineValidation:
    """Timeline structure validation (DC-AM-004 / B-5)."""

    def test_multiple_sources_returns_unsupported(self, tmp_path: Path) -> None:
        """UNSUPPORTED_OPERATION must be returned when V1 contains clips from multiple sources."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Multiple sources (two different target_urls)
        other = tmp_path / "other.mp4"
        tl = _make_otio_timeline(
            media,
            sources=[str(media.resolve()), str(other.resolve())],
        )
        timeline_path = tmp_path / "multi_src.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        # Multiple sources → UNSUPPORTED_OPERATION or INVALID_INPUT
        assert result["error"]["code"] in (
            ErrorCode.UNSUPPORTED_OPERATION,
            ErrorCode.INVALID_INPUT,
        )

    def test_two_video_tracks_returns_invalid_input(self, tmp_path: Path) -> None:
        """INVALID_INPUT must be returned when the timeline has two Video tracks (B-5)."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media, num_video_tracks=2, num_audio_tracks=0)
        timeline_path = tmp_path / "two_video.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (i2) V1+A1 normal timeline passes (B-5)
# ===========================================================================


class TestV1A1TimelinePositive:
    """A V1+A1 (one Video + one Audio) timeline must pass validation (B-5)."""

    def test_v1_a1_timeline_passes_validation(self, tmp_path: Path) -> None:
        """A silence-origin V1+A1 timeline must pass (Audio track is allowed)."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # V1+A1 (one Video + one Audio)
        tl = _make_otio_timeline(media, num_video_tracks=1, num_audio_tracks=1)
        timeline_path = tmp_path / "v1a1.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"B-5: V1+A1 normal timeline produced INVALID_INPUT."
            f" error={result.get('error')}"
        )


# ===========================================================================
# (i3) Empty V1 timeline → full-length clip appended, becomes renderable (CR-M-1)
# ===========================================================================


class TestEmptyV1Timeline:
    """When V1 of an existing timeline is empty, a full-length keep clip must be appended so ok=True (CR-M-1).

    Prevents render's resolve_kept_ranges from rejecting with INVALID_INPUT (zero clips).
    _load_and_validate_timeline appends a full-length clip equivalent to creating a new timeline.
    """

    def test_empty_v1_timeline_adds_clip_and_succeeds(self, tmp_path: Path) -> None:
        """Passing an existing timeline with an empty V1 must append a full-length clip and return ok=True."""
        from clipwright.otio_utils import load_timeline

        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Manually create a timeline with an empty V1 (no clips)
        empty_tl = otio.schema.Timeline(name="empty")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        empty_tl.tracks.append(v1)
        timeline_path = tmp_path / "empty_v1.otio"
        otio.adapters.write_to_file(empty_tl, str(timeline_path))

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"CR-M-1: Empty V1 timeline produced INVALID_INPUT."
            f" error={result.get('error')}"
        )

        # V1 in the output timeline must have a clip appended
        out_tl = load_timeline(str(output))
        out_v1 = next(t for t in out_tl.tracks if t.kind == otio.schema.TrackKind.Video)
        out_clips = [it for it in out_v1 if isinstance(it, otio.schema.Clip)]
        assert len(out_clips) >= 1, (
            "CR-M-1: No full-length clip was appended to the empty V1 timeline."
        )


# ===========================================================================
# (j) backend=deepfilternet → params={} annotation + warning (DC-GP-003)
# ===========================================================================


class TestDeepfilternetBackend:
    """When backend=deepfilternet, params={} annotation and a warning must be emitted (DC-GP-003)."""

    def test_deepfilternet_sets_empty_params_in_metadata(self, tmp_path: Path) -> None:
        """The denoise annotation params must be {} (DC-AM-002)."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT_DFN,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(backend="deepfilternet"),
                timeline=None,
            )

        assert result["ok"] is True
        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "denoise" in meta
        assert meta["denoise"]["params"] == {}, (
            "DC-AM-002: deepfilternet params must be {}."
        )

    def test_deepfilternet_warning_mentions_render_unsupported(
        self, tmp_path: Path
    ) -> None:
        """warnings must contain a message indicating that render application is not supported (DC-GP-003)."""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT_DFN,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(backend="deepfilternet"),
                timeline=None,
            )

        warnings = result.get("warnings", [])
        assert len(warnings) > 0, (
            "DC-GP-003: warnings must not be empty when deepfilternet is selected."
        )
        warning_text = " ".join(warnings)
        # Must contain keywords indicating render is not supported
        assert any(
            kw in warning_text for kw in ["render", "not supported", "afftdn", "future"]
        ), f"DC-GP-003: warnings do not mention render not supported: {warnings}"

    def test_deepfilternet_backend_stored_in_metadata(self, tmp_path: Path) -> None:
        """The denoise annotation backend must be "deepfilternet"."""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT_DFN,
            ),
        ):
            detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(backend="deepfilternet"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert meta["denoise"]["backend"] == "deepfilternet"
