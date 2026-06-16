"""test_extract.py — Tests for extract.py orchestration (TDD Red phase).

Target API:
  clipwright_frames.extract.extract_frames(
      media: str,
      output_dir: str,
      options: ExtractFramesOptions,
  ) -> ToolResult

Mocking policy:
  - Patch clipwright_frames.extract.inspect_media to supply MediaInfo.
  - Patch clipwright_frames.extract.run to control subprocess output.
  - No real ffmpeg binaries are called.
  - For scene mode: patch clipwright_frames.extract.load_timeline to supply OTIO.

Verification aspects:
  (1)  Input validation: no video stream -> ok=False / UNSUPPORTED_OPERATION
  (2)  Input validation: output_dir does not exist -> ok=False / INVALID_INPUT
  (3)  Input validation: media missing (FILE_NOT_FOUND from inspect_media) -> ok=False
       / FILE_NOT_FOUND, basename only in error message
  (4)  Scene mode: scene_timeline unspecified or non-existent -> INVALID_INPUT
  (5)  Scene mode: load_timeline raises OTIO_ERROR -> OTIO_ERROR propagated
  (6)  Scene mode: scene_timeline extension not .otio -> INVALID_INPUT
  (7)  Success paths (interval/scene/timestamps) — run mocked, image files created
       artificially -> artifacts contains frames.otio (role=timeline, format=otio)
       and frames.json (role=manifest, format=json), data.frame_count is correct
  (8)  interval > duration -> ok=True, warnings contain "interval_sec exceeds"
       phrase, empty frames.otio and frames.json produced
  (9)  Scene mode: 0 markers found -> ok=True, warnings contain "No scene_boundary
       markers found", empty output
  (10) timestamps mode: out-of-range timestamps -> warnings contain "skipped",
       in-range timestamps extracted
  (11) subprocess failure -> safe_subprocess_message used, no absolute path exposed
  (12) frames.json schema: {"count", "mode", "format", "frames":[{"index",
       "timestamp_sec", "path"}]} where path is absolute path inside output_dir
  (13) frames.otio markers: metadata["clipwright"]["kind"]=="extracted_frame" and
       "timestamp_sec" key present
  (14) artifacts format: {"role", "path", "format"} —
       frames.otio -> role="timeline"/format="otio",
       frames.json -> role="manifest"/format="json"
  (15) build_single_frame_command returns list[str|float]; extract passes str()
       of each element to run — captured run args are all str
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_frames.schemas import ExtractFramesOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    audio_streams: int = 1,
) -> MediaInfo:
    """Construct a MediaInfo for tests (mock return value for inspect_media)."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
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


def _opts(
    mode: str = "interval",
    interval_sec: float = 5.0,
    scene_timeline: str | None = None,
    timestamps: list[float] | None = None,
    fmt: str = "jpeg",
    quality: int = 2,
    max_width: int | None = None,
) -> ExtractFramesOptions:
    return ExtractFramesOptions(
        mode=mode,  # type: ignore[arg-type]
        interval_sec=interval_sec,
        scene_timeline=scene_timeline,
        timestamps=timestamps if timestamps is not None else [],
        format=fmt,  # type: ignore[arg-type]
        quality=quality,
        max_width=max_width,
    )


def _make_fake_run(output_dir: Path, timestamps: list[float], fmt: str = "jpeg") -> Any:
    """Return a side_effect for run() that also creates fake image files.

    The fake run creates image files in output_dir so that the orchestration
    can find them during manifest assembly.
    """
    created: list[Path] = []

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        # Create a fake output file for each unique output path found in cmd.
        # build_single_frame_command puts the out_path as the last element.
        # build_fps_command puts the pattern as the last element (skip for interval).
        if cmd:
            last = cmd[-1]
            last_path = Path(last)
            if last_path.suffix in {".jpg", ".png", ".jpeg"}:
                last_path.parent.mkdir(parents=True, exist_ok=True)
                last_path.write_bytes(b"FAKE_IMAGE")
                created.append(last_path)
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return _impl


