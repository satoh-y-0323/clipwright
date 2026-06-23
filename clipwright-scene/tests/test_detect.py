"""test_detect.py — Tests for detect.py orchestration.

Target API:
  clipwright_scene.detect.detect_scenes(
      media: str,
      output: str,
      options: DetectScenesOptions,
      timeline: str | None = None,
  ) -> ToolResult

Mocking policy:
  - Patch clipwright_scene.detect.inspect_media to supply MediaInfo.
  - Patch clipwright_scene.detect.run to control subprocess output.
  - No real ffmpeg/scenedetect binaries are called.

Verification aspects:
  (1)  Normal case (ffmpeg backend): mock scdet stderr -> OTIO markers
  (2)  Normal case (pyscenedetect backend): mock CSV stdout -> OTIO markers
  (3)  OTIO markers metadata: kind="scene_boundary", confidence, scene_index
  (4)  timeline argument: append markers to existing OTIO
  (5)  ToolResult envelope: ok=True, summary includes scene count and backend
  (6)  artifacts: OTIO file path in artifacts list
  (7)  Input validation error: media file missing -> ok=False with error code
  (8)  Input validation error: output extension not .otio -> ok=False
  (9)  subprocess failure: ClipwrightError -> error_result
  (10) min_scene_duration merge: close boundaries reduced
  (11) Zero scenes: ok=True with warning
  (12) OTIO file generation: output path exists after call
  (13) DetectScenesOptions model_config: extra fields and nan/inf rejected
  (14) ffmpeg scdet filter argument locked by regex
  (15) pyscenedetect --threshold argument locked by regex
"""

from __future__ import annotations

import math
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_scene.schemas import DetectScenesOptions

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


def _make_scdet_stderr(boundaries: list[tuple[float, float]]) -> str:
    """Generate fake ffmpeg scdet filter stderr lines.

    Each tuple is (pts_time, score_0_to_100).
    """
    lines: list[str] = []
    for pts, score in boundaries:
        lines.append(
            f"[scdet @ 0xabcdef] Scdet: frame=10 pts=300 pts_time={pts} "
            f"score={score} prev_mafd=12.5 mafd=45.3"
        )
    return "\n".join(lines)


def _make_pyscenedetect_csv(boundaries_sec: list[float]) -> str:
    """Generate fake pyscenedetect list-scenes CSV stdout."""
    header = (
        "Scene Number,Start Frame,Start Timecode,Start Time (seconds),"
        "End Frame,End Timecode,End Time (seconds),"
        "Length (frames),Length (timecode),Length (seconds)"
    )
    rows: list[str] = [header]
    for i, t in enumerate(boundaries_sec, start=1):
        end_t = t + 4.0
        rows.append(
            f"{i},1,00:00:00.000,{t:.3f},120,00:00:04.000,{end_t:.3f},"
            "119,00:00:03.967,3.967"
        )
    return "\n".join(rows)


def _fake_run_ffmpeg_ok(stderr: str) -> Any:
    """Return a side_effect closure for a successful ffmpeg subprocess."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


def _fake_run_pyscenedetect_ok(csv_stdout: str) -> Any:
    """Return a side_effect closure for a successful pyscenedetect subprocess."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout=csv_stdout, stderr="")

    return _impl


def _opts(
    threshold: float = 0.3,
    min_scene_duration: float = 1.0,
    backend: str = "ffmpeg",
) -> DetectScenesOptions:
    return DetectScenesOptions(
        threshold=threshold,
        min_scene_duration=min_scene_duration,
        backend=backend,  # type: ignore[arg-type]
    )


# ===========================================================================
# (1) Normal case — ffmpeg backend
# ===========================================================================


