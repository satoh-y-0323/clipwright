"""test_e2e_merge.py — Real e2e tests for multi-source concatenation (task_id: e2e-merge).

Design rationale:
  - §7 v2
  - ADR-C5-r2: pre-process each clip with format-normalisation filters (fps/scale/pad/setsar)
  - ADR-C7-r2: mandatory audio format normalisation (aformat=48000/stereo), anullsrc silence fill
  - ADR-C3: route by unique source count; preserve backward compatibility for single-source path
  - ADR-C9-r2: use input_sources as the single source of truth in plan.py for -i ordering
  - DC-AS-002/AM-005/GP-003: verify concatenation succeeds with mismatched sample_rate/channels

Test layout:
  1. Fixture generation (3 mismatched spec clips via ffmpeg lavfi)
     - Landscape: 640x480, 30 fps, sine 44100 Hz mono
     - Portrait:  360x640, 25 fps, sine 48000 Hz stereo
     - No-audio:  640x480, testsrc, no audio
  2. Multi-source e2e: concatenate 3 clips in timeline.otio and call render_timeline(dry_run=False)
     - assert1: output file is created
     - assert2: output duration ≈ sum of source_range durations (±2 frames tolerance)
     - assert3: output resolution = first-clip spec (640x480, even-rounded)
     - assert4: 1 audio stream in output; concatenation succeeds despite spec mismatch
       (DC-AS-002/AM-005/GP-003)
  3. Negative control: single-source-only timeline outputs as before

How to run (skipped when ffmpeg is absent):
  uv run --package clipwright-render pytest -k e2e_merge

Set ffmpeg on PATH or specify via CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE env vars.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# ===========================================================================
# ffmpeg / ffprobe binary resolution (same pattern as conftest.py require_ffmpeg)
# ===========================================================================


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary in PATH first, then fall back to env_var."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


_FFMPEG = _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")
_FFPROBE = _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")

pytestmark = pytest.mark.e2e

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None,
    reason=(
        "ffmpeg not found. "
        "Add ffmpeg to PATH or "
        "set the CLIPWRIGHT_FFMPEG environment variable to its full path."
    ),
)

requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe not found. "
        "Add ffprobe to PATH or "
        "set the CLIPWRIGHT_FFPROBE environment variable to its full path."
    ),
)

# Subprocess timeout in seconds for all e2e tests.
# Overridable via CI env var E2E_TIMEOUT_SEC.
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# Duration of each fixture (seconds). Kept short to reduce runtime.
_DUR_LANDSCAPE = 3.0  # Landscape (640x480, 30 fps, 44100 Hz mono)
_DUR_PORTRAIT = 3.0  # Portrait  (360x640, 25 fps, 48000 Hz stereo)
_DUR_NOAUDIO = 3.0  # No audio  (640x480, testsrc)

# First-clip spec (landscape is first -> output spec is based on this)
_FIRST_W = 640
_FIRST_H = 480
_FIRST_FPS = 30.0

# Expected resolution after even-rounding ((v // 2) * 2, ADR-C4-r2)
_EXPECT_W = (_FIRST_W // 2) * 2  # 640
_EXPECT_H = (_FIRST_H // 2) * 2  # 480

# Duration tolerance: ±2 frames (first clip 30 fps -> 1 frame = 1/30 ≈ 0.033 s)
_FRAME_TOLERANCE = 2 / _FIRST_FPS  # ≈ 0.067 s


# ===========================================================================
# Helpers: fixture generation
# ===========================================================================


def _make_landscape_video(
    ffmpeg: str, output: Path, duration: float = _DUR_LANDSCAPE
) -> None:
    """Generate a landscape video (640x480, 30 fps, sine 44100 Hz mono) (DC-GP-003).

    44100 Hz mono is intentionally mismatched with portrait (48000 Hz stereo) to expose
    aformat normalisation on the multi-source path.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x480:rate=30:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    # Dedicated to e2e fixture generation: direct subprocess call allowed
    # instead of process.run (approved exception in MEMORY.md)
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"Landscape fixture generation failed: {result.stderr[:400]}"
    )