def _make_scene_timeline_with_markers(
    tmp_path: Path,
    timestamps_sec: list[float],
    rate: float = FPS,
) -> str:
    """Create a real OTIO file with scene_boundary markers and return its path."""
    tl = otio.schema.Timeline(name="scene_test")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)

    for ts in timestamps_sec:
        rt = otio.opentime.RationalTime(ts * rate, rate)
        dur = otio.opentime.RationalTime(0.0, rate)
        marker = otio.schema.Marker(
            name="scene_boundary",
            marked_range=otio.opentime.TimeRange(start_time=rt, duration=dur),
        )
        marker.metadata["clipwright"] = {"kind": "scene_boundary"}
        v1.markers.append(marker)

    scene_path = str(tmp_path / "scene.otio")
    otio.adapters.write_to_file(tl, scene_path)
    return scene_path


# ===========================================================================
# (1) No video stream -> UNSUPPORTED_OPERATION
# ===========================================================================


class TestNoVideoStream:
    """Media with no video stream must return UNSUPPORTED_OPERATION."""

    def test_audio_only_returns_unsupported_operation(self, tmp_path: Path) -> None:
        """Audio-only MediaInfo (no video stream) -> ok=False / UNSUPPORTED_OPERATION."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "audio.mp3")
        Path(media).touch()
        media_info = _make_media_info(
            path=media, has_video=False, audio_streams=1, duration_sec=10.0
        )

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(media, str(tmp_path), _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.UNSUPPORTED_OPERATION


# ===========================================================================
# (2) output_dir does not exist -> INVALID_INPUT
# ===========================================================================


class TestOutputDirNotExist:
    """Non-existent output_dir must return INVALID_INPUT (no auto-create)."""

    def test_missing_output_dir_returns_invalid_input(self, tmp_path: Path) -> None:
        """output_dir that does not exist -> ok=False / INVALID_INPUT."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        missing_dir = str(tmp_path / "does_not_exist")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(media, missing_dir, _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_output_dir_is_a_file_returns_invalid_input(self, tmp_path: Path) -> None:
        """output_dir pointing to a file (not a directory) -> ok=False / INVALID_INPUT."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        not_a_dir = tmp_path / "not_a_dir.txt"
        not_a_dir.write_text("hello")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(media, str(not_a_dir), _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# (3) Media missing (FILE_NOT_FOUND from inspect_media)
# ===========================================================================


class TestMediaNotFound:
    """Missing media must propagate FILE_NOT_FOUND with basename only."""

    def test_nonexistent_media_returns_file_not_found(self, tmp_path: Path) -> None:
        """inspect_media raises FILE_NOT_FOUND -> result is ok=False / FILE_NOT_FOUND."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "nonexistent.mp4")

        with patch(
            "clipwright_frames.extract.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = extract_frames(media, str(tmp_path), _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.FILE_NOT_FOUND

    def test_error_message_exposes_basename_only(self, tmp_path: Path) -> None:
        """Error message must not expose the full directory path."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "secret_path" / "video.mp4")
        full_dir = str(tmp_path / "secret_path")

        with patch(
            "clipwright_frames.extract.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = extract_frames(media, str(tmp_path), _opts())

        assert result.ok is False
        error_msg = result.error.message if result.error else ""
        assert full_dir not in error_msg


# ===========================================================================
# (4) Scene mode: scene_timeline validation
# ===========================================================================


class TestSceneTimelineValidation:
    """scene_timeline validation for scene mode."""

    def test_scene_mode_without_scene_timeline_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """mode='scene' with scene_timeline=None -> ok=False / INVALID_INPUT."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(
                media, str(tmp_path), _opts(mode="scene", scene_timeline=None)
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_scene_mode_with_nonexistent_scene_timeline_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """mode='scene' with scene_timeline pointing to missing file -> INVALID_INPUT."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        missing_otio = str(tmp_path / "missing.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(
                media,
                str(tmp_path),
                _opts(mode="scene", scene_timeline=missing_otio),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_scene_mode_with_non_otio_extension_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """mode='scene' with scene_timeline having extension other than .otio -> INVALID_INPUT."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        bad_timeline = tmp_path / "scene.json"  # wrong extension
        bad_timeline.write_text("{}")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(
                media,
                str(tmp_path),
                _opts(mode="scene", scene_timeline=str(bad_timeline)),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# (5) Scene mode: load_timeline raises OTIO_ERROR
# ===========================================================================


class TestSceneModeOtioError:
    """load_timeline failure must propagate as OTIO_ERROR."""

    def test_load_timeline_failure_returns_otio_error(self, tmp_path: Path) -> None:
        """When load_timeline raises OTIO_ERROR, result is ok=False / OTIO_ERROR."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        scene_file = tmp_path / "scene.otio"
        scene_file.write_text("INVALID OTIO CONTENT")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch(
                "clipwright_frames.extract.load_timeline",
                side_effect=ClipwrightError(
                    code=ErrorCode.OTIO_ERROR,
                    message="Failed to load OTIO file: scene.otio",
                    hint="Specify a valid .otio timeline file.",
                ),
            ),
        ):
            result = extract_frames(
                media,
                str(tmp_path),
                _opts(mode="scene", scene_timeline=str(scene_file)),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.OTIO_ERROR


# ===========================================================================
# (7) Success paths
# ===========================================================================


class TestSuccessPathInterval:
    """Normal success case for interval mode."""

    def test_interval_mode_returns_ok_with_two_artifacts(self, tmp_path: Path) -> None:
        """Interval mode success -> ok=True, 2 artifacts (frames.otio + frames.json)."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        # Pre-create fake frame files that the orchestration will discover
        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")
        (out_dir / "frame_00001.jpg").write_bytes(b"FAKE")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is True
        assert result.artifacts is not None
        assert len(result.artifacts) == 2

    def test_interval_mode_artifacts_have_correct_roles_and_formats(
        self, tmp_path: Path
    ) -> None:
        """Artifacts must have role=timeline/format=otio and role=manifest/format=json."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is True
        assert result.artifacts is not None

        roles = {
            a.role if hasattr(a, "role") else a.get("role", "")
            for a in result.artifacts
        }
        formats = {
            a.format if hasattr(a, "format") else a.get("format", "")
            for a in result.artifacts
        }
        assert "timeline" in roles
        assert "manifest" in roles
        assert "otio" in formats
        assert "json" in formats

    def test_interval_mode_data_has_frame_count(self, tmp_path: Path) -> None:
        """result.data must have a 'frame_count' key with the number of extracted frames."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")
        (out_dir / "frame_00001.jpg").write_bytes(b"FAKE")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is True
        assert result.data is not None
        assert "frame_count" in result.data


class TestSuccessPathScene:
    """Normal success case for scene mode."""

    def test_scene_mode_returns_ok_with_two_artifacts(self, tmp_path: Path) -> None:
        """Scene mode success -> ok=True, 2 artifacts."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [2.0, 5.0])

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            # Create a fake output image (last element is the output path)
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="scene", scene_timeline=scene_otio),
            )

        assert result.ok is True
        assert result.artifacts is not None
        assert len(result.artifacts) == 2

    def test_scene_mode_frame_count_matches_marker_count(self, tmp_path: Path) -> None:
        """frame_count must equal the number of scene_boundary markers."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [1.0, 4.0, 7.0])

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="scene", scene_timeline=scene_otio),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 3