class TestFfmpegBackendNormal:
    """Normal-path tests for ffmpeg scdet backend."""

    def test_single_boundary_produces_one_marker(self, tmp_path: Path) -> None:
        """One scdet line in stderr -> one OTIO marker in the V1 track."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(5.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("scene_count") == 1

    def test_multiple_boundaries_produce_multiple_markers(self, tmp_path: Path) -> None:
        """Multiple scdet lines -> multiple markers."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(2.0, 50.0), (5.0, 80.0), (8.0, 40.0)])
        media_info = _make_media_info(path=media, duration_sec=12.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts(min_scene_duration=0.0))

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("scene_count") == 3

    def test_ffmpeg_command_uses_list_not_shell_string(self, tmp_path: Path) -> None:
        """The command passed to run() must be a list[str] (shell=False equivalent)."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.run", side_effect=_capture),
        ):
            detect_scenes(media, output, _opts())

        assert len(captured_cmds) >= 1
        for cmd in captured_cmds:
            assert isinstance(cmd, list)
            for arg in cmd:
                assert isinstance(arg, str)

    def test_ffmpeg_timeout_scales_with_duration(self, tmp_path: Path) -> None:
        """ffmpeg run is called with timeout = max(60, ceil(duration * 2))."""
        from clipwright_scene.detect import detect_scenes

        duration_sec = 10.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=duration_sec)

        captured_timeouts: list[float] = []

        def _capture(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            captured_timeouts.append(timeout)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.run", side_effect=_capture),
        ):
            detect_scenes(media, output, _opts())

        assert len(captured_timeouts) >= 1
        expected = max(60, math.ceil(duration_sec * 2))
        assert captured_timeouts[0] == pytest.approx(expected, abs=1)


# ===========================================================================
# (2) Normal case — pyscenedetect backend
# ===========================================================================


class TestPyscenedetectBackendNormal:
    """Normal-path tests for pyscenedetect backend."""

    def test_csv_boundaries_produce_markers(self, tmp_path: Path) -> None:
        """pyscenedetect CSV stdout -> OTIO markers."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        csv_stdout = _make_pyscenedetect_csv([0.0, 4.033])
        media_info = _make_media_info(path=media, duration_sec=8.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.resolve_tool", return_value="scenedetect"),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_pyscenedetect_ok(csv_stdout),
            ),
        ):
            result = detect_scenes(media, output, _opts(backend="pyscenedetect"))

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("scene_count", 0) >= 1

    def test_pyscenedetect_confidence_is_1(self, tmp_path: Path) -> None:
        """All markers from pyscenedetect backend have confidence=1.0."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        csv_stdout = _make_pyscenedetect_csv([0.0, 5.0])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.resolve_tool", return_value="scenedetect"),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_pyscenedetect_ok(csv_stdout),
            ),
        ):
            result = detect_scenes(media, output, _opts(backend="pyscenedetect"))

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        for marker in v1.markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("confidence") == pytest.approx(1.0)

    def test_pyscenedetect_timeout_scales_with_duration(self, tmp_path: Path) -> None:
        """pyscenedetect run is called with timeout = max(120, ceil(duration * 5))."""
        from clipwright_scene.detect import detect_scenes

        duration_sec = 10.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=duration_sec)

        captured_timeouts: list[float] = []

        def _capture(
            cmd: list[str], *, timeout: float = 120.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            captured_timeouts.append(timeout)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.resolve_tool", return_value="scenedetect"),
            patch("clipwright_scene.detect.run", side_effect=_capture),
        ):
            detect_scenes(media, output, _opts(backend="pyscenedetect"))

        assert len(captured_timeouts) >= 1
        expected = max(120, math.ceil(duration_sec * 5))
        assert captured_timeouts[0] == pytest.approx(expected, abs=1)


# ===========================================================================
# (3) OTIO marker metadata verification
# ===========================================================================


class TestOtioMarkerMetadata:
    """Verify OTIO marker metadata: kind, confidence, scene_index, backend."""

    def test_marker_metadata_kind_is_scene_boundary(self, tmp_path: Path) -> None:
        """Each marker metadata['clipwright']['kind'] must equal 'scene_boundary'."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 70.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) > 0
        for marker in v1.markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("kind") == "scene_boundary"

    def test_marker_confidence_normalized_from_score(self, tmp_path: Path) -> None:
        """confidence = min(score / 100.0, 1.0) for ffmpeg backend."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 80.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) > 0
        marker = v1.markers[0]
        cw = marker.metadata.get("clipwright", {})
        assert cw.get("confidence") == pytest.approx(0.80, abs=0.01)

    def test_marker_scene_index_is_sequential(self, tmp_path: Path) -> None:
        """scene_index values are sequential starting from 0."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(2.0, 50.0), (5.0, 60.0), (8.0, 70.0)])
        media_info = _make_media_info(path=media, duration_sec=12.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts(min_scene_duration=0.0))

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) == 3
        indices = [
            v1.markers[i].metadata.get("clipwright", {}).get("scene_index")
            for i in range(len(v1.markers))
        ]
        assert sorted(indices) == list(range(3))

    def test_marker_metadata_has_backend_key(self, tmp_path: Path) -> None:
        """marker metadata['clipwright']['backend'] must equal 'ffmpeg' for ffmpeg backend."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 55.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) > 0
        for marker in v1.markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("backend") == "ffmpeg"

    def test_marker_is_zero_duration(self, tmp_path: Path) -> None:
        """Markers are zero-duration (instantaneous point events on the timeline)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(4.0, 65.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=FPS)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) > 0
        for marker in v1.markers:
            assert marker.marked_range.duration.value == pytest.approx(0.0)


