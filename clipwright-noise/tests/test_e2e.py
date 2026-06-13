"""test_e2e.py — clipwright-noise afftdn real e2e tests (design §6.1 / v3 B-3).

Runs the full pipeline of fixture generation → detect_noise → render_timeline
with actual ffmpeg, and measures the noise reduction effect of afftdn via volumedetect.

Pass criteria (§6.1):
  out.mean_volume <= in.mean_volume - 3.0 dB

Negative control (B-3):
  mean_volume of denoise-free render does not drop more than -3.0 dB below input
  → ensures that a drop of -3.0 dB or more is caused by afftdn.

Prerequisites:
  - ffmpeg must be resolvable via CLIPWRIGHT_FFMPEG env var or PATH.
  - Environments without ffmpeg are skipped via @pytest.mark.e2e.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from clipwright.schemas import ToolResult

# -------------------------------------------------------------------
# ffmpeg presence check (e2e skip guard)
# -------------------------------------------------------------------


def _find_ffmpeg() -> str | None:
    """Resolve ffmpeg via CLIPWRIGHT_FFMPEG or PATH and return the path. Returns None if not found."""
    try:
        from clipwright.process import resolve_tool

        return resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    except Exception:
        return None


_FFMPEG = _find_ffmpeg()
_FFMPEG_MISSING = _FFMPEG is None

pytestmark = pytest.mark.e2e

# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------


def _make_fixture(tmp_path: Path) -> Path:
    """Generate and return a 2-second video+pure-white-noise fixture using ffmpeg (v3 B-3).

    Muxes testsrc(320x240,15fps) + anoisesrc(white,amplitude=0.3) and trims to 2s
    with -t 2 / -shortest.

    Note:
        This helper is exclusively for generating test fixtures and measuring audio level
        in e2e tests; it is not production code. clipwright.process.run is a wrapper for
        applying subprocess discipline inside MCP tools, and using it as a test tool would
        require awkward popen argument handling. Direct subprocess calls are an accepted
        exception here. timeout / capture_output / returncode checks are all performed.
        # noqa: subprocess-in-test
    """
    assert _FFMPEG is not None
    fixture = tmp_path / "fixture.mp4"
    cmd = [
        _FFMPEG,
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=15:duration=2",
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=color=white:amplitude=0.3:duration=2",
        "-t",
        "2",
        "-shortest",
        "-y",
        str(fixture),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Fixture generation failed: {result.stderr[-400:]}"
    assert fixture.exists(), "fixture.mp4 was not generated"
    return fixture


def _get_mean_volume(path: Path) -> float:
    """Return the audio mean_volume (dB) via ffmpeg volumedetect.

    Note:
        Dedicated audio level measurement helper for e2e tests.
        Direct subprocess call is an accepted exception
        (timeout / capture_output / returncode checks are performed).
        # noqa: subprocess-in-test
    """
    assert _FFMPEG is not None
    result = subprocess.run(
        [_FFMPEG, "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", result.stderr)
    assert m is not None, (
        f"Could not obtain mean_volume via volumedetect.\nstderr: {result.stderr[-400:]}"
    )
    return float(m.group(1))


def _assert_ok(result: ToolResult | dict[str, Any], label: str) -> None:
    """Call pytest.fail if ok is not True."""
    ok = result.ok if isinstance(result, ToolResult) else result.get("ok")
    if not ok:
        pytest.fail(f"{label} failed: {result}")


# -------------------------------------------------------------------
# Test: DC-GP-002 pre-confirmation that render extension is applied
# -------------------------------------------------------------------


@pytest.mark.skipif(
    _FFMPEG_MISSING,
    reason="ffmpeg not found (CLIPWRIGHT_FFMPEG or PATH required)",
)
def test_render_processes_afftdn_directive(tmp_path: Path) -> None:
    """Confirm that render processes an afftdn-annotated timeline without UNSUPPORTED
    and that afftdn is present in filter_complex (DC-GP-002)."""
    from clipwright_render.render import render_timeline
    from clipwright_render.schemas import RenderOptions

    from clipwright_noise.noise import detect_noise
    from clipwright_noise.schemas import DetectNoiseOptions

    fixture = _make_fixture(tmp_path)
    timeline_path = tmp_path / "timeline.otio"

    # Write afftdn directive to timeline via detect_noise
    opts = DetectNoiseOptions(backend="afftdn", strength="medium")
    result = detect_noise(str(fixture), str(timeline_path), opts, None)
    _assert_ok(result, "detect_noise")

    # Obtain render plan with dry_run=True and confirm afftdn is in filter_complex
    out_mp4 = tmp_path / "out_dryrun.mp4"
    render_opts = RenderOptions(
        video_codec="libx264", audio_codec="aac", overwrite=True
    )
    rr = render_timeline(str(timeline_path), str(out_mp4), render_opts, dry_run=True)
    _assert_ok(rr, "render_timeline (dry_run)")

    rr_data = rr.data if isinstance(rr, ToolResult) else rr.get("data", {})
    filter_complex: str = rr_data.get("filter_complex", "")
    assert "afftdn" in filter_complex, (
        f"filter_complex does not contain afftdn. filter_complex: {filter_complex!r}"
    )


# -------------------------------------------------------------------
# Test: B-3 Negative control (denoise-free render does not drop more than -3.0 dB)
# -------------------------------------------------------------------


@pytest.mark.skipif(
    _FFMPEG_MISSING,
    reason="ffmpeg not found (CLIPWRIGHT_FFMPEG or PATH required)",
)
def test_negative_control_no_denoise_within_threshold(tmp_path: Path) -> None:
    """Confirm that denoise-free render mean_volume does not drop more than -3.0 dB below input (v3 B-3).

    This ensures that a drop of -3.0 dB or more cannot be caused by codec re-encoding alone,
    i.e., it must be attributable to afftdn.
    """
    from clipwright.media import inspect_media
    from clipwright.otio_utils import new_timeline, save_timeline
    from clipwright_render.render import render_timeline
    from clipwright_render.schemas import RenderOptions

    from clipwright_noise.noise import _add_full_clip

    fixture = _make_fixture(tmp_path)

    # Generate a timeline without a denoise directive (in same tmp_path directory)
    neg_timeline_path = tmp_path / "neg_timeline.otio"
    media_info = inspect_media(str(fixture))
    dur_sec = (
        media_info.duration.value / media_info.duration.rate
        if media_info.duration
        else 2.0
    )
    neg_tl = new_timeline(fixture.name)
    _add_full_clip(neg_tl, fixture, dur_sec, media_info.duration)
    save_timeline(neg_tl, str(neg_timeline_path))

    in_vol = _get_mean_volume(fixture)

    # Render without denoise
    neg_out = tmp_path / "neg_out.mp4"
    rr = render_timeline(
        str(neg_timeline_path),
        str(neg_out),
        RenderOptions(video_codec="libx264", audio_codec="aac", overwrite=True),
    )
    _assert_ok(rr, "render_timeline (negative control)")
    assert neg_out.exists(), "Negative control output mp4 was not generated"

    neg_vol = _get_mean_volume(neg_out)

    # Negative control: denoise-free re-encoding alone must not cause more than -3.0 dB drop
    assert neg_vol >= in_vol - 3.0, (
        f"Negative control failed: unexpected large volume drop in denoise-free re-encoding."
        f" in={in_vol:.1f} dB, neg_out={neg_vol:.1f} dB, diff={neg_vol - in_vol:.2f} dB"
    )


# -------------------------------------------------------------------
# Test: §6.1 Main verification (afftdn causes -3.0 dB or more volume drop)
# -------------------------------------------------------------------


@pytest.mark.skipif(
    _FFMPEG_MISSING,
    reason="ffmpeg not found (CLIPWRIGHT_FFMPEG or PATH required)",
)
def test_afftdn_reduces_noise_by_3db(tmp_path: Path) -> None:
    """Confirm that the full pipeline detect_noise(afftdn) → render_timeline
    reduces output mean_volume by -3.0 dB or more compared to input (§6.1).

    media / timeline.otio / out.mp4 are all placed in the same tmp_path directory (DC-AS-002).
    """
    from clipwright_render.render import render_timeline
    from clipwright_render.schemas import RenderOptions

    from clipwright_noise.noise import detect_noise
    from clipwright_noise.schemas import DetectNoiseOptions

    fixture = _make_fixture(tmp_path)

    # --- Measure input volume ---
    in_vol = _get_mean_volume(fixture)

    # --- Write afftdn directive to timeline via detect_noise ---
    timeline_path = tmp_path / "timeline.otio"
    opts = DetectNoiseOptions(backend="afftdn", strength="medium")
    result = detect_noise(str(fixture), str(timeline_path), opts, None)
    _assert_ok(result, "detect_noise")
    assert timeline_path.exists(), "timeline.otio was not generated"

    # --- Apply afftdn via render_timeline and generate output mp4 ---
    out_mp4 = tmp_path / "out.mp4"
    render_opts = RenderOptions(
        video_codec="libx264", audio_codec="aac", overwrite=True
    )
    rr = render_timeline(str(timeline_path), str(out_mp4), render_opts)
    _assert_ok(rr, "render_timeline")
    assert out_mp4.exists(), "out.mp4 was not generated"

    # --- Measure output volume ---
    out_vol = _get_mean_volume(out_mp4)

    # --- Pass condition: out_vol <= in_vol - 3.0 ---
    assert out_vol <= in_vol - 3.0, (
        f"afftdn noise reduction is insufficient."
        f" in={in_vol:.1f} dB, out={out_vol:.1f} dB, diff={out_vol - in_vol:.2f} dB"
        f" (expected: diff <= -3.0 dB)"
    )