class TestSuccessPathTimestamps:
    """Normal success case for timestamps mode."""

    def test_timestamps_mode_returns_ok_with_two_artifacts(
        self, tmp_path: Path
    ) -> None:
        """Timestamps mode success -> ok=True, 2 artifacts."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[2.0, 5.0, 8.0]),
            )

        assert result.ok is True
        assert result.artifacts is not None
        assert len(result.artifacts) == 2

    def test_timestamps_mode_frame_count_matches_kept_timestamps(
        self, tmp_path: Path
    ) -> None:
        """frame_count must equal the count of in-range timestamps."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[2.0, 5.0, 8.0]),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 3


# ===========================================================================
# (8) interval > duration -> ok=True + warning + empty output
# ===========================================================================


class TestIntervalExceedsDuration:
    """interval_sec exceeding media duration: ok=True with warning, empty output."""

    def test_interval_exceeds_duration_returns_ok_with_warning(
        self, tmp_path: Path
    ) -> None:
        """interval_sec > duration_sec -> ok=True, warnings contains 'interval_sec'."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=5.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(
                media, str(out_dir), _opts(mode="interval", interval_sec=10.0)
            )

        assert result.ok is True
        assert result.warnings is not None
        assert any(
            "interval_sec" in w.lower() or "interval" in w.lower()
            for w in result.warnings
        )

    def test_interval_exceeds_duration_produces_empty_artifacts(
        self, tmp_path: Path
    ) -> None:
        """interval_sec > duration -> 2 empty artifact files are still produced."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=5.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(
                media, str(out_dir), _opts(mode="interval", interval_sec=10.0)
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 0
        assert result.artifacts is not None
        assert len(result.artifacts) == 2


