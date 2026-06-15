"""test_e2e.py — Integration tests for clipwright-scene with real ffmpeg.

These tests require a real ffmpeg binary and are skipped automatically
when ffmpeg is not available (via require_ffmpeg fixture from conftest.py).

Tests generate a synthetic video (black + white concatenation) using ffmpeg
and verify that clipwright_detect_scenes detects the scene boundary and
writes a valid OTIO file with markers.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

# ---------------------------------------------------------------------------
# Attempt to import mcp server
# ---------------------------------------------------------------------------

try:
    from clipwright_scene.server import mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not implemented",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            f"{result.stderr[:500]}"
        )


# ---------------------------------------------------------------------------
# e2e tests
# ---------------------------------------------------------------------------


class TestE2eFfmpegBackend:
    """End-to-end tests using a real ffmpeg binary."""

    def test_black_white_scene_detected(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """Real ffmpeg detects the black->white scene boundary in a synthetic video."""
        video_path = str(tmp_path / "test_video.mp4")
        output_path = str(tmp_path / "out.otio")

        _generate_black_white_video(require_ffmpeg, video_path)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_scenes",
                {
                    "media": video_path,
                    "output": output_path,
                    "options": {
                        "threshold": 0.1,
                        "min_scene_duration": 0.5,
                        "backend": "ffmpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got ok=False: {structured.get('error')}"
        )
        scene_count = structured.get("data", {}).get("scene_count", 0)
        assert scene_count >= 1, (
            f"Expected at least 1 scene boundary, got {scene_count}"
        )

    def test_output_otio_is_readable_with_markers(
        self, tmp_path: Path, require_ffmpeg: str
    ) -> None:
        """After detection, the OTIO output file is loadable and contains markers."""
        from clipwright.otio_utils import load_timeline

        video_path = str(tmp_path / "test_video.mp4")
        output_path = str(tmp_path / "out.otio")

        _generate_black_white_video(require_ffmpeg, video_path)

        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_scenes",
                {
                    "media": video_path,
                    "output": output_path,
                    "options": {
                        "threshold": 0.1,
                        "min_scene_duration": 0.5,
                        "backend": "ffmpeg",
                    },
                },
            )
        )

        assert structured["ok"] is True, (
            f"Expected ok=True but got ok=False: {structured.get('error')}"
        )
        assert Path(output_path).exists(), "Output OTIO file was not created"

        tl = load_timeline(output_path)
        assert isinstance(tl, otio.schema.Timeline)

        v1 = tl.tracks[0]
        assert v1.kind == otio.schema.TrackKind.Video

        # At least one marker should be present
        assert len(v1.markers) >= 1, (
            "Expected at least one scene boundary marker in V1 track"
        )

        # Each marker must have clipwright metadata with kind='scene_boundary'
        for marker in v1.markers:
            cw = marker.metadata.get("clipwright", {})
            assert cw.get("kind") == "scene_boundary", (
                f"Unexpected marker kind: {cw.get('kind')}"
            )
