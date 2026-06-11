"""test_e2e_loudness.py — Real e2e tests for loudnorm/peak (task_id: e2e-loudnorm).

Design rationale:
  - architecture-report-20260611-114314 §4, §9
  - ADR-L1: loudnorm two-pass linear apply (detect obtains measured_* -> render applies
    linear=true)
  - ADR-L2: peak uses volumedetect max_volume and applies differential gain via volume filter
  - DC-GP-001: fixtures use pink noise (sine has extreme LRA values making asserts unstable)
  - DC-AM-002: strict e2e for peak + denoise combination is out of scope (peak only, no denoise)
  - B-3 (noise lesson): negative control — assert that output does not converge to target
    without a loudness directive

Test layout:
  1. Fixture generation (pink noise video, approx -35 LUFS / max -21 dB)
  2. loudnorm e2e: detect_loudness -> render_timeline -> remeasure with ebur128/loudnorm
     - assert: output loudness within ±2 LU of target I=-14 LUFS
  3. Negative control: assert that loudness-directive-free render does not reach target
  4. peak e2e: detect_loudness(mode=peak) -> render_timeline -> remeasure with volumedetect
     - assert: output max_volume within ±1.5 dB of target peak_db=-1.0 dB
  5. Minimum assert for render extension: loudness-annotated timeline returns ok=True

How to run (skipped when ffmpeg is absent):
  uv run --package clipwright-render pytest -k e2e_loudness

Set ffmpeg on PATH or specify via CLIPWRIGHT_FFMPEG env var.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.otio_utils import get_clipwright_metadata, set_clipwright_metadata

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

# ===========================================================================
# Helpers: pink noise fixture generation
# ===========================================================================

_DURATION_SEC = 5.0
_RATE = 25.0
_PINK_AMPLITUDE = (
    0.1  # Adjusted to produce approximately -35 LUFS (verified on real hardware)
)
# Subprocess timeout in seconds for all e2e tests (CR-T-001 constant).
# Overridable via CI env var E2E_TIMEOUT_SEC to allow CI tuning.
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))
# Pre-condition for negative control: input LUFS must be lower than this (L-5 pre-condition).
# Ensures fixture amplitude 0.1 falls around -35 LUFS as expected.
_PRE_CONDITION_MAX_LUFS = -25.0


def _make_pink_noise_video(
    ffmpeg: str, output: Path, duration: float = _DURATION_SEC
) -> None:
    """Generate a pink-noise + testsrc video (DC-GP-001).

    Pink noise has stable LRA and is suitable for loudnorm convergence asserts.
    Sine tones produce extreme LRA values (0.00 or inf) that make asserts unstable.
    Amplitude 0.1 produces approximately -35 LUFS / max_volume approximately -21 dB
    (verified on real hardware).
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=15:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"anoisesrc=color=pink:amplitude={_PINK_AMPLITUDE}:duration={duration}",
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
    # Dedicated to e2e fixture/measurement helpers: direct subprocess call allowed
    # instead of process.run (approved exception in MEMORY.md)
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"Fixture generation failed: {result.stderr[:400]}"


def _measure_integrated_loudness(ffmpeg: str, media: Path) -> float:
    """Measure and return integrated loudness (input_i) via loudnorm print_format=json (LUFS)."""
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"loudnorm measurement failed: {result.stderr[:400]}"
    m = re.search(r'"input_i"\s*:\s*"([-0-9.]+)"', result.stderr)
    assert m is not None, f"loudnorm JSON input_i not found:\n{result.stderr[-600:]}"
    return float(m.group(1))


def _measure_max_volume(ffmpeg: str, media: Path) -> float:
    """Measure and return max_volume via volumedetect (dB)."""
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"volumedetect measurement failed: {result.stderr[:400]}"
    )
    m = re.search(r"max_volume:\s*([-0-9.]+)\s*dB", result.stderr)
    assert m is not None, f"max_volume not found:\n{result.stderr[-400:]}"
    return float(m.group(1))