# ===========================================================================
# (9) Scene mode: 0 markers found
# ===========================================================================


class TestSceneModeZeroMarkers:
    """Scene mode with no scene_boundary markers: ok=True + warning + empty output."""

    def test_zero_scene_markers_returns_ok_with_warning(self, tmp_path: Path) -> None:
        """0 scene_boundary markers -> ok=True, warnings contain 'No scene_boundary markers'."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        # Create OTIO with no markers
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [])

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="scene", scene_timeline=scene_otio),
            )

        assert result.ok is True
        assert result.warnings is not None
        assert any(
            "scene_boundary" in w.lower() or "marker" in w.lower()
            for w in result.warnings
        )

    def test_zero_scene_markers_frame_count_is_zero(self, tmp_path: Path) -> None:
        """0 scene_boundary markers -> frame_count == 0."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [])

        with patch("clipwright_frames.extract.inspect_media", return_value=media_info):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="scene", scene_timeline=scene_otio),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 0


# ===========================================================================
# (10) timestamps mode: out-of-range timestamps produce warnings
# ===========================================================================


class TestTimestampsOutOfRange:
    """Out-of-range timestamps produce warnings; in-range timestamps are extracted."""

    def test_out_of_range_timestamps_produce_warnings(self, tmp_path: Path) -> None:
        """Timestamps outside [0, duration) produce warnings and are skipped."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                # 2.0 is valid; 15.0 and -1.0 are out of range
                _opts(mode="timestamps", timestamps=[2.0, 15.0, -1.0]),
            )

        assert result.ok is True
        assert result.warnings is not None
        warning_text = " ".join(result.warnings).lower()
        assert (
            "skip" in warning_text or "exceed" in warning_text or "out" in warning_text
        )

    def test_only_in_range_timestamps_are_extracted(self, tmp_path: Path) -> None:
        """Only in-range timestamps (0 <= ts < duration) are extracted."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        run_call_count = [0]

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            run_call_count[0] += 1
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[2.0, 15.0, -1.0]),
            )

        assert result.ok is True
        assert result.data is not None
        # Only 2.0 is in-range; run() should be called once
        assert result.data.get("frame_count") == 1


# ===========================================================================
# (11) Subprocess failure -> safe_subprocess_message
# ===========================================================================


class TestSubprocessFailure:
    """subprocess failure must use safe_subprocess_message; no absolute paths exposed."""

    def test_subprocess_failure_returns_error_result(self, tmp_path: Path) -> None:
        """run() raising SUBPROCESS_FAILED -> ok=False."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="Command failed",
                hint="Check ffmpeg.",
            )

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fail),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is False
        assert result.error is not None
        assert result.error.code in (
            ErrorCode.SUBPROCESS_FAILED,
            ErrorCode.INTERNAL,
        )

    def test_subprocess_error_does_not_expose_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """Error message from subprocess failure must not leak raw absolute paths."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        secret_path = "/home/internal_user/private/path"
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed: {secret_path}",
                hint="Check ffmpeg.",
            )

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fail),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is False
        error_msg = result.error.message if result.error else ""
        assert secret_path not in error_msg


