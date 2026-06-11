"""test_integration.py — End-to-end tests for clipwright-render with real binaries (DC-GP-002).

These tests use no mocks and verify clipwright_render orchestration functions
end-to-end with real ffmpeg/ffprobe binaries.

Coverage position:
  - Mock-based test_render.py cannot verify acceptance criteria 2/3:
    "ffmpeg executes correctly and generates a single output video" or
    "source media and OTIO files are unchanged".
  - This file runs real binaries to confirm those criteria for the first time.

Execution requirements:
  - CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE environment variables are set, or
    ffmpeg/ffprobe are available on PATH.
  - Tests are skipped when neither is available (no mocks used).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# ===========================================================================
# Helpers
# ===========================================================================


def _sha256(path: Path) -> str:
    """Return the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_duration(ffprobe: str, path: Path) -> float:
    """Use ffprobe to get the duration (seconds) of a video file."""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    data: dict[str, Any] = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _make_test_video(ffmpeg: str, output: Path, duration: float = 5.0) -> None:
    """Generate a short video with audio using lavfi testsrc + sine (CFR, fixed resolution).

    Args:
        ffmpeg: Path to the ffmpeg executable.
        output: Output file path (.mp4).
        duration: Video duration in seconds.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=320x240:rate=25",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"Source generation failed: {result.stderr[:300]}"


def _build_two_segment_timeline(
    source_path: Path,
    otio_path: Path,
    clip1_start: float,
    clip1_duration: float,
    clip2_start: float,
    clip2_duration: float,
    rate: float = 25.0,
) -> otio.schema.Timeline:
    """Build and save a two-segment OTIO Timeline referencing the same source.

    Args:
        source_path: Path to the source media file.
        otio_path: Destination .otio file path.
        clip1_start: source_range start for clip 1 (seconds).
        clip1_duration: Duration for clip 1 (seconds).
        clip2_start: source_range start for clip 2 (seconds).
        clip2_duration: Duration for clip 2 (seconds).
        rate: RationalTime rate (default 25 fps).

    Returns:
        The saved Timeline object.
    """
    ref = otio.schema.ExternalReference(target_url=str(source_path))

    def make_clip(start_sec: float, dur_sec: float) -> otio.schema.Clip:
        return otio.schema.Clip(
            name=f"clip_{start_sec}",
            media_reference=ref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(start_sec * rate, rate),
                duration=otio.opentime.RationalTime(dur_sec * rate, rate),
            ),
        )

    track = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)
    track.append(make_clip(clip1_start, clip1_duration))
    track.append(make_clip(clip2_start, clip2_duration))

    timeline = otio.schema.Timeline(name="integration_test")
    timeline.tracks.append(track)

    otio.adapters.write_to_file(timeline, str(otio_path))
    return timeline


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.integration
def test_render_two_segments_produces_single_output(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """Acceptance criterion 2: real ffmpeg concatenates 2 segments and produces a single output.

    Verifies acceptance criterion 2 (ffmpeg executes correctly and generates the output file),
    which cannot be verified with mocks alone.

    Observations:
      - clipwright_render with dry_run=False returns ok=True.
      - Exactly one output file is generated (recorded in artifacts).
      - ffprobe can read the output file (it is a valid video file).
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=6.0)

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=2.0,
        clip2_start=3.0,
        clip2_duration=2.0,
    )

    output = tmp_path / "output.mp4"
    result = render_timeline(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(),
        dry_run=False,
    )

    assert result["ok"] is True, f"render failed: {result}"
    assert output.exists(), "output file was not generated"
    assert output.stat().st_size > 0, "output file size is 0"

    # Confirm the output can be read by ffprobe (valid video file)
    duration = _probe_duration(require_ffprobe, output)
    assert duration > 0.0, "output video duration is 0 seconds"

    # Output path is recorded in artifacts
    artifacts = result.get("artifacts", [])
    assert len(artifacts) == 1, f"unexpected artifact count: {artifacts}"
    assert Path(artifacts[0]["path"]).resolve() == output.resolve()


@pytest.mark.integration
def test_render_output_duration_matches_segments(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """Acceptance criterion 2: output duration matches the sum of segment durations (within tolerance).

    Verifies acceptance criterion 2 (ffmpeg actually generates a video of the correct duration),
    which cannot be verified with mocks alone.

    Two-segment total = 2.0 + 2.0 = 4.0 s. Measured duration by ffprobe must be within 0.5 s.
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=6.0)

    clip1_dur = 2.0
    clip2_dur = 2.0
    expected_total = clip1_dur + clip2_dur

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=clip1_dur,
        clip2_start=3.0,
        clip2_duration=clip2_dur,
    )

    output = tmp_path / "output.mp4"
    result = render_timeline(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(),
        dry_run=False,
    )

    assert result["ok"] is True, f"render failed: {result}"

    actual_duration = _probe_duration(require_ffprobe, output)
    tolerance = 0.5  # allow for encoder GOP boundary deviation
    assert abs(actual_duration - expected_total) <= tolerance, (
        f"output duration out of expected range: actual={actual_duration:.3f}s,"
        f" expected~{expected_total}s, tolerance=+-{tolerance}s"
    )

    # Also verify the reported total_duration_seconds matches
    reported_duration = result["data"]["total_duration_seconds"]
    assert abs(reported_duration - expected_total) <= 0.001, (
        f"reported duration is incorrect: "
        f"reported={reported_duration}, expected={expected_total}"
    )


@pytest.mark.integration
def test_render_preserves_source_and_otio(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,  # noqa: ARG001
) -> None:
    """Acceptance criterion 3: source media and OTIO file are unchanged after render (non-destructive).

    Verifies acceptance criterion 3 (source media and OTIO are not modified),
    which cannot be verified with mocks alone.

    File sizes and SHA-256 hashes before and after render must match.
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=5.0)

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=1.5,
        clip2_start=2.0,
        clip2_duration=2.0,
    )

    # Record state before render
    source_size_before = source.stat().st_size
    source_hash_before = _sha256(source)
    otio_size_before = otio_path.stat().st_size
    otio_hash_before = _sha256(otio_path)

    output = tmp_path / "output.mp4"
    result = render_timeline(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(),
        dry_run=False,
    )

    assert result["ok"] is True, f"render failed: {result}"

    # Verify state after render
    assert source.stat().st_size == source_size_before, "source media size changed"
    assert _sha256(source) == source_hash_before, "source media hash changed"
    assert otio_path.stat().st_size == otio_size_before, "OTIO file size changed"
    assert _sha256(otio_path) == otio_hash_before, "OTIO file hash changed"

    # Output is a newly generated file separate from the source
    assert output.resolve() != source.resolve(), "output and source share the same path"
    assert output.exists(), "output file was not generated"


@pytest.mark.integration
def test_render_with_width_height_produces_output(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """L-4 verification: real ffmpeg operates correctly with width/height specified.

    Confirms that the implementation integrating scale inside filter_complex (replacing -vf)
    works correctly with real binaries.
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=4.0)

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=1.5,
        clip2_start=2.0,
        clip2_duration=1.5,
    )

    output = tmp_path / "output_scaled.mp4"
    # Scale to width=160, height=120 (half of source 320x240)
    result = render_timeline(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(width=160, height=120),
        dry_run=False,
    )

    assert result["ok"] is True, f"render with width/height failed: {result}"
    assert output.exists(), "output file was not generated"
    assert output.stat().st_size > 0, "output file size is 0"

    # Confirm the output can be read by ffprobe (valid video file)
    duration = _probe_duration(require_ffprobe, output)
    assert duration > 0.0, "output video duration is 0 seconds"
