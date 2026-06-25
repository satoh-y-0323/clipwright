"""test_extract.py — Orchestration tests for extract.py (extract_frames function).

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
       - error message is fixed: "scene_timeline must have a .otio extension."
       - .suffix value (e.g. "'.json'") must NOT appear in the message
  (7)  Success paths (interval/scene/timestamps) — run mocked, image files created
       artificially -> artifacts contains frames.otio (role=timeline, format=otio)
       and frames.json (role=manifest, format=json), data.frame_count is correct
  (8)  interval > duration -> ok=True, warning is fixed:
       "interval_sec exceeds media duration. No frames extracted. Use a smaller
       interval_sec value." — concrete interval/duration values must NOT appear
  (9)  Scene mode: 0 markers found -> ok=True, warnings contain "No scene_boundary
       markers found", empty output
  (10) timestamps mode: out-of-range timestamps -> warning is fixed:
       "Skipped {N} out-of-range timestamp(s). Values must be in [0, duration_sec)."
       — raw timestamp values (e.g. "999.0s") must NOT appear in the warning
  (11) subprocess failure -> safe_subprocess_message used, no absolute path exposed
  (12) frames.json schema: {"count", "mode", "format", "frames":[{"index",
       "timestamp_sec", "path"}]} where path is absolute path inside output_dir
  (13) frames.otio markers: metadata["clipwright"]["kind"]=="extracted_frame" and
       "timestamp_sec" key present
  (14) artifacts format: {"role", "path", "format"} —
       frames.otio -> role="timeline"/format="otio",
       frames.json -> role="manifest"/format="json"
  (15) build_single_frame_command now returns list[str] directly; run() must receive
       a list[str] with no float elements (no str() conversion needed in extract.py)
  (SR M-1) Boundary check: scene_timeline may be outside output_dir (ok=True);
       output artifacts that resolve outside output_dir boundary -> PATH_NOT_ALLOWED
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
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
    scene_sample: str | None = None,
) -> ExtractFramesOptions:
    """Build ExtractFramesOptions for tests.

    scene_sample is passed only when explicitly specified; omitting it lets
    the schema default apply (or keeps pre-implementation tests stable at Red).
    """
    kwargs: dict[str, Any] = {
        "mode": mode,
        "interval_sec": interval_sec,
        "scene_timeline": scene_timeline,
        "timestamps": timestamps if timestamps is not None else [],
        "format": fmt,
        "quality": quality,
        "max_width": max_width,
    }
    if scene_sample is not None:
        kwargs["scene_sample"] = scene_sample
    return ExtractFramesOptions(**kwargs)  # type: ignore[arg-type]


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
        """mode='scene' with scene_timeline having extension other than .otio -> INVALID_INPUT.

        SR L-1: error message must be exactly "scene_timeline must have a .otio extension."
        The .suffix value (e.g. "'.json'") must NOT appear in the message.
        """
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
        # SR L-1: fixed message; no .suffix value in message
        assert result.error.message == "scene_timeline must have a .otio extension."
        assert "'.json'" not in result.error.message
        assert ".json" not in result.error.message


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
    """Normal success case for interval mode.

    CR M-1: frame_count is based on compute_interval_timestamps count, NOT glob.
    _fake_run does NOT create files; the orchestration no longer depends on
    out_dir.glob() — it uses compute_interval_timestamps to determine the count.
    """

    def test_interval_mode_returns_ok_with_two_artifacts(self, tmp_path: Path) -> None:
        """Interval mode success -> ok=True, 2 artifacts (frames.otio + frames.json).

        CR M-1: run is a no-op; no pre-created files needed.
        duration=10.0, interval_sec=5.0 -> compute_interval_timestamps -> [0.0, 5.0] (2 frames).
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

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
        """result.data must have 'frame_count' equal to compute_interval_timestamps count.

        CR M-1: duration=10.0, interval_sec=5.0 -> timestamps=[0.0, 5.0] -> frame_count=2.
        No pre-created glob files needed; count comes from timestamp computation.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

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
        # CR M-1: 10.0 / 5.0 = 2 timestamps (0.0, 5.0)
        assert result.data["frame_count"] == 2


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
        """scene_sample='boundary' extracts exactly N frames for N scene_boundary markers.

        Updated to use scene_sample='boundary' explicitly to preserve the v0.1.0
        boundary behavior. Without this, the default 'midpoint' would produce N+1 frames.
        """
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="boundary"),
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
# (8) interval > duration -> ok=True + fixed warning + empty output
# ===========================================================================


class TestIntervalExceedsDuration:
    """interval_sec exceeding media duration: ok=True with fixed warning, empty output.

    SR M-3: warning must be exactly:
    "interval_sec exceeds media duration. No frames extracted. Use a smaller interval_sec value."
    Concrete numeric values (interval_sec/duration) must NOT appear.
    """

    def test_interval_exceeds_duration_returns_ok_with_fixed_warning(
        self, tmp_path: Path
    ) -> None:
        """interval_sec > duration_sec -> ok=True, warning matches fixed string (SR M-3)."""
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
        expected_warning = (
            "interval_sec exceeds media duration. No frames extracted. "
            "Use a smaller interval_sec value."
        )
        assert any(w == expected_warning for w in result.warnings), (
            f"Expected fixed warning not found. Got: {result.warnings}"
        )
        # SR M-3: concrete numbers must NOT appear
        warning_text = " ".join(result.warnings)
        assert "10.0" not in warning_text, (
            "interval_sec value must not appear in warning"
        )
        assert "5.0" not in warning_text, "duration value must not appear in warning"

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
    """Scene mode with no scene_boundary markers: ok=True + warning + empty output.

    Both tests use scene_sample='boundary' explicitly to preserve the v0.1.0 behavior
    where 0 markers produces a warning and 0 frames. With the new default 'midpoint',
    0 markers would yield 1 frame (single-shot representative) with no warning.
    """

    def test_zero_scene_markers_returns_ok_with_warning(self, tmp_path: Path) -> None:
        """scene_sample='boundary', 0 markers -> ok=True, fixed warning about no markers."""
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="boundary"),
            )

        assert result.ok is True
        assert result.warnings is not None
        assert any(
            "scene_boundary" in w.lower() or "marker" in w.lower()
            for w in result.warnings
        )

    def test_zero_scene_markers_frame_count_is_zero(self, tmp_path: Path) -> None:
        """scene_sample='boundary', 0 markers -> frame_count == 0."""
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="boundary"),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 0


# ===========================================================================
# (10) timestamps mode: out-of-range timestamps produce fixed warning
# ===========================================================================


class TestTimestampsOutOfRange:
    """Out-of-range timestamps produce fixed warning; in-range timestamps are extracted.

    SR M-2: warning must be exactly:
    "Skipped {N} out-of-range timestamp(s). Values must be in [0, duration_sec)."
    Raw timestamp values (e.g. "999.0s", "-1.0s") must NOT appear in the warning.
    """

    def test_out_of_range_timestamps_produce_fixed_warning(
        self, tmp_path: Path
    ) -> None:
        """Timestamps outside [0, duration) produce fixed warning (SR M-2)."""
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
                # 2.0 is valid; 15.0 and -1.0 are out of range -> N=2
                _opts(mode="timestamps", timestamps=[2.0, 15.0, -1.0]),
            )

        assert result.ok is True
        assert result.warnings is not None
        # SR M-2: fixed warning format with N=2
        expected_warning = (
            "Skipped 2 out-of-range timestamp(s). Values must be in [0, duration_sec)."
        )
        assert any(w == expected_warning for w in result.warnings), (
            f"Expected fixed warning not found. Got: {result.warnings}"
        )
        # SR M-2: raw timestamp values must NOT appear
        warning_text = " ".join(result.warnings)
        assert "15.0" not in warning_text, (
            "Raw timestamp value 15.0 must not appear in warning"
        )
        assert "-1.0" not in warning_text, (
            "Raw timestamp value -1.0 must not appear in warning"
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
        """run() raising SUBPROCESS_FAILED -> ok=False / SUBPROCESS_FAILED.

        CR L-4: only ErrorCode.SUBPROCESS_FAILED is allowed; INTERNAL is excluded.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

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
        # CR L-4: SUBPROCESS_FAILED only; INTERNAL must NOT be accepted
        assert result.error.code == ErrorCode.SUBPROCESS_FAILED

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
        """frames.json must contain count, mode, format, frames keys.

        CR M-1: interval mode uses compute_interval_timestamps; no pre-created files.
        duration=10.0, interval_sec=5.0 -> 2 timestamps -> manifest has 2 entries.
        But since run() is a no-op (no actual files written), we only verify schema keys.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

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
            assert str(frame_path).startswith(str(out_dir.resolve())), (
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
        """frames.otio artifact must have role='timeline' and format='otio'.

        CR M-1: no pre-created files; run() is a no-op.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

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
        """frames.json artifact must have role='manifest' and format='json'.

        CR M-1: no pre-created files; run() is a no-op.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

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
# (15) build_single_frame_command returns list[str]; run() receives all-str args
# ===========================================================================


class TestRunArgsAreAllStrings:
    """run() must receive list[str] with no float elements.

    SR L-3: build_single_frame_command now returns list[str] directly.
    The [str(x) for x in raw_cmd] conversion in extract.py is no longer needed.
    We verify that run() is still called with all-str args (the contract is the same,
    but now build_single_frame_command is the source of truth for str types).
    """

    def test_run_called_with_all_string_args(self, tmp_path: Path) -> None:
        """run() must receive a list[str] with no float elements."""
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


# ===========================================================================
# (SR M-1) Boundary checks: output_dir / scene_timeline path validation
# ===========================================================================


class TestBoundaryChecks:
    """SR M-1: boundary validation for output artifacts and scene_timeline.

    - scene_timeline may be located OUTSIDE output_dir; mode='scene' must still succeed.
    - Artifacts written outside out_dir.resolve() boundary -> PATH_NOT_ALLOWED.
      On Windows, real symlink creation requires elevation; we verify the
      boundary-check function directly when symlink is not available,
      and fall back to asserting that a normal output_dir never triggers PATH_NOT_ALLOWED.
    """

    def test_scene_timeline_outside_output_dir_succeeds(self, tmp_path: Path) -> None:
        """scene_timeline in a separate directory (outside output_dir) -> ok=True.

        SR M-1: read-only inputs are not required to be inside output_dir.
        Only the output artifacts must be within the boundary.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        # scene_timeline lives in a sibling directory, NOT inside out_dir
        scene_dir = tmp_path / "scene_sources"
        scene_dir.mkdir()
        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()

        scene_otio = _make_scene_timeline_with_markers(scene_dir, [2.0, 5.0])
        # Confirm scene_timeline is outside out_dir
        assert not str(scene_otio).startswith(str(out_dir))

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
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
        assert result.error is None

    def test_normal_output_dir_does_not_trigger_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """A normal, well-behaved output_dir must never produce PATH_NOT_ALLOWED.

        Regression guard: ensures the boundary check itself does not break
        the happy path when output artifacts resolve within out_dir.
        """
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
        # Must not produce PATH_NOT_ALLOWED for a normal output_dir
        if result.error is not None:
            assert result.error.code != ErrorCode.PATH_NOT_ALLOWED

    def test_boundary_outside_path_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """Artifacts resolving outside out_dir boundary -> PATH_NOT_ALLOWED.

        SR M-1: We mock _check_within_boundary to raise PATH_NOT_ALLOWED when
        the resolved artifact path is outside out_dir.resolve().
        This verifies that extract.py calls the boundary check and propagates
        the error correctly without exposing raw paths.

        On Windows, creating real symlinks that escape boundaries requires
        elevation (UAC), so we use a direct mock of the boundary-check function.
        """
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

        # Patch the boundary-check function to simulate an escape
        def _boundary_escape(path: Path, base: Path) -> None:
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Output path is outside the allowed boundary.",
                hint="Ensure the output_dir does not contain symlinks that escape the directory.",
            )

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
            patch(
                "clipwright_frames.extract._check_within_boundary",
                side_effect=_boundary_escape,
            ),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="timestamps", timestamps=[2.0]),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED


# ===========================================================================
# scene_sample dispatch: midpoint / start / boundary
# (Red: scene_sample not yet in ExtractFramesOptions or extract.py dispatch)
# ===========================================================================


def _extract_ss_from_cmds(captured_cmds: list[list[str]]) -> list[float]:
    """Extract -ss values from captured single-frame ffmpeg commands in call order."""
    ss_values: list[float] = []
    for cmd in captured_cmds:
        for i, arg in enumerate(cmd):
            if arg == "-ss" and i + 1 < len(cmd):
                ss_values.append(float(cmd[i + 1]))
    return ss_values


class TestSceneSampleDispatch:
    """Tests for mode='scene' x scene_sample(midpoint/start/boundary) dispatch.

    Red: ExtractFramesOptions.scene_sample and extract.py dispatch are not yet
    implemented.  After impl-scene-dispatch (task) these must all turn Green.

    Fixture: boundaries=[2.0, 5.0], duration=10.0
    Expected representative timestamps per mode:
      midpoint -> [1.0, 3.5, 7.5]  (N+1 = 3 frames)
      start    -> [0.0, 2.0, 5.0]  (N+1 = 3 frames)
      boundary -> [2.0, 5.0]        (N   = 2 frames)
    """

    _BOUNDARIES = [2.0, 5.0]
    _DURATION = 10.0

    # ------------------------------------------------------------------
    # frame_count by scene_sample
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "scene_sample, expected_count",
        [
            ("midpoint", 3),
            ("start", 3),
            ("boundary", 2),
        ],
    )
    def test_frame_count_by_scene_sample(
        self, tmp_path: Path, scene_sample: str, expected_count: int
    ) -> None:
        """frame_count must match expected value for each scene_sample.

        midpoint/start yield N+1 (one per segment), boundary yields N (one per marker).
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, self._BOUNDARIES)

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
                _opts(
                    mode="scene",
                    scene_timeline=scene_otio,
                    scene_sample=scene_sample,
                ),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == expected_count, (
            f"scene_sample={scene_sample!r}: expected frame_count={expected_count}, "
            f"got {result.data.get('frame_count')}"
        )

    # ------------------------------------------------------------------
    # -ss values (build_single_frame_command timestamp verification)
    # ------------------------------------------------------------------

    def test_midpoint_ss_values(self, tmp_path: Path) -> None:
        """midpoint: -ss values must be [1.0, 3.5, 7.5] for boundaries=[2.0,5.0], duration=10."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, self._BOUNDARIES)

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(list(cmd))
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="midpoint"),
            )

        assert result.ok is True
        ss_values = _extract_ss_from_cmds(captured_cmds)
        assert len(ss_values) == 3
        assert ss_values == pytest.approx([1.0, 3.5, 7.5])

    def test_start_ss_values(self, tmp_path: Path) -> None:
        """start: -ss values must be [0.0, 2.0, 5.0] for boundaries=[2.0,5.0], duration=10."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, self._BOUNDARIES)

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(list(cmd))
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="start"),
            )

        assert result.ok is True
        ss_values = _extract_ss_from_cmds(captured_cmds)
        assert len(ss_values) == 3
        assert ss_values == pytest.approx([0.0, 2.0, 5.0])

    def test_boundary_ss_values(self, tmp_path: Path) -> None:
        """boundary: -ss values must be [2.0, 5.0] for boundaries=[2.0,5.0], duration=10."""
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, self._BOUNDARIES)

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(list(cmd))
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="boundary"),
            )

        assert result.ok is True
        ss_values = _extract_ss_from_cmds(captured_cmds)
        assert len(ss_values) == 2
        assert ss_values == pytest.approx([2.0, 5.0])

    # ------------------------------------------------------------------
    # Zero-boundary edge cases
    # ------------------------------------------------------------------

    def test_zero_boundaries_midpoint_one_frame_no_warning(
        self, tmp_path: Path
    ) -> None:
        """midpoint with 0 markers: single-shot representative (1 frame), no warning.

        The entire media [0, duration) forms one segment; midpoint = duration/2.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [])  # no markers

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(list(cmd))
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="midpoint"),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 1, (
            "midpoint with 0 markers must yield 1 frame (single-shot representative)"
        )
        # No warning for midpoint/start when 0 markers (single-shot is valid)
        assert not result.warnings, (
            f"midpoint with 0 markers must not produce warnings; got: {result.warnings}"
        )
        # -ss value must be midpoint of [0, 10) = 5.0
        ss_values = _extract_ss_from_cmds(captured_cmds)
        assert ss_values == pytest.approx([5.0])

    def test_zero_boundaries_start_one_frame_no_warning(self, tmp_path: Path) -> None:
        """start with 0 markers: single-shot representative (1 frame), no warning.

        The entire media [0, duration) forms one segment; start = 0.0.
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [])  # no markers

        captured_cmds: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(list(cmd))
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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="start"),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 1, (
            "start with 0 markers must yield 1 frame (single-shot representative)"
        )
        assert not result.warnings, (
            f"start with 0 markers must not produce warnings; got: {result.warnings}"
        )
        # -ss value must be start of [0, 10) = 0.0
        ss_values = _extract_ss_from_cmds(captured_cmds)
        assert ss_values == pytest.approx([0.0])

    def test_zero_boundaries_boundary_zero_frames_with_warning(
        self, tmp_path: Path
    ) -> None:
        """boundary with 0 markers: 0 frames extracted, fixed warning message.

        Warning text must be exactly:
        'No scene_boundary markers found in the timeline. No frames extracted.'
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, [])  # no markers

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="boundary"),
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("frame_count") == 0, (
            "boundary with 0 markers must yield 0 frames"
        )
        assert result.warnings is not None, (
            "boundary with 0 markers must produce a warning"
        )
        expected_warning = (
            "No scene_boundary markers found in the timeline. No frames extracted."
        )
        assert any(w == expected_warning for w in result.warnings), (
            f"Expected fixed warning not found. Got: {result.warnings}"
        )

    # ------------------------------------------------------------------
    # Artifact shape unchanged across scene_sample values
    # ------------------------------------------------------------------

    def test_artifacts_shape_unchanged_across_scene_sample(
        self, tmp_path: Path
    ) -> None:
        """Artifacts must always be exactly 2 items with correct role/format.

        The scene_sample value must not alter the artifact envelope shape:
        - frames.otio: role='timeline', format='otio'
        - frames.json: role='manifest', format='json'
        """
        from clipwright_frames.extract import extract_frames

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=self._DURATION)

        out_dir = tmp_path / "frames_out"
        out_dir.mkdir()
        scene_otio = _make_scene_timeline_with_markers(tmp_path, self._BOUNDARIES)

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
                _opts(mode="scene", scene_timeline=scene_otio, scene_sample="midpoint"),
            )

        assert result.ok is True
        assert result.artifacts is not None
        assert len(result.artifacts) == 2, (
            "Exactly 2 artifacts expected: frames.otio + frames.json"
        )

        roles = {
            a.role if hasattr(a, "role") else a.get("role", "")
            for a in result.artifacts
        }
        formats = {
            a.format if hasattr(a, "format") else a.get("format", "")
            for a in result.artifacts
        }
        assert "timeline" in roles, "Missing role='timeline' artifact"
        assert "manifest" in roles, "Missing role='manifest' artifact"
        assert "otio" in formats, "Missing format='otio' artifact"
        assert "json" in formats, "Missing format='json' artifact"