# ===========================================================================
# (4) timeline argument — augment mode
# ===========================================================================


class TestTimelineAugmentMode:
    """When timeline argument is provided, markers are appended to the existing OTIO."""

    def test_timeline_argument_appends_markers_to_existing(
        self, tmp_path: Path
    ) -> None:
        """Existing OTIO's tracks are preserved; new markers are appended to V1."""
        from clipwright.otio_utils import load_timeline, new_timeline, save_timeline

        from clipwright_scene.detect import detect_scenes

        # Build a pre-existing OTIO
        existing_otio = str(tmp_path / "existing.otio")
        tl = new_timeline("pre-existing")
        save_timeline(tl, existing_otio)

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 70.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts(), timeline=existing_otio)

        assert result.ok is True
        tl_out = load_timeline(output)
        v1 = tl_out.tracks[0]
        # At least one marker was added
        assert len(v1.markers) >= 1


# ===========================================================================
# (5) ToolResult envelope
# ===========================================================================


class TestToolResultEnvelope:
    """Verify the success ToolResult envelope format."""

    def test_success_result_has_ok_true(self, tmp_path: Path) -> None:
        """On success, result.ok must be True."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True

    def test_summary_contains_scene_count(self, tmp_path: Path) -> None:
        """summary string must reference the scene count."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(2.0, 50.0), (6.0, 75.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts(min_scene_duration=0.0))

        assert result.ok is True
        assert result.summary is not None
        summary_lower = result.summary.lower()
        # summary must mention the count (2) or word "scene"
        assert "2" in summary_lower or "scene" in summary_lower

    def test_summary_contains_backend_name(self, tmp_path: Path) -> None:
        """summary must mention which backend was used."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert result.summary is not None
        assert "ffmpeg" in result.summary.lower()

    def test_data_has_scene_count_key(self, tmp_path: Path) -> None:
        """result.data must have a 'scene_count' key."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert result.data is not None
        assert "scene_count" in result.data


# ===========================================================================
# (6) artifacts verification
# ===========================================================================


class TestArtifacts:
    """Verify artifacts list contains the OTIO file path."""

    def test_artifacts_contain_otio_path(self, tmp_path: Path) -> None:
        """artifacts must contain at least one entry with the output OTIO path."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert result.artifacts is not None
        assert len(result.artifacts) >= 1
        paths = [
            a.path if hasattr(a, "path") else a.get("path", "")
            for a in result.artifacts
        ]
        assert any(output in str(p) or Path(output).name in str(p) for p in paths)

    def test_artifacts_have_otio_format(self, tmp_path: Path) -> None:
        """artifacts entries must specify format='otio'."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert result.artifacts is not None
        formats = [
            a.format if hasattr(a, "format") else a.get("format", "")
            for a in result.artifacts
        ]
        assert any(f == "otio" for f in formats)


# ===========================================================================
# (7)(8) Input validation errors
# ===========================================================================