def _make_portrait_video(
    ffmpeg: str, output: Path, duration: float = _DUR_PORTRAIT
) -> None:
    """Generate a portrait video (360x640, 25 fps, sine 48000 Hz stereo) (DC-GP-003).

    48000 Hz stereo is intentionally mismatched with landscape (44100 Hz mono).
    Portrait resolution (360x640) differs from first clip (640x480), which also exposes
    aspect-preserving letterbox via pad (ADR-C6).
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=360x640:rate=25:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:sample_rate=48000:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"Portrait fixture generation failed: {result.stderr[:400]}"
    )


def _make_noaudio_video(
    ffmpeg: str, output: Path, duration: float = _DUR_NOAUDIO
) -> None:
    """Generate a no-audio video (640x480, testsrc) (ADR-C7-r2: expose anullsrc fill).

    Including a no-audio clip in the timeline exposes that anullsrc silence fill
    maintains a/v sync when concatenating.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x480:rate=30:duration={duration}",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-an",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"No-audio fixture generation failed: {result.stderr[:400]}"
    )


# ===========================================================================
# Helpers: inspect output media with ffprobe
# ===========================================================================


def _probe_media(ffprobe: str, media: Path) -> dict[str, Any]:
    """Return output from ffprobe -show_streams -show_format -print_format json."""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(media),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr[:400]}"
    return json.loads(result.stdout)


def _get_video_stream(probe: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first video stream from probe output."""
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _get_audio_streams(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all audio streams from probe output."""
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]


def _get_duration_seconds(probe: dict[str, Any]) -> float:
    """Return the duration in seconds from probe output (uses format.duration)."""
    duration_str = probe.get("format", {}).get("duration")
    assert duration_str is not None, "duration could not be retrieved"
    return float(duration_str)


# ===========================================================================
# Helpers: OTIO timeline construction (multi-source)
# ===========================================================================


def _make_multi_source_timeline(
    clips: list[tuple[Path, float, float]],
    timeline_name: str = "e2e_merge_test",
) -> otio.schema.Timeline:
    """Build an OTIO timeline from multiple clips (each with a distinct source).

    clips: list of (source_path, duration_sec, rate).
    Each clip has a separate source; all clips are appended to a single video track.
    """
    track = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)

    for source_path, duration_sec, rate in clips:
        ref = otio.schema.ExternalReference(target_url=str(source_path))
        clip = otio.schema.Clip(
            name=source_path.name,
            media_reference=ref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, rate),
                duration=otio.opentime.RationalTime(duration_sec * rate, rate),
            ),
        )
        track.append(clip)

    timeline = otio.schema.Timeline(name=timeline_name)
    timeline.tracks.append(track)
    return timeline


def _make_single_source_timeline(
    source_path: Path,
    duration_sec: float,
    rate: float,
) -> otio.schema.Timeline:
    """Build an OTIO timeline from a single clip (full source) for negative-control tests."""
    ref = otio.schema.ExternalReference(target_url=str(source_path))
    clip = otio.schema.Clip(
        name=source_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )
    track = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    timeline = otio.schema.Timeline(name="e2e_single_test")
    timeline.tracks.append(track)
    return timeline