# ===========================================================================
# (12) frames.json schema verification
# ===========================================================================


class TestFramesJsonSchema:
    """frames.json must have the correct schema."""

    def test_frames_json_has_required_top_level_keys(self, tmp_path: Path) -> None:
        """frames.json must contain count, mode, format, frames keys."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is True
        assert result.artifacts is not None

        # Find the manifest artifact and load its file
        manifest_path: str | None = None
        for a in result.artifacts:
            role = a.role if hasattr(a, "role") else a.get("role", "")
            if role == "manifest":
                manifest_path = a.path if hasattr(a, "path") else a.get("path", "")
                break

        assert manifest_path is not None, "No manifest artifact found"
        assert Path(str(manifest_path)).exists(), (
            f"Manifest file does not exist: {manifest_path}"
        )

        with open(str(manifest_path), encoding="utf-8") as f:
            manifest = json.load(f)

        assert "count" in manifest
        assert "mode" in manifest
        assert "format" in manifest
        assert "frames" in manifest
        assert isinstance(manifest["frames"], list)

    def test_frames_json_frame_entries_have_required_keys(self, tmp_path: Path) -> None:
        """Each frame entry in frames.json must have index, timestamp_sec, path."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[2.0, 5.0]),
            )

        assert result.ok is True
        assert result.artifacts is not None

        manifest_path: str | None = None
        for a in result.artifacts:
            role = a.role if hasattr(a, "role") else a.get("role", "")
            if role == "manifest":
                manifest_path = a.path if hasattr(a, "path") else a.get("path", "")
                break

        assert manifest_path is not None
        with open(str(manifest_path), encoding="utf-8") as f:
            manifest = json.load(f)

        assert len(manifest["frames"]) == 2
        for frame in manifest["frames"]:
            assert "index" in frame
            assert "timestamp_sec" in frame
            assert "path" in frame
            assert isinstance(frame["index"], int)
            assert isinstance(frame["timestamp_sec"], float)

    def test_frames_json_path_is_absolute_inside_output_dir(
        self, tmp_path: Path
    ) -> None:
        """Each frame path in frames.json must be an absolute path under output_dir."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[3.0]),
            )

        assert result.ok is True
        assert result.artifacts is not None

        manifest_path: str | None = None
        for a in result.artifacts:
            role = a.role if hasattr(a, "role") else a.get("role", "")
            if role == "manifest":
                manifest_path = a.path if hasattr(a, "path") else a.get("path", "")
                break

        assert manifest_path is not None
        with open(str(manifest_path), encoding="utf-8") as f:
            manifest = json.load(f)

        for frame in manifest["frames"]:
            frame_path = Path(frame["path"])
            assert frame_path.is_absolute(), f"path must be absolute: {frame_path}"
            # path must be inside output_dir
            assert str(frame_path).startswith(str(out_dir)), (
                f"path {frame_path} is not under output_dir {out_dir}"
            )


# ===========================================================================
# (13) frames.otio marker metadata
# ===========================================================================


class TestFramesOtioMarkers:
    """frames.otio markers must have kind='extracted_frame' and 'timestamp_sec'."""

    def test_otio_markers_have_kind_extracted_frame(self, tmp_path: Path) -> None:
        """Each marker in frames.otio must have metadata['clipwright']['kind']=='extracted_frame'."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[2.0, 5.0]),
            )

        assert result.ok is True
        assert result.artifacts is not None

        otio_path: str | None = None
        for a in result.artifacts:
            role = a.role if hasattr(a, "role") else a.get("role", "")
            if role == "timeline":
                otio_path = a.path if hasattr(a, "path") else a.get("path", "")
                break

        assert otio_path is not None
        tl = otio.adapters.read_from_file(str(otio_path))
        assert isinstance(tl, otio.schema.Timeline)

        all_markers: list[otio.schema.Marker] = []
        for track in tl.tracks:
            all_markers.extend(track.markers)

        assert len(all_markers) == 2
        for marker in all_markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("kind") == "extracted_frame", (
                f"Expected kind='extracted_frame', got {cw.get('kind')!r}"
            )

    def test_otio_markers_have_timestamp_sec(self, tmp_path: Path) -> None:
        """Each marker in frames.otio must have metadata['clipwright']['timestamp_sec']."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if cmd:
                out = Path(cmd[-1])
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[3.0, 7.0]),
            )

        assert result.ok is True
        assert result.artifacts is not None

        otio_path: str | None = None
        for a in result.artifacts:
            role = a.role if hasattr(a, "role") else a.get("role", "")
            if role == "timeline":
                otio_path = a.path if hasattr(a, "path") else a.get("path", "")
                break

        assert otio_path is not None
        tl = otio.adapters.read_from_file(str(otio_path))

        all_markers: list[otio.schema.Marker] = []
        for track in tl.tracks:
            all_markers.extend(track.markers)

        for marker in all_markers:
            cw = marker.metadata.get("clipwright", {})
            assert "timestamp_sec" in cw, (
                f"Marker missing 'timestamp_sec' in clipwright metadata: {cw}"
            )
            assert isinstance(cw["timestamp_sec"], float)


# ===========================================================================
# (14) Artifact format: role / path / format fields
# ===========================================================================


class TestArtifactFormat:
    """Artifacts must follow the scene convention: {role, path, format}."""

    def test_frames_otio_artifact_format(self, tmp_path: Path) -> None:
        """frames.otio artifact must have role='timeline' and format='otio'."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is True
        assert result.artifacts is not None

        timeline_artifact = next(
            (
                a
                for a in result.artifacts
                if (a.role if hasattr(a, "role") else a.get("role", "")) == "timeline"
            ),
            None,
        )
        assert timeline_artifact is not None, "No timeline artifact found"

        fmt = (
            timeline_artifact.format
            if hasattr(timeline_artifact, "format")
            else timeline_artifact.get("format", "")
        )
        assert fmt == "otio"

        path_val = (
            timeline_artifact.path
            if hasattr(timeline_artifact, "path")
            else timeline_artifact.get("path", "")
        )
        assert str(path_val).endswith(".otio")

    def test_frames_json_artifact_format(self, tmp_path: Path) -> None:
        """frames.json artifact must have role='manifest' and format='json'."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        (out_dir / "frame_00000.jpg").write_bytes(b"FAKE")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts(mode="interval"))

        assert result.ok is True
        assert result.artifacts is not None

        manifest_artifact = next(
            (
                a
                for a in result.artifacts
                if (a.role if hasattr(a, "role") else a.get("role", "")) == "manifest"
            ),
            None,
        )
        assert manifest_artifact is not None, "No manifest artifact found"

        fmt = (
            manifest_artifact.format
            if hasattr(manifest_artifact, "format")
            else manifest_artifact.get("format", "")
        )
        assert fmt == "json"

        path_val = (
            manifest_artifact.path
            if hasattr(manifest_artifact, "path")
            else manifest_artifact.get("path", "")
        )
        assert str(path_val).endswith(".json")


# ===========================================================================
# (15) build_single_frame_command returns list[str|float]; extract str()-ifies args
# ===========================================================================


class TestRunArgsAreAllStrings:
    """extract must convert all list[str|float] command elements to str before run()."""

    def test_run_called_with_all_string_args(self, tmp_path: Path) -> None:
        """run() must receive a list[str] with no float elements (str() applied to ts)."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        captured_cmds: list[list[Any]] = []

        def _capture(cmd: list[Any], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(list(cmd))
            if cmd:
                out = Path(str(cmd[-1]))
                if out.suffix in {".jpg", ".png", ".jpeg"}:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"FAKE_IMAGE")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_capture),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[3.0]),
            )

        assert result.ok is True
        assert len(captured_cmds) >= 1
        for cmd in captured_cmds:
            for arg in cmd:
                assert isinstance(arg, str), (
                    f"run() received non-str argument: {arg!r} (type={type(arg).__name__})"
                )