class TestInputValidationErrors:
    """Verify input validation error paths."""

    def test_nonexistent_media_returns_error(self, tmp_path: Path) -> None:
        """Non-existent media path -> ok=False with FILE_NOT_FOUND."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "nonexistent.mp4")
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_scene.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.FILE_NOT_FOUND

    def test_invalid_output_extension_returns_error(self, tmp_path: Path) -> None:
        """output path with extension other than .otio -> ok=False."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")  # wrong extension
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with patch("clipwright_scene.detect.inspect_media", return_value=media_info):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_no_video_stream_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """Media with no video stream -> UNSUPPORTED_OPERATION."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "audio_only.mp3")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=False, audio_streams=1
        )

        with patch("clipwright_scene.detect.inspect_media", return_value=media_info):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_duration_none_returns_probe_failed(self, tmp_path: Path) -> None:
        """MediaInfo.duration is None -> PROBE_FAILED."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=None)

        with patch("clipwright_scene.detect.inspect_media", return_value=media_info):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PROBE_FAILED

    def test_error_message_does_not_expose_directory_path(self, tmp_path: Path) -> None:
        """Error message must contain only the basename, not the full directory path."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "missing.mp4")
        output = str(tmp_path / "out.otio")
        full_dir = str(tmp_path)

        with patch(
            "clipwright_scene.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        error_msg = result.error.message if result.error else ""
        assert full_dir not in error_msg


# ===========================================================================
# (9) subprocess failure
# ===========================================================================


class TestSubprocessFailure:
    """ClipwrightError from subprocess is converted to error_result."""

    def test_subprocess_failure_returns_error_result(self, tmp_path: Path) -> None:
        """When run() raises ClipwrightError(SUBPROCESS_FAILED), result is ok=False."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="Command failed",
                hint="Check ffmpeg.",
            )

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.run", side_effect=_fail),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code in (
            ErrorCode.SUBPROCESS_FAILED,
            ErrorCode.INTERNAL,
        )

    def test_subprocess_error_message_does_not_expose_raw_stderr(
        self, tmp_path: Path
    ) -> None:
        """Subprocess error message must not leak raw internal paths."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        secret = "INTERNAL_SECRET_PATH /home/user/private"

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed: {secret}",
                hint="Check ffmpeg.",
            )

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.run", side_effect=_fail),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is False
        error_msg = result.error.message if result.error else ""
        assert secret not in error_msg


# ===========================================================================
# (10) min_scene_duration merge
# ===========================================================================


class TestMinSceneDurationMerge:
    """Boundaries closer than min_scene_duration are merged."""

    def test_close_boundaries_merged_reduces_marker_count(self, tmp_path: Path) -> None:
        """Two boundaries within min_scene_duration=2.0s -> merged to one."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Two boundaries only 0.5s apart -> merge into 1 (min_scene_duration=2.0)
        stderr = _make_scdet_stderr([(3.0, 60.0), (3.5, 70.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts(min_scene_duration=2.0))

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) == 1

    def test_well_separated_boundaries_not_merged(self, tmp_path: Path) -> None:
        """Boundaries separated by more than min_scene_duration are not merged."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 5s apart, min_scene_duration=1.0 -> both kept
        stderr = _make_scdet_stderr([(2.0, 60.0), (7.0, 70.0)])
        media_info = _make_media_info(path=media, duration_sec=12.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts(min_scene_duration=1.0))

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) == 2


# ===========================================================================
# (11) Zero scenes detected
# ===========================================================================


class TestZeroScenesDetected:
    """When no scene boundaries are detected, result is ok=True with warning."""

    def test_zero_boundaries_returns_ok_with_warning(self, tmp_path: Path) -> None:
        """No scdet lines -> ok=True, guidance warning with new message substrings."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Empty stderr: no boundaries; default _opts() is threshold=0.3, backend=ffmpeg
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(""),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("scene_count") == 0
        assert result.warnings is not None
        assert len(result.warnings) > 0
        # New guidance substrings (ffmpeg backend, threshold=0.3 -> suggested=0.15)
        assert "No scene boundaries were detected." in result.warnings[0]
        assert "backend='pyscenedetect'" in result.warnings[0]
        assert "0.15" in result.warnings[0]
        # summary also contains guidance
        assert result.summary is not None
        assert "pyscenedetect" in result.summary

    def test_zero_boundaries_produces_no_markers(self, tmp_path: Path) -> None:
        """No detection -> OTIO V1 track has no markers."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(""),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert len(v1.markers) == 0


# ===========================================================================
# (12) OTIO file generation
# ===========================================================================


class TestOtioFileGeneration:
    """Verify that the output OTIO file is actually created."""

    def test_output_otio_file_is_created(self, tmp_path: Path) -> None:
        """After detect_scenes, the output path must exist on disk."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        assert Path(output).exists(), f"Output OTIO file not found at: {output}"

    def test_output_otio_is_loadable(self, tmp_path: Path) -> None:
        """The generated OTIO file must be loadable as a Timeline."""
        from clipwright.otio_utils import load_timeline

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(2.0, 55.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            result = detect_scenes(media, output, _opts())

        assert result.ok is True
        tl = load_timeline(output)
        assert isinstance(tl, otio.schema.Timeline)

    def test_media_file_unchanged_after_detect(self, tmp_path: Path) -> None:
        """Source media file must not be modified (non-destructive operation)."""
        from clipwright_scene.detect import detect_scenes

        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy content")
        original = media_path.read_bytes()
        output = str(tmp_path / "out.otio")
        stderr = _make_scdet_stderr([(3.0, 60.0)])
        media_info = _make_media_info(path=str(media_path), duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(stderr),
            ),
        ):
            detect_scenes(str(media_path), output, _opts())

        assert media_path.read_bytes() == original


# ===========================================================================
# (13) DetectScenesOptions model_config: extra="forbid" and allow_inf_nan=False
# ===========================================================================


class TestDetectScenesOptionsModelConfig:
    """DetectScenesOptions must reject unknown extra fields and inf/nan values."""

    def test_extra_field_raises_validation_error(self) -> None:
        """DetectScenesOptions(extra_field="x") must raise ValidationError."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            DetectScenesOptions(extra_field="x")  # type: ignore[call-arg]

    def test_nan_threshold_raises_validation_error(self) -> None:
        """DetectScenesOptions(threshold=float('nan')) must raise ValidationError."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            DetectScenesOptions(threshold=float("nan"))

    def test_inf_threshold_raises_validation_error(self) -> None:
        """DetectScenesOptions(threshold=float('inf')) must raise ValidationError."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            DetectScenesOptions(threshold=float("inf"))


# ===========================================================================
# (14) ffmpeg scdet filter argument locked by regex
# ===========================================================================


class TestFfmpegScdetArgumentFormat:
    """The -vf value passed to ffmpeg must match the expected scdet filter pattern."""

    def test_vf_argument_matches_scdet_pattern(self, tmp_path: Path) -> None:
        """ffmpeg -vf argument must match r'^scdet=threshold=\\d+(?:\\.\\d+)?$'."""
        import re

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_vf: list[str] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            for i, arg in enumerate(cmd):
                if arg == "-vf" and i + 1 < len(cmd):
                    captured_vf.append(cmd[i + 1])
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.run", side_effect=_capture),
        ):
            detect_scenes(media, output, _opts())

        assert len(captured_vf) == 1, "Expected exactly one -vf argument"
        pattern = re.compile(r"^scdet=threshold=\d+(?:\.\d+)?$")
        assert pattern.fullmatch(captured_vf[0]), (
            f"ffmpeg -vf value {captured_vf[0]!r} does not match scdet filter pattern"
        )


# ===========================================================================
# (15) pyscenedetect --threshold argument locked by regex
# ===========================================================================


class TestPyscenedetectThresholdArgumentFormat:
    """The --threshold value passed to pyscenedetect must be a bare number."""

    def test_threshold_argument_matches_numeric_pattern(self, tmp_path: Path) -> None:
        """pyscenedetect --threshold next-arg must match r'^\\d+(?:\\.\\d+)?$'."""
        import re

        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_threshold: list[str] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            for i, arg in enumerate(cmd):
                if arg == "--threshold" and i + 1 < len(cmd):
                    captured_threshold.append(cmd[i + 1])
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.resolve_tool", return_value="scenedetect"),
            patch("clipwright_scene.detect.run", side_effect=_capture),
        ):
            detect_scenes(media, output, _opts(backend="pyscenedetect"))

        assert len(captured_threshold) == 1, "Expected exactly one --threshold argument"
        pattern = re.compile(r"^\d+(?:\.\d+)?$")
        assert pattern.fullmatch(captured_threshold[0]), (
            f"pyscenedetect --threshold value {captured_threshold[0]!r} "
            "does not match numeric pattern"
        )


# ===========================================================================
# (A) _zero_boundary_guidance — pure helper contract tests
# ===========================================================================


class TestZeroBoundaryGuidancePureHelper:
    """Unit tests for _zero_boundary_guidance() pure helper.

    Verifies the string contract of _zero_boundary_guidance():
    common prefix, four branches, and suggested-threshold formatting.

    Contract:
    - Always: "No scene boundaries were detected."
    - ffmpeg backend: "backend='pyscenedetect'" and "content-aware"
    - pyscenedetect backend: "single continuous shot"
    - threshold > 0.05: "Try lowering 'threshold' to" + suggested value string
    - threshold <= 0.05: "practical floor (0.05)" and NOT "Try lowering 'threshold' to"
    """

    # ------------------------------------------------------------------
    # Parametrize table
    # threshold values: 0.0, 0.05, 0.1, 0.3, 1.0
    # backend values: "ffmpeg", "pyscenedetect"
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "threshold,backend",
        [
            (0.0, "ffmpeg"),
            (0.0, "pyscenedetect"),
            (0.05, "ffmpeg"),
            (0.05, "pyscenedetect"),
            (0.1, "ffmpeg"),
            (0.1, "pyscenedetect"),
            (0.3, "ffmpeg"),
            (0.3, "pyscenedetect"),
            (1.0, "ffmpeg"),
            (1.0, "pyscenedetect"),
        ],
    )
    def test_common_prefix_always_present(self, threshold: float, backend: str) -> None:
        """All combinations must start with the common prefix."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=threshold, backend=backend)
        guidance = _zero_boundary_guidance(opts)
        assert "No scene boundaries were detected." in guidance

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 0.05, 0.1, 0.3, 1.0],
    )
    def test_ffmpeg_backend_contains_switch_fragment(self, threshold: float) -> None:
        """ffmpeg backend must always mention backend='pyscenedetect' and content-aware."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=threshold, backend="ffmpeg")
        guidance = _zero_boundary_guidance(opts)
        assert "backend='pyscenedetect'" in guidance
        assert "content-aware" in guidance

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 0.05, 0.1, 0.3, 1.0],
    )
    def test_pyscenedetect_backend_contains_single_shot_fragment(
        self, threshold: float
    ) -> None:
        """pyscenedetect backend must always mention single continuous shot."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=threshold, backend="pyscenedetect")
        guidance = _zero_boundary_guidance(opts)
        assert "single continuous shot" in guidance

    @pytest.mark.parametrize(
        "threshold,expected_suggested",
        [
            # threshold > 0.05: suggested = round(max(0.05, threshold/2), 2)
            (0.1, "0.05"),
            (0.3, "0.15"),
            (1.0, "0.5"),
        ],
    )
    def test_threshold_above_floor_contains_lower_fragment(
        self, threshold: float, expected_suggested: str
    ) -> None:
        """threshold > 0.05 must produce LOWER fragment with suggested value."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        # Use ffmpeg backend (does not affect threshold branch)
        opts = _opts(threshold=threshold, backend="ffmpeg")
        guidance = _zero_boundary_guidance(opts)
        assert "Try lowering 'threshold' to" in guidance
        assert expected_suggested in guidance

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 0.05],
    )
    def test_threshold_at_or_below_floor_contains_floor_note(
        self, threshold: float
    ) -> None:
        """threshold <= 0.05 must show FLOOR_NOTE and must NOT show LOWER fragment."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=threshold, backend="ffmpeg")
        guidance = _zero_boundary_guidance(opts)
        assert "practical floor (0.05)" in guidance
        # Negative assert: LOWER fragment must not appear
        assert "Try lowering 'threshold' to" not in guidance

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 0.05],
    )
    def test_threshold_at_or_below_floor_pyscenedetect(self, threshold: float) -> None:
        """pyscenedetect + threshold<=0.05: floor note present, LOWER absent."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=threshold, backend="pyscenedetect")
        guidance = _zero_boundary_guidance(opts)
        assert "practical floor (0.05)" in guidance
        assert "Try lowering 'threshold' to" not in guidance

    def test_ffmpeg_threshold_0_3_full_contract(self) -> None:
        """Canonical case: ffmpeg + threshold=0.3 -> suggested=0.15."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=0.3, backend="ffmpeg")
        guidance = _zero_boundary_guidance(opts)
        # All required substrings for this canonical case
        assert "No scene boundaries were detected." in guidance
        assert "Try lowering 'threshold' to" in guidance
        assert "0.15" in guidance
        assert "backend='pyscenedetect'" in guidance
        assert "content-aware" in guidance
        # SINGLE_SHOT must not appear in ffmpeg branch
        assert "single continuous shot" not in guidance
        # FLOOR_NOTE must not appear since threshold > 0.05
        assert "practical floor (0.05)" not in guidance

    def test_pyscenedetect_threshold_0_0_full_contract(self) -> None:
        """Edge case: pyscenedetect + threshold=0.0 -> floor note, single shot."""
        from clipwright_scene.detect import (
            _zero_boundary_guidance,  # type: ignore[attr-defined]
        )

        opts = _opts(threshold=0.0, backend="pyscenedetect")
        guidance = _zero_boundary_guidance(opts)
        assert "No scene boundaries were detected." in guidance
        assert "practical floor (0.05)" in guidance
        assert "single continuous shot" in guidance
        # LOWER must not appear
        assert "Try lowering 'threshold' to" not in guidance
        # SWITCH_FROM_FFMPEG must not appear in pyscenedetect branch
        assert "backend='pyscenedetect'" not in guidance


