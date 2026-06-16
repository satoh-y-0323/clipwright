"""test_e2e.py — End-to-end tests for clipwright-frames with real ffmpeg.

These tests require a real ffmpeg binary and are skipped automatically
when ffmpeg is not available (via require_ffmpeg fixture from conftest.py).

Tests generate synthetic videos using ffmpeg lavfi and verify frame extraction
through the MCP interface (mcp.call_tool).

Three modes are tested:
  - interval: extract one frame every N seconds
  - scene:    extract frames at scene boundaries (OTIO with kind='scene_boundary')
  - timestamps: extract frames at explicit timestamps, out-of-range -> warnings
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

# ---------------------------------------------------------------------------
# Attempt to import mcp server
# ---------------------------------------------------------------------------

try:
    from clipwright_frames.server import mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not implemented",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers: synthetic video generation
# ---------------------------------------------------------------------------


def _generate_solid_color_video(
    ffmpeg: str,
    output_path: str,
    *,
    duration: float = 6.0,
    color: str = "blue",
    size: str = "320x240",
    rate: int = 10,
) -> None:
    """Generate a single-color lavfi video of a given duration.

    Uses color source so no real media file is required.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:duration={duration}:size={size}:rate={rate}",
        output_path,
    ]
    # Intentional exception to the process.run convention: test fixture video
    # generation runs ffmpeg directly (see conftest module docstring).
    result = subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to generate test video (exit {result.returncode}):\n"
            f"{result.stderr[:200]}"
        )