def _measure_loudnorm_all(ffmpeg: str, media: Path) -> dict[str, float]:
    """Return numeric fields from loudnorm print_format=json.

    The loudnorm JSON contains string fields such as 'normalization_type', so only
    fields convertible to float are extracted (-inf/inf are excluded as a
    ValidationError equivalent). Also verifies that all 5 required fields
    (input_i/input_tp/input_lra/input_thresh/target_offset) are present.
    """
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"loudnorm measurement failed: {result.stderr[:400]}"
    # Same pattern as analyze.py H-1 fix: re.search risks matching an early {} block in stderr.
    # Collect all candidates with re.findall and select the last block containing required_keys.
    # ffmpeg outputs loudnorm JSON at the end of stderr, but preceding {} blocks may appear
    # depending on the ffmpeg version.
    required_keys = [
        "input_i",
        "input_tp",
        "input_lra",
        "input_thresh",
        "target_offset",
    ]
    candidates = re.findall(r"\{[^{}]+\}", result.stderr, re.DOTALL)
    raw: dict[str, Any] | None = None
    for block in reversed(candidates):
        try:
            parsed: dict[str, Any] = json.loads(block)
        except json.JSONDecodeError:
            continue
        if all(k in parsed for k in required_keys):
            raw = parsed
            break
    assert raw is not None, f"loudnorm JSON not found:\n{result.stderr[-600:]}"
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:  # noqa: SIM105
            out[k] = float(v)
        except (ValueError, TypeError):
            pass  # ignore string fields such as "dynamic"
    for key in required_keys:
        assert key in out, f"loudnorm JSON missing required field '{key}': {raw}"
    return out


# ===========================================================================
# Helpers: OTIO timeline construction
# ===========================================================================


def _make_single_clip_timeline(
    source_path: Path,
    duration_sec: float = _DURATION_SEC,
    rate: float = _RATE,
) -> otio.schema.Timeline:
    """Generate a single-clip (full source) OTIO timeline."""
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
    timeline = otio.schema.Timeline(name="e2e_test")
    timeline.tracks.append(track)
    return timeline


def _set_loudness_directive(
    timeline: otio.schema.Timeline, directive: dict[str, Any]
) -> None:
    """Write a loudness directive into timeline-level metadata."""
    meta = get_clipwright_metadata(timeline)
    meta["loudness"] = directive
    set_clipwright_metadata(timeline, meta)


# ===========================================================================
# Tests
# ===========================================================================


@requires_ffmpeg
class TestLoudnormE2E:
    """Real e2e tests for loudnorm mode (fixture -> detect -> render -> remeasure)."""

    def test_loudnorm_converges_to_target_i(self, tmp_path: Path) -> None:
        """Integrated loudness after loudnorm converges within ±2 LU of target I=-14 LUFS (ADR-L1).

        Precondition: pink noise fixture is approximately -35 LUFS (about 21 LU from target).
        Verify on real hardware that two-pass loudnorm linear=true brings it to target.
        Tolerance ±2 LU confirmed from real hardware (measured diff ~0.66 LU).
        """
        assert _FFMPEG is not None  # guaranteed by skipif, but needed for type checker
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        # Measure input loudness
        input_i = _measure_integrated_loudness(_FFMPEG, source)

        # loudnorm measurement (detect phase)
        measured_raw = _measure_loudnorm_all(_FFMPEG, source)

        # Build LoudnessDirective and write it to the timeline
        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "loudnorm",
            "scope": "track",
            "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
            "measured": {
                "input_i": measured_raw["input_i"],
                "input_tp": measured_raw["input_tp"],
                "input_lra": measured_raw["input_lra"],
                "input_thresh": measured_raw["input_thresh"],
                "target_offset": measured_raw["target_offset"],
            },
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        # render (same-dir constraint: source / timeline.otio / out.mp4 all under tmp_path)
        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"
        assert out_path.exists(), "Output file was not created"

        # Remeasure output loudness
        output_i = _measure_integrated_loudness(_FFMPEG, out_path)

        target_i = -14.0
        tolerance = 2.0  # ±2 LU (real hardware: diff ~0.66 LU)
        diff = abs(output_i - target_i)

        assert diff <= tolerance, (
            f"loudnorm convergence exceeds tolerance:\n"
            f"  input loudness:  {input_i:.2f} LUFS\n"
            f"  output loudness: {output_i:.2f} LUFS\n"
            f"  target: {target_i} LUFS\n"
            f"  diff: {diff:.2f} LU (allowed: ±{tolerance} LU)"
        )

    def test_render_with_loudnorm_returns_ok(self, tmp_path: Path) -> None:
        """Loudness-annotated timeline render returns ok=True without UNSUPPORTED (minimum assert)."""
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        measured_raw = _measure_loudnorm_all(_FFMPEG, source)
        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "loudnorm",
            "scope": "track",
            "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
            "measured": {
                "input_i": measured_raw["input_i"],
                "input_tp": measured_raw["input_tp"],
                "input_lra": measured_raw["input_lra"],
                "input_thresh": measured_raw["input_thresh"],
                "target_offset": measured_raw["target_offset"],
            },
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )

        assert result["ok"] is True, (
            f"Loudness-annotated timeline render failed: {result.get('error')}"
        )
        assert out_path.exists(), "Output file was not created"
        assert out_path.stat().st_size > 0, "Output file size is 0"