# ===========================================================================
# (A) Envelope integration: zero scenes -> warning + summary both carry guidance
# ===========================================================================


class TestZeroBoundaryGuidanceEnvelopeIntegration:
    """Integration tests: detect_scenes with zero boundaries carries guidance
    in both warnings[0] and summary (via _zero_boundary_guidance).

    Verifies that when no scene boundaries are detected, the warnings list
    is replaced with the guidance message and the guidance is appended to
    the summary string.
    """

    def test_zero_scenes_ffmpeg_warning_substrings(self, tmp_path: Path) -> None:
        """backend=ffmpeg, threshold=0.3: warnings[0] carries all required substrings."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(""),
            ),
        ):
            result = detect_scenes(
                media, output, _opts(threshold=0.3, backend="ffmpeg")
            )

        assert result.ok is True
        assert result.warnings is not None and len(result.warnings) > 0
        w = result.warnings[0]
        assert "No scene boundaries were detected." in w
        assert "backend='pyscenedetect'" in w
        assert "0.15" in w

    def test_zero_scenes_ffmpeg_summary_contains_guidance(self, tmp_path: Path) -> None:
        """backend=ffmpeg, threshold=0.3: summary also contains guidance substring."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(""),
            ),
        ):
            result = detect_scenes(
                media, output, _opts(threshold=0.3, backend="ffmpeg")
            )

        assert result.ok is True
        assert result.summary is not None
        assert "pyscenedetect" in result.summary

    def test_zero_scenes_pyscenedetect_warning_substrings(self, tmp_path: Path) -> None:
        """backend=pyscenedetect, threshold=0.3: warnings[0] carries single-shot hint."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        csv_stdout = _make_pyscenedetect_csv([])  # no boundaries

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch("clipwright_scene.detect.resolve_tool", return_value="scenedetect"),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_pyscenedetect_ok(csv_stdout),
            ),
        ):
            result = detect_scenes(
                media, output, _opts(threshold=0.3, backend="pyscenedetect")
            )

        assert result.ok is True
        assert result.warnings is not None and len(result.warnings) > 0
        w = result.warnings[0]
        assert "No scene boundaries were detected." in w
        assert "single continuous shot" in w
        assert "0.15" in w

    def test_zero_scenes_floor_threshold_no_lower_fragment(
        self, tmp_path: Path
    ) -> None:
        """threshold=0.05: LOWER fragment absent in warnings[0]."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.run",
                side_effect=_fake_run_ffmpeg_ok(""),
            ),
        ):
            result = detect_scenes(
                media, output, _opts(threshold=0.05, backend="ffmpeg")
            )

        assert result.ok is True
        assert result.warnings is not None and len(result.warnings) > 0
        w = result.warnings[0]
        assert "practical floor (0.05)" in w
        assert "Try lowering 'threshold' to" not in w


