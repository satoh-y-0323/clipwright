"""test_e2e_sequence.py — Real e2e integration tests for build_sequence -> render round-trip.

Design rationale:
  - §8.3, §V2.1 (DC-AS-001 round-trip co-location): sources produced by build_sequence
    must be accepted by clipwright-render without PATH_NOT_ALLOWED.
  - §V2.5 (DC-AS-006 empty A1 / resolve_bgm harmless): a timeline with no Audio
    tracks produces zero BGM clips; render must succeed.
  - §V2.9 (DC-AM-003 approx duration): rendered output duration ≈ sum of input
    clip source_range durations within ±a few frames.

Test layout:
  1. Generate 2-3 co-located sample sources via ffmpeg lavfi (all in tmp_path)
  2. build_sequence -> .otio (assert ok, artifact .otio exists)
  3. DC-AS-001 round-trip: load .otio, pass each target_url through
     render._check_source_within_timeline_dir — assert no PATH_NOT_ALLOWED
  4. render_timeline on the .otio: assert output duration ≈ sum of source durations
     (DC-AM-003, ±3-frame tolerance at 30 fps)
  5. DC-AS-006: assert resolve_bgm returns None for the V1-only timeline

How to run:
  cd clipwright-sequence
  uv run pytest tests/test_e2e_sequence.py -v
  # or with marker:
  uv run pytest -m integration -v
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
from clipwright_render.plan import resolve_bgm
from clipwright_render.render import _check_source_within_timeline_dir, render_timeline
from clipwright_render.schemas import RenderOptions

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


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

pytestmark = pytest.mark.integration

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None,
    reason=(
        "ffmpeg not found. "
        "Add ffmpeg to PATH or set CLIPWRIGHT_FFMPEG to its full path."
    ),
)

requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe not found. "
        "Add ffprobe to PATH or set CLIPWRIGHT_FFPROBE to its full path."
    ),
)

# Subprocess timeout in seconds (overridable via CI env)
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# Clip duration constants (seconds, kept short for speed)
_DUR_A = 3.0  # clip A: 640x480, 30 fps
_DUR_B = 2.0  # clip B: 640x480, 30 fps (same spec for simple concat)
_DUR_C = 2.0  # clip C: 640x480, 30 fps

_RATE = 30.0
_FRAME_TOLERANCE = 3 / _RATE  # ±3 frames at 30 fps ≈ 0.1 s


# ---------------------------------------------------------------------------
# Fixture generation helpers
# ---------------------------------------------------------------------------


def _make_video(ffmpeg: str, output: Path, duration: float, rate: float = 30.0) -> None:
    """Generate a short test video (640x480, 30 fps, sine audio) via ffmpeg lavfi."""
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x480:rate={int(rate)}:duration={duration}",
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
        f"ffmpeg fixture generation failed for {output.name}: {result.stderr[:400]}"
    )


# ---------------------------------------------------------------------------
# ffprobe helper
# ---------------------------------------------------------------------------


def _probe_media(ffprobe: str, media: Path) -> dict[str, Any]:
    """Return ffprobe JSON for the given media file."""
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
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def _get_duration_seconds(probe: dict[str, Any]) -> float:
    """Return the duration in seconds from ffprobe JSON (format.duration)."""
    duration_str = probe.get("format", {}).get("duration")
    assert duration_str is not None, "ffprobe could not retrieve duration"
    return float(duration_str)


# ---------------------------------------------------------------------------
# Core e2e test class
# ---------------------------------------------------------------------------


@requires_ffmpeg
@requires_ffprobe
class TestBuildSequenceRenderRoundTrip:
    """Round-trip e2e: build_sequence -> .otio -> render_timeline -> mp4.

    All source files are co-located in tmp_path (the same directory as the
    output .otio), satisfying both sequence co-location (DC-AS-001) and
    render boundary checks (_check_source_within_timeline_dir).
    """

    def test_build_sequence_ok_and_otio_exists(self, tmp_path: Path) -> None:
        """build_sequence returns ok=True and the .otio artifact exists on disk.

        Generates 2 co-located source clips, assembles them via build_sequence,
        and verifies:
        - result["ok"] is True
        - the .otio path from artifacts exists as a real file
        """
        assert _FFMPEG is not None

        clip_a = tmp_path / "clip_a.mp4"
        clip_b = tmp_path / "clip_b.mp4"
        _make_video(_FFMPEG, clip_a, _DUR_A)
        _make_video(_FFMPEG, clip_b, _DUR_B)

        otio_path = tmp_path / "sequence.otio"
        clips = [
            SequenceClip(media=str(clip_a)),
            SequenceClip(media=str(clip_b)),
        ]
        result = build_sequence(clips, str(otio_path))

        assert result["ok"] is True, f"build_sequence failed: {result}"
        assert otio_path.exists(), "output .otio file was not created"
        assert otio_path.stat().st_size > 0, "output .otio file is empty"

    def test_dc_as_001_round_trip_colocation(self, tmp_path: Path) -> None:
        """DC-AS-001: every target_url in the produced .otio passes render's co-location check.

        Loads the produced .otio, iterates over every ExternalReference in V1,
        and calls render._check_source_within_timeline_dir for each path.
        None of them should raise PATH_NOT_ALLOWED — all co-located sources
        that were accepted by build_sequence must also be accepted by render.
        """
        assert _FFMPEG is not None

        clip_a = tmp_path / "clip_a.mp4"
        clip_b = tmp_path / "clip_b.mp4"
        clip_c = tmp_path / "clip_c.mp4"
        _make_video(_FFMPEG, clip_a, _DUR_A)
        _make_video(_FFMPEG, clip_b, _DUR_B)
        _make_video(_FFMPEG, clip_c, _DUR_C)

        otio_path = tmp_path / "sequence.otio"
        clips = [
            SequenceClip(media=str(clip_a)),
            SequenceClip(media=str(clip_b)),
            SequenceClip(media=str(clip_c)),
        ]
        result = build_sequence(clips, str(otio_path))
        assert result["ok"] is True, f"build_sequence failed: {result}"

        # Load the produced .otio and verify each source URL
        tl = otio.adapters.read_from_file(str(otio_path))
        sources_checked: list[str] = []
        for track in tl.tracks:
            for item in track:
                if not isinstance(item, otio.schema.Clip):
                    continue
                mr = item.media_reference
                if not isinstance(mr, otio.schema.ExternalReference):
                    continue
                source_url = mr.target_url
                # This must NOT raise PATH_NOT_ALLOWED (DC-AS-001)
                _check_source_within_timeline_dir(otio_path, source_url)
                sources_checked.append(source_url)

        assert len(sources_checked) >= 2, (
            f"Expected at least 2 clips in the .otio but found {len(sources_checked)}"
        )

    def test_render_output_duration_approx(self, tmp_path: Path) -> None:
        """DC-AM-003: rendered output duration ≈ sum of source_range durations (±3 frames).

        Builds a 3-clip sequence, renders to mp4, and confirms the output
        duration is within ±3 frames of the expected total.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        clip_a = tmp_path / "clip_a.mp4"
        clip_b = tmp_path / "clip_b.mp4"
        clip_c = tmp_path / "clip_c.mp4"
        _make_video(_FFMPEG, clip_a, _DUR_A)
        _make_video(_FFMPEG, clip_b, _DUR_B)
        _make_video(_FFMPEG, clip_c, _DUR_C)

        otio_path = tmp_path / "sequence.otio"
        clips = [
            SequenceClip(media=str(clip_a)),
            SequenceClip(media=str(clip_b)),
            SequenceClip(media=str(clip_c)),
        ]
        seq_result = build_sequence(clips, str(otio_path))
        assert seq_result["ok"] is True, f"build_sequence failed: {seq_result}"

        out_path = tmp_path / "rendered.mp4"
        render_result = render_timeline(
            str(otio_path),
            str(out_path),
            RenderOptions(overwrite=False),
            dry_run=False,
        )
        assert render_result["ok"] is True, f"render_timeline failed: {render_result}"
        assert out_path.exists(), "render output file was not created"
        assert out_path.stat().st_size > 0, "render output file is empty"

        # Duration check: output duration ≈ sum of source clip durations (DC-AM-003)
        expected_total = _DUR_A + _DUR_B + _DUR_C
        probe = _probe_media(_FFPROBE, out_path)
        actual_duration = _get_duration_seconds(probe)
        diff = abs(actual_duration - expected_total)
        assert diff <= _FRAME_TOLERANCE, (
            f"Output duration deviates from expected (DC-AM-003):\n"
            f"  Expected total: {expected_total:.3f} s\n"
            f"  Actual:         {actual_duration:.3f} s\n"
            f"  Diff:           {diff:.4f} s (tolerance: ±{_FRAME_TOLERANCE:.4f} s = ±3 frames)"
        )

    def test_dc_as_006_empty_audio_track_resolve_bgm_harmless(
        self, tmp_path: Path
    ) -> None:
        """DC-AS-006: resolve_bgm returns None for a V1-only timeline (no BGM clips).

        build_sequence produces a V1 (video) track with no Audio tracks.
        resolve_bgm must return None — meaning 0 BGM clips — and render must
        succeed without any side-effects from the empty audio path.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        clip_a = tmp_path / "clip_a.mp4"
        clip_b = tmp_path / "clip_b.mp4"
        _make_video(_FFMPEG, clip_a, _DUR_A)
        _make_video(_FFMPEG, clip_b, _DUR_B)

        otio_path = tmp_path / "sequence.otio"
        clips = [
            SequenceClip(media=str(clip_a)),
            SequenceClip(media=str(clip_b)),
        ]
        seq_result = build_sequence(clips, str(otio_path))
        assert seq_result["ok"] is True, f"build_sequence failed: {seq_result}"

        # Load the .otio and verify resolve_bgm returns None (DC-AS-006)
        tl = otio.adapters.read_from_file(str(otio_path))
        bgm_clip = resolve_bgm(tl)
        assert bgm_clip is None, (
            f"resolve_bgm should return None for a V1-only timeline (DC-AS-006),"
            f" but returned: {bgm_clip}"
        )

        # render must also succeed (harmless empty-audio path)
        out_path = tmp_path / "rendered.mp4"
        render_result = render_timeline(
            str(otio_path),
            str(out_path),
            RenderOptions(overwrite=False),
            dry_run=False,
        )
        assert render_result["ok"] is True, (
            f"render_timeline failed for V1-only timeline (DC-AS-006): {render_result}"
        )
        assert out_path.exists(), "render output file was not created"