# ===========================================================================
# Tests
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
class TestMultiSourceMergeE2E:
    """Real e2e tests for multi-source concatenation (ADR-C5-r2/C7-r2/C3)."""

    def test_render_returns_ok(self, tmp_path: Path) -> None:
        """render_timeline(dry_run=False) returns ok=True for a multi-source timeline (assert1).

        Concatenates 3 sources — landscape (44100 mono), portrait (48000 stereo), no-audio —
        and confirms render succeeds (minimal assert).
        """
        assert _FFMPEG is not None
        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"
        assert out_path.exists(), "output file was not created"
        assert out_path.stat().st_size > 0, "output file size is 0"

    def test_output_duration_equals_sum_of_sources(self, tmp_path: Path) -> None:
        """Output duration ≈ sum of source_range durations (assert2, ±2-frame tolerance).

        ADR-C5-r2: fps conversion preserves wall-clock duration (trimmed interval is unchanged).
        ±2-frame rounding error is acceptable (_FRAME_TOLERANCE = 2/30 ≈ 0.067 s).
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        expected_total = _DUR_LANDSCAPE + _DUR_PORTRAIT + _DUR_NOAUDIO

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        actual_duration = _get_duration_seconds(probe)

        diff = abs(actual_duration - expected_total)
        assert diff <= _FRAME_TOLERANCE, (
            f"Output duration deviates from expected (assert2):\n"
            f"  Expected total: {expected_total:.3f} s\n"
            f"  Actual:         {actual_duration:.3f} s\n"
            f"  Diff:           {diff:.4f} s (tolerance: ±{_FRAME_TOLERANCE:.4f} s = ±2 frames)"
        )

    def test_output_resolution_matches_first_clip(self, tmp_path: Path) -> None:
        """Output resolution = first-clip spec (640x480); portrait clip is letterboxed (assert3).

        ADR-C4-r2: when options.width/height are unset, first-clip source resolution is the target.
        ADR-C6: force_original_aspect_ratio=decrease + pad letterboxes portrait clips.
        Confirm output width=640 and height=480 via ffprobe.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        video_stream = _get_video_stream(probe)
        assert video_stream is not None, "no video stream found in output"

        actual_w = int(video_stream["width"])
        actual_h = int(video_stream["height"])

        assert actual_w == _EXPECT_W, (
            f"Output width does not match expected (assert3):\n"
            f"  Expected: {_EXPECT_W}px\n"
            f"  Actual:   {actual_w}px\n"
            f"  Portrait (360x640) should be letterboxed via pad"
        )
        assert actual_h == _EXPECT_H, (
            f"Output height does not match expected (assert3):\n"
            f"  Expected: {_EXPECT_H}px\n"
            f"  Actual:   {actual_h}px"
        )

    def test_audio_stream_present_and_sync(self, tmp_path: Path) -> None:
        """Concatenation succeeds for mismatched specs and outputs exactly one audio stream (assert4).

        Demonstrates DC-AS-002/AM-005/GP-003:
        - One audio stream in output (anullsrc fills the no-audio clip).
        - Audio is unbroken and a/v-synced (audio duration matches video duration).
        - aformat=48000/stereo unifies format so concat succeeds.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        expected_total = _DUR_LANDSCAPE + _DUR_PORTRAIT + _DUR_NOAUDIO

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        audio_streams = _get_audio_streams(probe)

        # Exactly one audio stream (anullsrc fills the no-audio clip so concat still works)
        assert len(audio_streams) == 1, (
            f"Output audio stream count does not match expected (assert4):\n"
            f"  Expected: 1\n"
            f"  Actual:   {len(audio_streams)}\n"
            f"  No-audio clip should be filled by anullsrc and concatenated (ADR-C7-r2)"
        )

        # Audio duration matches video duration (a/v sync check)
        audio_duration_str = audio_streams[0].get("duration")
        if audio_duration_str is not None:
            audio_duration = float(audio_duration_str)
            diff_av = abs(audio_duration - expected_total)
            # a/v sync: ±4-frame tolerance (includes audio encoder padding etc.)
            av_tolerance = 4 / _FIRST_FPS
            assert diff_av <= av_tolerance, (
                f"Audio and video durations diverge (a/v sync assert4):\n"
                f"  Expected total: {expected_total:.3f} s\n"
                f"  Audio duration: {audio_duration:.3f} s\n"
                f"  Diff:           {diff_av:.4f} s (tolerance: ±{av_tolerance:.4f} s = ±4 frames)"
            )


@requires_ffmpeg
@requires_ffprobe
class TestSingleSourceNegativeControl:
    """Negative control: single-source timeline stays on the legacy path and returns unchanged output (ADR-C3).

    Isolates that format-normalisation filters (pad/aformat) on the multi-source path are
    truly merge-specific.  Single-source renders omit fps unification, pad, and aformat,
    so source specs pass through to the output unchanged.
    """

    def test_single_source_render_returns_ok(self, tmp_path: Path) -> None:
        """render_timeline returns ok=True for a single-source timeline (backward-compatibility check)."""
        assert _FFMPEG is not None

        landscape = tmp_path / "landscape.mp4"
        _make_landscape_video(_FFMPEG, landscape)

        timeline = _make_single_source_timeline(landscape, _DUR_LANDSCAPE, _FIRST_FPS)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_single.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, (
            f"Single-source render failed (backward-compatibility assert): {result}"
        )
        assert out_path.exists(), "single-source output file was not created"

    def test_single_source_output_keeps_original_resolution(
        self, tmp_path: Path
    ) -> None:
        """Single-source render outputs the original resolution (640x480) without pad or scale (ADR-C3).

        The single-source path uses _build_filter_complex, which omits scale when
        options.width/height are unset (trim/concat only).  Confirming 640x480 pass-through
        isolates that the 640x480 output on the multi-source path is due to merge-time normalisation.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        _make_landscape_video(_FFMPEG, landscape)

        timeline = _make_single_source_timeline(landscape, _DUR_LANDSCAPE, _FIRST_FPS)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_single.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        video_stream = _get_video_stream(probe)
        assert video_stream is not None, "no video stream found in output"

        actual_w = int(video_stream["width"])
        actual_h = int(video_stream["height"])

        # Single-source path: no scale -> original 640x480 passes through unchanged
        assert actual_w == 640, (
            f"Single-source output width differs from original resolution (ADR-C3 isolation):\n"
            f"  Expected: 640px (no scale, original source resolution)\n"
            f"  Actual:   {actual_w}px"
        )
        assert actual_h == 480, (
            f"Single-source output height differs from original resolution (ADR-C3 isolation):\n"
            f"  Expected: 480px (no scale, original source resolution)\n"
            f"  Actual:   {actual_h}px"
        )

    def test_single_source_no_pad_filter(self, tmp_path: Path) -> None:
        """Single-source dry_run filter_complex does not contain pad (ADR-C3 internal check).

        The multi-source path uses _build_multi_source_filter_complex (includes pad);
        the single-source path uses _build_filter_complex (trim/concat only, no pad).
        Retrieve filter_complex via dry_run and assert pad is absent.
        """
        assert _FFMPEG is not None

        landscape = tmp_path / "landscape.mp4"
        _make_landscape_video(_FFMPEG, landscape)

        timeline = _make_single_source_timeline(landscape, _DUR_LANDSCAPE, _FIRST_FPS)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_single_dry.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run failed: {result}"

        fc = result["data"]["filter_complex"]
        assert "pad=" not in fc, (
            f"Single-source filter_complex contains pad (ADR-C3 isolation failure):\n"
            f"  filter_complex: {fc}"
        )
        assert "aformat=" not in fc, (
            f"Single-source filter_complex contains aformat (ADR-C3 isolation failure):\n"
            f"  filter_complex: {fc}"
        )

    def test_multi_source_has_pad_and_aformat_in_filter(self, tmp_path: Path) -> None:
        """Multi-source dry_run filter_complex contains pad and aformat (ADR-C5-r2/C7-r2 internal check).

        Combined with the negative control, this confirms that normalisation filters are
        inserted only on the multi-source path — verified at the filter_complex level.
        """
        assert _FFMPEG is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_multi_dry.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run failed: {result}"

        fc = result["data"]["filter_complex"]

        assert "pad=" in fc, (
            f"Multi-source filter_complex does not contain pad (ADR-C5-r2 violation):\n"
            f"  filter_complex: {fc}"
        )
        assert "aformat=" in fc, (
            f"Multi-source filter_complex does not contain aformat (ADR-C7-r2 violation):\n"
            f"  filter_complex: {fc}"
        )
        assert "anullsrc" in fc, (
            f"Multi-source filter_complex does not contain anullsrc (ADR-C7-r2 violation):\n"
            f"  filter_complex: {fc}"
        )