# ===========================================================================
# (B) scenedetect DEPENDENCY_MISSING — UX unified error test
# ===========================================================================


class TestScenedetectDependencyMissing:
    """When scenedetect is not installed, detect_scenes must return DEPENDENCY_MISSING
    with the correct pip-install hint (not the ffmpeg winget hint).

    Verifies that a missing scenedetect dependency is reported as
    DEPENDENCY_MISSING with a pip-install hint rather than a generic error.
    """

    def _make_dep_missing_error(self) -> ClipwrightError:
        """Build a DEPENDENCY_MISSING error mirroring the ffmpeg-hint that resolve_tool
        actually raises when a tool is not found on PATH.

        resolve_tool (clipwright.process) always appends the ffmpeg-flavoured
        ``_INSTALL_HINT`` ("On Windows, install via `winget install Gyan.FFmpeg`…")
        regardless of the tool name.  By using this ffmpeg-oriented hint in the mock
        we ensure that the negative assertion in
        ``test_dependency_missing_hint_not_ffmpeg_winget`` truly verifies that
        detect_scenes *replaces* the raw resolve_tool hint with a scenedetect-specific
        one, rather than merely forwarding a hint that never contained the ffmpeg text.
        """
        return ClipwrightError(
            code=ErrorCode.DEPENDENCY_MISSING,
            message="scenedetect not found on PATH",
            hint=(
                "Place scenedetect in a directory on PATH, or set an environment"
                " variable to its full executable path."
                " On Windows, install via `winget install Gyan.FFmpeg` or equivalent."
            ),
        )

    def test_dependency_missing_returns_error_result(self, tmp_path: Path) -> None:
        """resolve_tool raising DEPENDENCY_MISSING -> result ok=False."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        dep_error = self._make_dep_missing_error()

        def _selective_resolve_tool(name: str, **kwargs: Any) -> str:
            # Only raise DEPENDENCY_MISSING for scenedetect; pass through ffmpeg
            if name == "scenedetect":
                raise dep_error
            # For other tools (e.g. ffmpeg) return a plausible path
            return f"/usr/bin/{name}"

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool",
                side_effect=_selective_resolve_tool,
            ),
        ):
            result = detect_scenes(
                media,
                output,
                _opts(backend="pyscenedetect"),
            )

        assert result.ok is False

    def test_dependency_missing_error_code(self, tmp_path: Path) -> None:
        """result.error.code must be 'DEPENDENCY_MISSING'."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        dep_error = self._make_dep_missing_error()

        def _selective_resolve_tool(name: str, **kwargs: Any) -> str:
            if name == "scenedetect":
                raise dep_error
            return f"/usr/bin/{name}"

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool",
                side_effect=_selective_resolve_tool,
            ),
        ):
            result = detect_scenes(
                media,
                output,
                _opts(backend="pyscenedetect"),
            )

        assert result.error is not None
        assert result.error.code == "DEPENDENCY_MISSING"

    def test_dependency_missing_hint_contains_pip_install(self, tmp_path: Path) -> None:
        """result.error.hint must contain 'pip install scenedetect'."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        dep_error = self._make_dep_missing_error()

        def _selective_resolve_tool(name: str, **kwargs: Any) -> str:
            if name == "scenedetect":
                raise dep_error
            return f"/usr/bin/{name}"

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool",
                side_effect=_selective_resolve_tool,
            ),
        ):
            result = detect_scenes(
                media,
                output,
                _opts(backend="pyscenedetect"),
            )

        assert result.error is not None
        assert result.error.hint is not None
        assert "pip install scenedetect" in result.error.hint

    def test_dependency_missing_hint_contains_env_var(self, tmp_path: Path) -> None:
        """result.error.hint must contain 'CLIPWRIGHT_SCENEDETECT'."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        dep_error = self._make_dep_missing_error()

        def _selective_resolve_tool(name: str, **kwargs: Any) -> str:
            if name == "scenedetect":
                raise dep_error
            return f"/usr/bin/{name}"

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool",
                side_effect=_selective_resolve_tool,
            ),
        ):
            result = detect_scenes(
                media,
                output,
                _opts(backend="pyscenedetect"),
            )

        assert result.error is not None
        assert result.error.hint is not None
        assert "CLIPWRIGHT_SCENEDETECT" in result.error.hint

    def test_dependency_missing_hint_not_ffmpeg_winget(self, tmp_path: Path) -> None:
        """result.error.hint must NOT contain ffmpeg winget hint (negative assert)."""
        from clipwright_scene.detect import detect_scenes

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        dep_error = self._make_dep_missing_error()

        def _selective_resolve_tool(name: str, **kwargs: Any) -> str:
            if name == "scenedetect":
                raise dep_error
            return f"/usr/bin/{name}"

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool",
                side_effect=_selective_resolve_tool,
            ),
        ):
            result = detect_scenes(
                media,
                output,
                _opts(backend="pyscenedetect"),
            )

        assert result.error is not None
        # Negative assert: must not carry the ffmpeg-specific winget hint
        assert result.error.hint is None or "winget install Gyan.FFmpeg" not in (
            result.error.hint or ""
        )
