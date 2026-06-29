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
import os
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

    def test_manifest_paths_exist_on_disk(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """Regression: frames.json path entries must point to real files on disk.

        interval mode uses a per-ss single-frame loop (same as scene/timestamps):
        each grid position is extracted by a separate ffmpeg -ss invocation, so the
        file written to disk is exactly the path recorded in frames.json.

        This guards against the previous fps-filter approach where 1-based ffmpeg
        numbering caused a manifest/disk mismatch for non-integer-multiple durations.
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

        # Locate frames.json among artifacts
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

        frames = manifest.get("frames", [])
        assert len(frames) > 0, "frames.json contains no frame entries"

        # Each path in frames.json must exist on disk.
        # This is the core regression guard: a 1-based ffmpeg output would cause
        # frame_00000.jpg (first entry) to be missing, and frame_00003.jpg to appear
        # on disk without a corresponding manifest entry.
        missing: list[str] = []
        for frame_entry in frames:
            p = frame_entry.get("path", "")
            if not Path(p).exists():
                missing.append(p)

        assert not missing, (
            f"{len(missing)} frame path(s) in frames.json do not exist on disk:\n"
            + "\n".join(missing[:10])
        )

    def test_manifest_paths_match_disk_files_exactly(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """frames.json path set must equal the set of .jpg files on disk exactly.

        Verifies both directions:
          - Every path in frames.json must exist on disk (no phantom entries).
          - Every .jpg file on disk must appear in frames.json (no orphan files).

        interval mode uses per-ss extraction: each grid timestamp is extracted
        independently, so frames.json and disk files share the same source of truth
        (extracted_frames list). Both sets must be identical.
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

        assert structured["ok"] is True

        artifacts = structured.get("artifacts", [])
        manifest_path: str | None = None
        for artifact in artifacts:
            if artifact.get("role") == "manifest":
                manifest_path = artifact.get("path")
                break

        assert manifest_path is not None, "No manifest artifact in response"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        # Paths listed in frames.json
        manifest_paths = {Path(entry["path"]) for entry in manifest.get("frames", [])}

        # Actual .jpg files written by ffmpeg
        disk_paths = set(out_dir.glob("frame_?????.jpg"))

        phantom = manifest_paths - disk_paths
        orphan = disk_paths - manifest_paths

        assert not phantom, (
            "Paths in frames.json that do not exist on disk: "
            + ", ".join(str(p) for p in sorted(phantom))
        )
        assert not orphan, "Files on disk not listed in frames.json: " + ", ".join(
            str(p) for p in sorted(orphan)
        )

    @pytest.mark.integration
    def test_interval_non_integer_multiple_manifest_matches_disk(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """Regression: non-integer-multiple duration must produce manifest == disk.

        Grid: compute_interval_timestamps(9, 4) = [0.0, 4.0, 8.0] => 3 points.
        The old fps-filter path uses period-midpoint sampling internally: for a 9s
        clip with a 4s period, midpoints are 2s and 6s (only 2 fall within [0, 9)),
        so only 2 frames are written to disk while the manifest lists 3 entries;
        frame_00002.jpg is absent on disk. This manifests whenever frac(D / N) < 0.5
        (here 9/4 = 2.25, fractional part 0.25 < 0.5).
        The per-ss loop fix extracts at each grid point directly, making the manifest
        count and disk file count agree at 3.

        Against the unfixed fps-filter implementation, this test would fail:
        manifest count=3, disk files=2, frame_00002.jpg missing.
        """
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / "frames"
        out_dir.mkdir()

        # 9s clip; interval=4s => grid [0, 4, 8] => 3 frames expected
        _generate_solid_color_video(require_ffmpeg, video_path, duration=9.0, rate=10)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "interval",
                        "interval_sec": 4.0,
                        "format": "jpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got: {structured.get('error')}"
        )

        # Locate frames.json among artifacts
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

        # Disk file count must equal manifest count (the core invariant).
        # Against unfixed code: manifest count=3, disk files=2 => FAILS here.
        disk_files = sorted(out_dir.glob("frame_*.jpg"))
        assert manifest["count"] == len(disk_files), (
            f"manifest count={manifest['count']} != disk file count={len(disk_files)}; "
            f"disk files: {[p.name for p in disk_files]}"
        )

        # Exact frame count: compute_interval_timestamps(9, 4) = [0, 4, 8] => 3
        assert manifest["count"] == 3, (
            f"Expected 3 frames for 9s/4s interval, got {manifest['count']}"
        )

        # Every path listed in frames.json must exist on disk.
        # Against unfixed code: frame_00002.jpg is listed but absent => FAILS here.
        for entry in manifest["frames"]:
            assert os.path.exists(entry["path"]), (
                f"Frame path in manifest does not exist on disk: {entry['path']}"
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
        """scene mode: one frame extracted per scene_boundary marker (scene_sample='boundary')."""
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
                        "scene_sample": "boundary",
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
        """frames.otio must contain extracted_frame markers with clipwright metadata.

        Uses scene_sample='boundary' to verify the pre-0.2.0 one-marker-per-boundary
        behavior: 2 boundaries -> exactly 2 extracted_frame markers.
        """
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
                        "scene_sample": "boundary",
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


# ---------------------------------------------------------------------------
# e2e: scene mode × scene_sample (midpoint / start / boundary)
# ---------------------------------------------------------------------------


class TestSceneModeSampleStrategies:
    """End-to-end tests for mode='scene' with scene_sample=midpoint/start/boundary.

    Synthetic setup: 10-second video, 2 scene_boundary markers at 2s and 5s.
    Resulting shot intervals: [0,2), [2,5), [5,10)  =>  N=2 boundaries.

    Expected frame counts by strategy:
      - midpoint:  N+1 = 3  (one representative per shot interval, at midpoints)
      - start:     N+1 = 3  (one representative per shot interval, at each interval start)
      - boundary:  N   = 2  (one frame per marker, v0.1.0-compatible behavior)
    """

    _BOUNDARY_SECONDS: list[float] = [2.0, 5.0]  # N=2 boundaries
    _VIDEO_DURATION: float = 10.0
    _VIDEO_RATE: int = 10

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_scene_extract(
        self,
        tmp_path: Path,
        ffmpeg: str,
        scene_sample: str,
    ) -> tuple[Path, dict]:
        """Generate a synthetic video + scene OTIO, call extract_frames, return (out_dir, response).

        Each scene_sample value gets its own subdirectory so the three
        parametrised tests can share the same tmp_path without collision.
        """
        video_path = str(tmp_path / "video.mp4")
        out_dir = tmp_path / f"frames_{scene_sample}"
        out_dir.mkdir()
        scene_otio_path = str(tmp_path / "scene.otio")

        _generate_solid_color_video(
            ffmpeg,
            video_path,
            duration=self._VIDEO_DURATION,
            rate=self._VIDEO_RATE,
        )
        _make_scene_otio(
            scene_otio_path, self._BOUNDARY_SECONDS, rate=float(self._VIDEO_RATE)
        )

        _content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": video_path,
                    "output_dir": str(out_dir),
                    "options": {
                        "mode": "scene",
                        "scene_timeline": scene_otio_path,
                        "format": "jpeg",
                        "scene_sample": scene_sample,
                    },
                },
            )
        )
        return out_dir, structured

    @staticmethod
    def _find_manifest(structured: dict) -> str | None:
        """Return the manifest artifact path from a call_tool response, or None."""
        for artifact in structured.get("artifacts", []):
            if artifact.get("role") == "manifest":
                return artifact.get("path")
        return None

    # ------------------------------------------------------------------
    # Frame count verification
    # ------------------------------------------------------------------

    def test_midpoint_frame_count_is_n_plus_one(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """scene + midpoint: N boundaries => N+1 frames (one per shot interval)."""
        _out_dir, structured = self._run_scene_extract(
            tmp_path, require_ffmpeg, "midpoint"
        )

        assert structured["ok"] is True, (
            f"Expected ok=True, got: {structured.get('error')}"
        )
        frame_count = structured.get("data", {}).get("frame_count", -1)
        expected = len(self._BOUNDARY_SECONDS) + 1
        assert frame_count == expected, (
            f"midpoint: expected {expected} frames for {len(self._BOUNDARY_SECONDS)} boundaries, "
            f"got {frame_count}"
        )

    def test_start_frame_count_is_n_plus_one(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """scene + start: N boundaries => N+1 frames (one per shot interval)."""
        _out_dir, structured = self._run_scene_extract(
            tmp_path, require_ffmpeg, "start"
        )

        assert structured["ok"] is True, (
            f"Expected ok=True, got: {structured.get('error')}"
        )
        frame_count = structured.get("data", {}).get("frame_count", -1)
        expected = len(self._BOUNDARY_SECONDS) + 1
        assert frame_count == expected, (
            f"start: expected {expected} frames for {len(self._BOUNDARY_SECONDS)} boundaries, "
            f"got {frame_count}"
        )

    def test_boundary_frame_count_is_n(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """scene + boundary: N boundaries => N frames (one per marker position)."""
        _out_dir, structured = self._run_scene_extract(
            tmp_path, require_ffmpeg, "boundary"
        )

        assert structured["ok"] is True, (
            f"Expected ok=True, got: {structured.get('error')}"
        )
        frame_count = structured.get("data", {}).get("frame_count", -1)
        expected = len(self._BOUNDARY_SECONDS)
        assert frame_count == expected, (
            f"boundary: expected {expected} frames for {len(self._BOUNDARY_SECONDS)} boundaries, "
            f"got {frame_count}"
        )

    # ------------------------------------------------------------------
    # File existence + numbering (frame_00000 start, no offset)
    # ------------------------------------------------------------------

    def test_midpoint_disk_files_match_manifest_and_start_at_zero(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """midpoint: frame_00000.jpg is first, disk files match frames.json exactly."""
        out_dir, structured = self._run_scene_extract(
            tmp_path, require_ffmpeg, "midpoint"
        )

        assert structured["ok"] is True

        manifest_path = self._find_manifest(structured)
        assert manifest_path is not None, "No manifest artifact in response"
        assert Path(manifest_path).exists(), f"frames.json missing: {manifest_path}"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        frames = manifest.get("frames", [])
        assert len(frames) > 0, "frames.json contains no entries"

        # 0-based numbering: first file must be frame_00000.jpg
        first_name = Path(frames[0]["path"]).name
        assert first_name == "frame_00000.jpg", (
            f"Expected first entry to be frame_00000.jpg, got {first_name!r}"
        )

        # Every path in frames.json must exist on disk (no phantom entries)
        missing = [e["path"] for e in frames if not Path(e["path"]).exists()]
        assert not missing, f"Manifest paths absent on disk: {missing[:5]}"

        # Disk .jpg set must equal manifest path set (no orphan files)
        manifest_paths = {Path(e["path"]) for e in frames}
        disk_paths = set(out_dir.glob("frame_?????.jpg"))
        phantom = manifest_paths - disk_paths
        orphan = disk_paths - manifest_paths
        assert not phantom, f"Phantom paths (in manifest, not on disk): {phantom}"
        assert not orphan, f"Orphan files (on disk, not in manifest): {orphan}"

    def test_start_disk_files_match_manifest_and_start_at_zero(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """start: frame_00000.jpg is first, disk files match frames.json exactly."""
        out_dir, structured = self._run_scene_extract(tmp_path, require_ffmpeg, "start")

        assert structured["ok"] is True

        manifest_path = self._find_manifest(structured)
        assert manifest_path is not None, "No manifest artifact in response"
        assert Path(manifest_path).exists(), f"frames.json missing: {manifest_path}"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        frames = manifest.get("frames", [])
        assert len(frames) > 0, "frames.json contains no entries"

        first_name = Path(frames[0]["path"]).name
        assert first_name == "frame_00000.jpg", (
            f"Expected first entry to be frame_00000.jpg, got {first_name!r}"
        )

        missing = [e["path"] for e in frames if not Path(e["path"]).exists()]
        assert not missing, f"Manifest paths absent on disk: {missing[:5]}"

        manifest_paths = {Path(e["path"]) for e in frames}
        disk_paths = set(out_dir.glob("frame_?????.jpg"))
        phantom = manifest_paths - disk_paths
        orphan = disk_paths - manifest_paths
        assert not phantom, f"Phantom paths (in manifest, not on disk): {phantom}"
        assert not orphan, f"Orphan files (on disk, not in manifest): {orphan}"

    def test_boundary_disk_files_match_manifest_and_start_at_zero(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """boundary: frame_00000.jpg is first, disk files match frames.json exactly."""
        out_dir, structured = self._run_scene_extract(
            tmp_path, require_ffmpeg, "boundary"
        )

        assert structured["ok"] is True

        manifest_path = self._find_manifest(structured)
        assert manifest_path is not None, "No manifest artifact in response"
        assert Path(manifest_path).exists(), f"frames.json missing: {manifest_path}"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        frames = manifest.get("frames", [])
        assert len(frames) > 0, "frames.json contains no entries"

        first_name = Path(frames[0]["path"]).name
        assert first_name == "frame_00000.jpg", (
            f"Expected first entry to be frame_00000.jpg, got {first_name!r}"
        )

        missing = [e["path"] for e in frames if not Path(e["path"]).exists()]
        assert not missing, f"Manifest paths absent on disk: {missing[:5]}"

        manifest_paths = {Path(e["path"]) for e in frames}
        disk_paths = set(out_dir.glob("frame_?????.jpg"))
        phantom = manifest_paths - disk_paths
        orphan = disk_paths - manifest_paths
        assert not phantom, f"Phantom paths (in manifest, not on disk): {phantom}"
        assert not orphan, f"Orphan files (on disk, not in manifest): {orphan}"

    # ------------------------------------------------------------------
    # manifest (frames.json) count consistency
    # ------------------------------------------------------------------

    def test_manifest_count_field_equals_frames_array_length_for_all_samples(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """frames.json count field == len(frames) array for all three scene_sample values.

        Creates three separate out_dirs within tmp_path to avoid file collisions.
        """
        n = len(self._BOUNDARY_SECONDS)
        expected_counts = {"midpoint": n + 1, "start": n + 1, "boundary": n}

        video_path = str(tmp_path / "video.mp4")
        scene_otio_path = str(tmp_path / "scene.otio")
        _generate_solid_color_video(
            require_ffmpeg,
            video_path,
            duration=self._VIDEO_DURATION,
            rate=self._VIDEO_RATE,
        )
        _make_scene_otio(
            scene_otio_path, self._BOUNDARY_SECONDS, rate=float(self._VIDEO_RATE)
        )

        for scene_sample, expected in expected_counts.items():
            out_dir = tmp_path / f"frames_count_{scene_sample}"
            out_dir.mkdir()

            _content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {
                        "media": video_path,
                        "output_dir": str(out_dir),
                        "options": {
                            "mode": "scene",
                            "scene_timeline": scene_otio_path,
                            "format": "jpeg",
                            "scene_sample": scene_sample,
                        },
                    },
                )
            )
            assert structured["ok"] is True, (
                f"scene_sample={scene_sample!r}: expected ok=True, got: {structured.get('error')}"
            )

            manifest_path = self._find_manifest(structured)
            assert manifest_path is not None, (
                f"scene_sample={scene_sample!r}: no manifest artifact"
            )

            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)

            count_field = manifest.get("count", -1)
            frames_len = len(manifest.get("frames", []))
            assert count_field == frames_len, (
                f"scene_sample={scene_sample!r}: manifest count={count_field} != "
                f"len(frames)={frames_len}"
            )
            assert count_field == expected, (
                f"scene_sample={scene_sample!r}: expected count={expected}, got {count_field}"
            )