def _generate_black_white_video(ffmpeg: str, output_path: str) -> None:
    """Generate a 4-second test video: 2s black followed by 2s white.

    Uses lavfi (virtual input device) to avoid requiring a real media file.
    The scene boundary between black and white is intentionally extreme,
    so scdet should detect it reliably regardless of threshold.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:duration=2:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:duration=2:size=320x240:rate=10",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0",
        output_path,
    ]
    # Intentional exception to the process.run convention: test fixture video
    # generation runs ffmpeg directly (see conftest module docstring).
    result = subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to generate test video (exit {result.returncode}):\n"
            f"{result.stderr[:200]}"
        )


# ---------------------------------------------------------------------------
# Helpers: OTIO timeline with scene_boundary markers
# ---------------------------------------------------------------------------


def _make_scene_otio(
    otio_path: str,
    boundary_seconds: list[float],
    *,
    rate: float = 10.0,
) -> None:
    """Write an OTIO file with scene_boundary markers at the given timestamps.

    Does NOT depend on clipwright-scene package. Uses new_timeline via OTIO
    directly to remain self-contained.
    """
    tl = otio.schema.Timeline(name="scene_test")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)

    for ts in boundary_seconds:
        rt = otio.opentime.RationalTime(ts * rate, rate)
        dur = otio.opentime.RationalTime(0.0, rate)
        marker = otio.schema.Marker(
            name="scene_boundary",
            marked_range=otio.opentime.TimeRange(start_time=rt, duration=dur),
        )
        marker.metadata["clipwright"] = {"kind": "scene_boundary"}
        v1.markers.append(marker)

    otio.adapters.write_to_file(tl, otio_path)


# ---------------------------------------------------------------------------
# e2e: interval mode
# ---------------------------------------------------------------------------


class TestIntervalMode:
    """End-to-end tests for interval mode with real ffmpeg."""

    def test_frame_count_matches_expected(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """interval mode: known duration and interval_sec => expected frame count.

        Video: 6 seconds, interval: 2 seconds => frames at 0s, 2s, 4s => 3 frames.
        """
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        _generate_solid_color_video(require_ffmpeg, video_path, duration=6.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "interval",
                        "interval_sec": 2.0,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got: {structured.get('error')}"
        )
        frame_count = structured.get("data", {}).get("frame_count", -1)
        # 6s / 2s = 3 frames (at 0s, 2s, 4s; 6s is excluded by < duration)
        assert frame_count == 3, f"Expected 3 frames, got {frame_count}"

    def test_frames_json_count_and_array_consistent(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """frames.json: count field equals len(frames) array."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        _generate_solid_color_video(require_ffmpeg, video_path, duration=6.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "interval",
                        "interval_sec": 2.0,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True

        # Find frames.json among artifacts
        artifacts = structured.get("artifacts", [])
        manifest_path: str | None = None
        for artifact in artifacts:
            if artifact.get("role") == "manifest":
                manifest_path = artifact.get("path")
                break

        assert manifest_path is not None, "No manifest artifact in response"
        assert Path(manifest_path).exists(), f"frames.json not found: {manifest_path}"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        assert manifest["count"] == len(manifest["frames"]), (
            f"count={manifest['count']} != len(frames)={len(manifest['frames'])}"
        )

    def test_image_files_match_frame_count(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """Actual JPEG files on disk match frames.json count."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        _generate_solid_color_video(require_ffmpeg, video_path, duration=6.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "interval",
                        "interval_sec": 2.0,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True
        frame_count = structured.get("data", {}).get("frame_count", -1)

        # Count actual .jpg files in output directory
        jpg_files = sorted(out_dir.glob("frame_?????.jpg"))
        assert len(jpg_files) == frame_count, (
            f"Expected {frame_count} .jpg files on disk, found {len(jpg_files)}"
        )


# ---------------------------------------------------------------------------
# e2e: scene mode
# ---------------------------------------------------------------------------


class TestSceneMode:
    """End-to-end tests for scene mode.

    Uses a self-generated OTIO file with scene_boundary markers
    (does not depend on clipwright-scene package).
    """

    def test_one_frame_per_boundary(self, tmp_path: Path, require_ffmpeg: str) -> None:
        """scene mode: one frame extracted per scene_boundary marker."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        scene_otio_path = str(tmp_path / "scene.otio")

        # Generate a 6s video; place boundaries at 1s, 3s, 5s
        _generate_solid_color_video(require_ffmpeg, video_path, duration=6.0, rate=10)
        _make_scene_otio(scene_otio_path, [1.0, 3.0, 5.0], rate=10.0)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "scene",
                        "scene_timeline": scene_otio_path,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got: {structured.get('error')}"
        )
        frame_count = structured.get("data", {}).get("frame_count", -1)
        assert frame_count == 3, (
            f"Expected 3 frames (one per boundary), got {frame_count}"
        )

    def test_frames_otio_contains_extracted_frame_markers(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """frames.otio must contain extracted_frame markers with clipwright metadata."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        scene_otio_path = str(tmp_path / "scene.otio")

        _generate_solid_color_video(require_ffmpeg, video_path, duration=6.0, rate=10)
        _make_scene_otio(scene_otio_path, [1.0, 3.0], rate=10.0)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "scene",
                        "scene_timeline": scene_otio_path,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True

        # Locate frames.otio artifact
        artifacts = structured.get("artifacts", [])
        otio_path: str | None = None
        for artifact in artifacts:
            if artifact.get("role") == "timeline":
                otio_path = artifact.get("path")
                break

        assert otio_path is not None, "No timeline artifact in response"
        assert Path(otio_path).exists(), f"frames.otio not found: {otio_path}"

        # Load the OTIO and verify markers
        tl = otio.adapters.read_from_file(otio_path)
        assert isinstance(tl, otio.schema.Timeline)

        all_markers: list[otio.schema.Marker] = []
        for track in tl.tracks:
            all_markers.extend(track.markers)

        assert len(all_markers) == 2, (
            f"Expected 2 extracted_frame markers, got {len(all_markers)}"
        )
        for marker in all_markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("kind") == "extracted_frame", (
                f"Unexpected marker kind: {cw.get('kind')!r}"
            )

    def test_frames_otio_is_valid_and_loadable(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """frames.otio output must be loadable as a valid OTIO Timeline."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        scene_otio_path = str(tmp_path / "scene.otio")

        _generate_solid_color_video(require_ffmpeg, video_path, duration=4.0, rate=10)
        _make_scene_otio(scene_otio_path, [1.0], rate=10.0)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "scene",
                        "scene_timeline": scene_otio_path,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True

        artifacts = structured.get("artifacts", [])
        otio_path: str | None = None
        for artifact in artifacts:
            if artifact.get("role") == "timeline":
                otio_path = artifact.get("path")
                break

        assert otio_path is not None
        tl = otio.adapters.read_from_file(otio_path)
        assert isinstance(tl, otio.schema.Timeline), (
            "frames.otio is not a valid OTIO Timeline"
        )


# ---------------------------------------------------------------------------
# e2e: timestamps mode
# ---------------------------------------------------------------------------


class TestTimestampsMode:
    """End-to-end tests for timestamps mode with real ffmpeg."""

    def test_in_range_timestamps_are_extracted(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """timestamps mode: in-range timestamps produce extracted frames."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        # 6s video; timestamps 1.0 and 4.0 are within range
        _generate_solid_color_video(require_ffmpeg, video_path, duration=6.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "timestamps",
                        "timestamps": [1.0, 4.0],
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got: {structured.get('error')}"
        )
        frame_count = structured.get("data", {}).get("frame_count", -1)
        assert frame_count == 2, f"Expected 2 frames, got {frame_count}"

    def test_out_of_range_timestamps_appear_in_warnings(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """timestamps mode: out-of-range timestamps appear in warnings."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        # 4s video; 1.0 is in-range; 10.0 and -1.0 are out-of-range
        _generate_solid_color_video(require_ffmpeg, video_path, duration=4.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "timestamps",
                        "timestamps": [1.0, 10.0, -1.0],
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got: {structured.get('error')}"
        )

        warnings = structured.get("warnings") or []
        warning_text = " ".join(warnings).lower()
        assert (
            "skip" in warning_text or "out" in warning_text or "range" in warning_text
        ), f"Expected out-of-range warning in warnings, got: {warnings}"

    def test_remaining_in_range_timestamps_extracted_after_out_of_range(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """timestamps mode: in-range timestamps are extracted even when others are skipped."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        # 4s video; only 1.0 and 2.0 are in [0, 4); 10.0 and -1.0 are out
        _generate_solid_color_video(require_ffmpeg, video_path, duration=4.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "timestamps",
                        "timestamps": [1.0, 2.0, 10.0, -1.0],
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True
        frame_count = structured.get("data", {}).get("frame_count", -1)
        assert frame_count == 2, (
            f"Expected 2 in-range frames extracted, got {frame_count}"
        )


# ---------------------------------------------------------------------------
# e2e: frames.otio validity (all modes share same OTIO format)
# ---------------------------------------------------------------------------


class TestFramesOtioValidity:
    """frames.otio must always be a valid loadable OTIO file with correct markers."""

    def test_interval_mode_otio_marker_kind(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """interval mode frames.otio: all markers have kind='extracted_frame'."""
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        _generate_solid_color_video(require_ffmpeg, video_path, duration=4.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "interval",
                        "interval_sec": 2.0,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True

        artifacts = structured.get("artifacts", [])
        otio_path: str | None = None
        for artifact in artifacts:
            if artifact.get("role") == "timeline":
                otio_path = artifact.get("path")
                break

        assert otio_path is not None, "No timeline artifact in response"
        tl = otio.adapters.read_from_file(otio_path)
        assert isinstance(tl, otio.schema.Timeline)

        all_markers: list[otio.schema.Marker] = []
        for track in tl.tracks:
            all_markers.extend(track.markers)

        # Verify markers exist and all have kind='extracted_frame'
        assert len(all_markers) > 0, "Expected at least one marker in frames.otio"
        for marker in all_markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("kind") == "extracted_frame", (
                f"Expected kind='extracted_frame', got {cw.get('kind')!r}"
            )