@requires_ffmpeg
class TestLoudnormNegativeControl:
    """Negative control: render without loudness directive does not converge to target (B-3 lesson).

    Required to confirm that loudnorm effect is caused by the loudness directive.
    No loudness -> render leaves input loudness unchanged (far from target).
    """

    def test_no_loudness_directive_does_not_converge_to_target(
        self, tmp_path: Path
    ) -> None:
        """Render without loudness directive leaves output loudness far from target I=-14 (control).

        When input is approximately -35 LUFS, output should remain around the same level
        (more than ±5 LU from target). This confirms that the effect in
        test_loudnorm_converges_to_target_i is caused by the loudnorm directive.
        """
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        # Verify input loudness (should be approximately -35 LUFS)
        input_i = _measure_integrated_loudness(_FFMPEG, source)

        # pre-condition: verify fixture LUFS is sufficiently low (L-5).
        # Early-detect cases where _PINK_AMPLITUDE=0.1 drifts away from ~-35 LUFS.
        # If fixture LUFS changes (e.g. due to an ffmpeg version update), this assert
        # fires immediately to signal that the negative control precondition is broken.
        assert input_i <= _PRE_CONDITION_MAX_LUFS, (
            f"Fixture input LUFS is higher than expected (pre-condition failure):\n"
            f"  input LUFS: {input_i:.2f} LUFS\n"
            f"  expected: <= {_PRE_CONDITION_MAX_LUFS} LUFS\n"
            f"  Adjust _PINK_AMPLITUDE={_PINK_AMPLITUDE}."
        )

        # Build timeline without a loudness directive
        timeline = _make_single_clip_timeline(source)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_no_loudness.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed: {result}"

        # Measure output loudness
        output_i = _measure_integrated_loudness(_FFMPEG, out_path)

        target_i = -14.0
        diff = abs(output_i - target_i)

        # Verify output has NOT converged to target (diff must be >= 5 LU)
        # Input is ~-35 LUFS, so render without loudnorm should remain at similar level (diff ~21 LU)
        min_expected_diff = 5.0
        assert diff >= min_expected_diff, (
            f"Render without loudness directive unexpectedly converged to target (negative control failure):\n"
            f"  input loudness:  {input_i:.2f} LUFS\n"
            f"  output loudness: {output_i:.2f} LUFS\n"
            f"  target: {target_i} LUFS\n"
            f"  diff: {diff:.2f} LU (expected: > {min_expected_diff} LU)"
        )


@requires_ffmpeg
class TestPeakE2E:
    """Real e2e tests for peak mode (no denoise, DC-AM-002)."""

    def test_peak_max_volume_converges_to_target(self, tmp_path: Path) -> None:
        """max_volume after peak apply converges within ±1.5 dB of target peak_db=-1.0 dB (ADR-L2).

        volumedetect is run directly here instead of calling detect_loudness (single e2e).
        Only peak without denoise (DC-AM-002: strict assert for peak + denoise is out of scope).
        """
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        # Measure max_volume (peak detect phase)
        max_volume_db_before = _measure_max_volume(_FFMPEG, source)

        target_peak_db = -1.0
        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "peak",
            "scope": "track",
            "target": {"peak_db": target_peak_db},
            "measured": {"max_volume_db": max_volume_db_before},
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_peak.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"peak render failed: {result}"
        assert out_path.exists(), "Output file was not created"

        # Remeasure output max_volume
        max_volume_db_after = _measure_max_volume(_FFMPEG, out_path)

        tolerance = 1.5  # ±1.5 dB (real hardware: diff ~0.3 dB)
        diff = abs(max_volume_db_after - target_peak_db)

        assert diff <= tolerance, (
            f"peak convergence exceeds tolerance:\n"
            f"  input max_volume:  {max_volume_db_before:.1f} dB\n"
            f"  output max_volume: {max_volume_db_after:.1f} dB\n"
            f"  target peak_db: {target_peak_db} dB\n"
            f"  diff: {diff:.2f} dB (allowed: ±{tolerance} dB)"
        )

    def test_peak_render_returns_ok(self, tmp_path: Path) -> None:
        """Peak-annotated timeline render returns ok=True (minimum assert)."""
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        max_volume_db = _measure_max_volume(_FFMPEG, source)

        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "peak",
            "scope": "track",
            "target": {"peak_db": -1.0},
            "measured": {"max_volume_db": max_volume_db},
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_peak.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )

        assert result["ok"] is True, (
            f"Peak-annotated timeline render failed: {result.get('error')}"
        )
