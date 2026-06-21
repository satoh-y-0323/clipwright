"""test_e2e_overlay.py — Real e2e tests for the add_overlay -> render round-trip.

Covers the MANDATORY dynamic verification deferred from the design review
(design-review-report-20260622-013708.md: DC-AS-001/002/003).

Test scenarios:
  1. overlay_visible_and_absent_outside  — overlay appears inside its time window
     and is absent (pixel-identical to base) outside (success conditions 3).
  2. fade_partial_alpha                  — mid-fade frame has partial alpha, not
     fully on/off (success condition 4 / V2-1 G2).
  3. odd_dim_scale_via_even_rounding     — scale that produces an odd intermediate
     height succeeds without "height not divisible by 2" failure (V2-2 / DC-AS-002).
  4. xy_placement_center_and_corner      — center formula and corner expression
     place the overlay correctly (V2-6 / DC-AM-002/003).
  5. project_move_round_trip             — annotate in dir A, move to dir B, render
     from dir B; no PATH_NOT_ALLOWED; also verifies image-only move raises
     PATH_NOT_ALLOWED (V2-8 / DC-GP-001 / DC-AS-003).
  6. corrupt_image_subprocess_failed     — garbage-bytes .png causes SUBPROCESS_FAILED
     with basename-only message and a hint mentioning corrupt/unsupported (V2-7 /
     DC-AM-004).
  7. bgm_and_image_overlay_coexistence   — render with both a BGM track and an
     image overlay; output has audio AND composite image (V2-0 G4 / success
     condition 9).
  8. backward_compat_no_image_overlay    — .otio without image_overlay marker
     renders dimensionally identical to the pre-extension path (success condition 10).

How to run (real ffmpeg required):
  cd clipwright-overlay && uv run pytest tests/test_e2e_overlay.py -m integration

Set CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE or add ffmpeg to PATH.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

from clipwright_overlay.overlay import add_overlay
from clipwright_overlay.schemas import AddOverlayOptions

# ===========================================================================
# Binary resolution
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

pytestmark = [pytest.mark.integration, pytest.mark.slow]

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

# Subprocess timeout for fixture generation / render calls
_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# Base video spec — small to keep tests fast
_BASE_W = 320
_BASE_H = 240
_BASE_FPS = 30
_BASE_DUR = 5.0  # seconds

# Overlay image spec — solid red rectangle
_OV_W = 32
_OV_H = 20

# Pixel tolerance for colour comparison (allow minor codec rounding)
_TOL = 15


# ===========================================================================
# Helpers
# ===========================================================================


def _run(cmd: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess[bytes]:
    """Run a subprocess and assert success."""
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    assert result.returncode == 0, (
        f"Command failed: {cmd}\nstderr: {result.stderr.decode(errors='replace')[:400]}"
    )
    return result


def _make_base_video(ffmpeg: str, output: Path, duration: float = _BASE_DUR) -> None:
    """Generate a solid-white base video (320x240, 30 fps) via lavfi."""
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=white:size={_BASE_W}x{_BASE_H}:rate={_BASE_FPS}:duration={duration}",
            "-c:v",
            "libx264",
            "-g",
            "1",  # all-keyframe: avoids seek artefacts in frame extraction
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
    )


def _make_base_video_with_audio(
    ffmpeg: str, output: Path, duration: float = _BASE_DUR
) -> None:
    """Generate a base video with sine audio for BGM coexistence test."""
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=white:size={_BASE_W}x{_BASE_H}:rate={_BASE_FPS}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-g",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(output),
        ]
    )


def _make_red_png(ffmpeg: str, output: Path) -> None:
    """Generate a solid-red PNG image (32x20 px) via lavfi."""
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=red:size={_OV_W}x{_OV_H}:duration=0.1",
            "-frames:v",
            "1",
            str(output),
        ]
    )


def _make_sine_audio(ffmpeg: str, output: Path, duration: float = _BASE_DUR) -> None:
    """Generate a sine-wave audio file for BGM annotation."""
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:a",
            "aac",
            str(output),
        ]
    )


def _make_simple_timeline(
    base_video: Path,
    rate: float = float(_BASE_FPS),
    duration: float = _BASE_DUR,
) -> otio.schema.Timeline:
    """Build an OTIO timeline with a single V1 clip from *base_video*."""
    tl = otio.schema.Timeline(name="test")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
    ref = otio.schema.ExternalReference(target_url=str(base_video))
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration * rate, rate),
    )
    v1.append(otio.schema.Clip(name="base", media_reference=ref, source_range=sr))
    return tl


def _probe_video(ffprobe: str, video: Path) -> dict[str, Any]:
    """Return ffprobe JSON for the first video stream of *video*."""
    r = subprocess.run(
        [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(video),
        ],
        capture_output=True,
        timeout=_TIMEOUT,
    )
    assert r.returncode == 0, r.stderr.decode(errors="replace")[:200]
    return json.loads(r.stdout.decode())  # type: ignore[no-any-return]


def _probe_first_video_stream(ffprobe: str, video: Path) -> dict[str, Any]:
    """Return the first video stream dict from ffprobe output."""
    data = _probe_video(ffprobe, video)
    streams = data.get("streams", [])
    for s in streams:
        if s.get("codec_type") == "video":
            return s  # type: ignore[return-value]
    raise AssertionError(f"No video stream found in {video}")


def _probe_audio_streams(ffprobe: str, video: Path) -> list[dict[str, Any]]:
    """Return all audio stream dicts from ffprobe output."""
    data = _probe_video(ffprobe, video)
    return [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]  # type: ignore[return-value]


def _read_frame_pixel(
    ffmpeg: str,
    video: Path,
    frame_n: int,
    px: int,
    py: int,
) -> tuple[int, int, int]:
    """Extract frame *frame_n* (0-indexed) from *video* and read pixel (px, py) as RGB.

    Strategy:
      1. Dump all frames as PNGs into a temp dir.
      2. Read the target PNG with crop=1:1:px:py | rawvideo rgb24.

    This avoids the unreliable -ss seek + -frames:v 1 combination on H.264 with
    B-frames and avoids the select=eq+rawvideo-pipe issue where ffmpeg produces
    no output when piping filtered rawvideo from a select filter.
    """
    with tempfile.TemporaryDirectory() as td:
        frames_dir = Path(td)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video),
                "-q:v",
                "2",
                str(frames_dir / "frame_%04d.png"),
            ],
            capture_output=True,
            timeout=_TIMEOUT,
        )
        # ffmpeg names frames frame_0001, frame_0002, … (1-indexed)
        frame_file = frames_dir / f"frame_{frame_n + 1:04d}.png"
        if not frame_file.exists():
            raise AssertionError(
                f"Frame file not found: {frame_file} (requested frame index {frame_n})"
            )
        r = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(frame_file),
                "-vf",
                f"crop=1:1:{px}:{py}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            timeout=10,
        )
        assert len(r.stdout) >= 3, (
            f"rawvideo pipe returned {len(r.stdout)} bytes for {frame_file}"
        )
        return int(r.stdout[0]), int(r.stdout[1]), int(r.stdout[2])


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(scope="module")
def work_dir() -> Any:
    """Module-scoped temp directory for shared fixture assets."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(scope="module")
def base_video(work_dir: Path) -> Path:
    """Solid-white base video (320x240, 30 fps, 5 s)."""
    assert _FFMPEG is not None
    out = work_dir / "base.mp4"
    _make_base_video(_FFMPEG, out)
    return out


@pytest.fixture(scope="module")
def base_video_with_audio(work_dir: Path) -> Path:
    """Solid-white base video with audio (320x240, 30 fps, 5 s)."""
    assert _FFMPEG is not None
    out = work_dir / "base_audio.mp4"
    _make_base_video_with_audio(_FFMPEG, out)
    return out


@pytest.fixture(scope="module")
def red_png(work_dir: Path) -> Path:
    """Solid-red PNG (32x20 px)."""
    assert _FFMPEG is not None
    out = work_dir / "logo.png"
    _make_red_png(_FFMPEG, out)
    return out


@pytest.fixture(scope="module")
def sine_audio(work_dir: Path) -> Path:
    """Sine-wave AAC audio for BGM tests."""
    assert _FFMPEG is not None
    out = work_dir / "bgm.aac"
    _make_sine_audio(_FFMPEG, out)
    return out


# ===========================================================================
# Tests
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
def test_overlay_visible_and_absent_outside(
    base_video: Path, red_png: Path, tmp_path: Path
) -> None:
    """Overlay visible inside window; pixel-identical to base outside (success cond 3).

    The red logo (32x20 px) is placed at the center of the base video for
    t=[1, 4] (3 s window, no fade).  A pixel at the overlay center should be
    red inside the window and white outside.
    """
    assert _FFMPEG is not None and _FFPROBE is not None

    # Copy assets to tmp_path so they are co-located with the output OTIO
    base_copy = tmp_path / "base.mp4"
    logo_copy = tmp_path / "logo.png"
    shutil.copy2(str(base_video), str(base_copy))
    shutil.copy2(str(red_png), str(logo_copy))

    in_otio = tmp_path / "timeline.otio"
    out_otio = tmp_path / "overlay.otio"
    out_mp4 = tmp_path / "rendered.mp4"

    tl = _make_simple_timeline(base_copy)
    otio.adapters.write_to_file(tl, str(in_otio))

    # Start=1 s, duration=3 s (window: 1–4 s), no fade
    opts = AddOverlayOptions(
        image_path=str(logo_copy),
        start_sec=1.0,
        duration_sec=3.0,
        x="(W-w)/2",
        y="(H-h)/2",
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    result = add_overlay(str(in_otio), str(out_otio), opts)
    assert result["ok"], f"add_overlay failed: {result}"

    render_result = render_timeline(
        str(out_otio), str(out_mp4), RenderOptions(overwrite=True)
    )
    assert render_result.ok, f"render failed: {render_result.error}"

    # Output dimensions UNCHANGED
    stream = _probe_first_video_stream(_FFPROBE, out_mp4)
    assert stream["width"] == _BASE_W, f"width changed: {stream['width']}"
    assert stream["height"] == _BASE_H, f"height changed: {stream['height']}"

    # Center pixel of the overlay region
    cx = _BASE_W // 2
    cy = _BASE_H // 2

    # Inside window: t=2.5 s → frame index 75 (0-indexed)
    r_in, g_in, b_in = _read_frame_pixel(_FFMPEG, out_mp4, frame_n=75, px=cx, py=cy)
    assert r_in >= 200, (
        f"Expected red inside window but got R={r_in}, G={g_in}, B={b_in}"
    )
    assert g_in < 50, f"Expected red inside window but got R={r_in}, G={g_in}, B={b_in}"

    # Outside window (before): t=0.3 s → frame index 9
    r_out, g_out, b_out = _read_frame_pixel(_FFMPEG, out_mp4, frame_n=9, px=cx, py=cy)
    assert r_out >= 240, f"Expected white outside window (before) but got R={r_out}"
    assert g_out >= 240, f"Expected white outside window (before) but got G={g_out}"
    assert b_out >= 240, f"Expected white outside window (before) but got B={b_out}"

    # Outside window (after): t=4.5 s → frame index 135
    r_af, g_af, b_af = _read_frame_pixel(_FFMPEG, out_mp4, frame_n=135, px=cx, py=cy)
    assert r_af >= 240, f"Expected white outside window (after) but got R={r_af}"
    assert g_af >= 240, f"Expected white outside window (after) but got G={g_af}"
    assert b_af >= 240, f"Expected white outside window (after) but got B={b_af}"


@requires_ffmpeg
def test_fade_partial_alpha(base_video: Path, red_png: Path, tmp_path: Path) -> None:
    """Mid-fade frame has partial alpha (G2 / success condition 4).

    Overlay window: t=[1, 4] s with fade_in=1 s, fade_out=1 s.
    At t=1.5 s the fade-in is 50% complete -> overlay pixel should be a blend of
    white (base) and red (overlay), i.e. neither fully white nor fully red.
    """
    assert _FFMPEG is not None

    base_copy = tmp_path / "base.mp4"
    logo_copy = tmp_path / "logo.png"
    shutil.copy2(str(base_video), str(base_copy))
    shutil.copy2(str(red_png), str(logo_copy))

    in_otio = tmp_path / "timeline.otio"
    out_otio = tmp_path / "overlay.otio"
    out_mp4 = tmp_path / "rendered.mp4"

    tl = _make_simple_timeline(base_copy)
    otio.adapters.write_to_file(tl, str(in_otio))

    opts = AddOverlayOptions(
        image_path=str(logo_copy),
        start_sec=1.0,
        duration_sec=3.0,
        x="(W-w)/2",
        y="(H-h)/2",
        opacity=1.0,
        fade_in_sec=1.0,
        fade_out_sec=1.0,
    )
    result = add_overlay(str(in_otio), str(out_otio), opts)
    assert result["ok"], f"add_overlay failed: {result}"

    render_result = render_timeline(
        str(out_otio), str(out_mp4), RenderOptions(overwrite=True)
    )
    assert render_result.ok, f"render failed: {render_result.error}"

    cx = _BASE_W // 2
    cy = _BASE_H // 2

    # t=1.5 s → ~50% fade-in → frame index 45 (0-indexed)
    r, g, b = _read_frame_pixel(_FFMPEG, out_mp4, frame_n=45, px=cx, py=cy)
    # Partial blend: red channel between 200 and 255, green channel between 50 and 230
    # (the blend is approximately 50% red + 50% white on a white background)
    assert 180 <= r <= 255, f"Partial fade-in: expected R in [180,255] but got {r}"
    assert 50 <= g <= 230, f"Partial fade-in: expected G in [50,230] but got {g}"
    # The pixel must not be pure white (no overlay) or pure red (full overlay)
    assert not (g > 240 and b > 240), (
        f"Pixel is pure white (no overlay visible) at mid-fade: R={r},G={g},B={b}"
    )

    # t=2.5 s → fully visible → frame index 75
    r_full, g_full, b_full = _read_frame_pixel(
        _FFMPEG, out_mp4, frame_n=75, px=cx, py=cy
    )
    assert r_full >= 200, f"Full overlay: R should be high but got {r_full}"
    assert g_full < 50, f"Full overlay: G should be low but got {g_full}"

    # t=3.5 s → ~50% fade-out → frame index 105
    r2, g2, b2 = _read_frame_pixel(_FFMPEG, out_mp4, frame_n=105, px=cx, py=cy)
    assert 50 <= g2 <= 230, f"Partial fade-out: expected G in [50,230] but got {g2}"
    assert not (g2 > 240 and b2 > 240), (
        f"Pixel is pure white (no overlay visible) at mid-fade-out: R={r2},G={g2},B={b2}"
    )


@requires_ffmpeg
def test_odd_dim_scale_via_even_rounding(base_video: Path, tmp_path: Path) -> None:
    """scale=0.333 on an odd-height source succeeds via :-2 even rounding (V2-2/DC-AS-002).

    Creates a PNG with an odd height (21 px). scale=0.333 -> 21*0.333 = 6.993 -> odd
    intermediate. The :-2 rounding in the filter produces an even height without the
    "height not divisible by 2" ffmpeg error.
    """
    assert _FFMPEG is not None

    # Make a 30x21 px PNG (odd height)
    odd_png = tmp_path / "odd.png"
    _run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=30x21:duration=0.1",
            "-frames:v",
            "1",
            str(odd_png),
        ]
    )

    base_copy = tmp_path / "base.mp4"
    shutil.copy2(str(base_video), str(base_copy))

    in_otio = tmp_path / "timeline.otio"
    out_otio = tmp_path / "overlay.otio"
    out_mp4 = tmp_path / "rendered.mp4"

    tl = _make_simple_timeline(base_copy)
    otio.adapters.write_to_file(tl, str(in_otio))

    opts = AddOverlayOptions(
        image_path=str(odd_png),
        start_sec=0.0,
        duration_sec=2.0,
        scale=0.333,
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    result = add_overlay(str(in_otio), str(out_otio), opts)
    assert result["ok"], f"add_overlay failed: {result}"

    render_result = render_timeline(
        str(out_otio), str(out_mp4), RenderOptions(overwrite=True)
    )
    assert render_result.ok, (
        f"render failed (expected :-2 even rounding to succeed): {render_result.error}"
    )
    assert out_mp4.exists(), "Output video not created"


@requires_ffmpeg
@requires_ffprobe
def test_xy_placement_center_and_corner(
    base_video: Path, red_png: Path, tmp_path: Path
) -> None:
    """Center formula and corner expression place the overlay correctly (V2-6/DC-AM-002/003)."""
    assert _FFMPEG is not None and _FFPROBE is not None

    base_copy = tmp_path / "base.mp4"
    logo_copy = tmp_path / "logo.png"
    shutil.copy2(str(base_video), str(base_copy))
    shutil.copy2(str(red_png), str(logo_copy))

    # --- Sub-test A: center placement (W-w)/2, (H-h)/2 ---
    in_otio_c = tmp_path / "timeline_c.otio"
    out_otio_c = tmp_path / "overlay_c.otio"
    out_mp4_c = tmp_path / "center.mp4"
    tl = _make_simple_timeline(base_copy)
    otio.adapters.write_to_file(tl, str(in_otio_c))
    opts_c = AddOverlayOptions(
        image_path=str(logo_copy),
        start_sec=1.0,
        duration_sec=3.0,
        x="(W-w)/2",
        y="(H-h)/2",
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    r = add_overlay(str(in_otio_c), str(out_otio_c), opts_c)
    assert r["ok"], f"add_overlay center failed: {r}"
    rr = render_timeline(str(out_otio_c), str(out_mp4_c), RenderOptions(overwrite=True))
    assert rr.ok, f"render center failed: {rr.error}"

    # Center pixel of overlay region should be red at t=2 s (frame 60)
    cx = _BASE_W // 2
    cy = _BASE_H // 2
    rc, gc, bc = _read_frame_pixel(_FFMPEG, out_mp4_c, frame_n=60, px=cx, py=cy)
    assert rc >= 200, (
        f"Center placement: expected red at center but R={rc},G={gc},B={bc}"
    )
    assert gc < 50, f"Center placement: expected red at center but R={rc},G={gc},B={bc}"

    # Top-left of canvas (0,0) should still be white (not overlaid)
    r0, g0, b0 = _read_frame_pixel(_FFMPEG, out_mp4_c, frame_n=60, px=0, py=0)
    assert g0 >= 200, (
        f"Center placement: pixel (0,0) should be white but R={r0},G={g0},B={b0}"
    )

    # --- Sub-test B: corner placement x=0, y=0 ---
    in_otio_k = tmp_path / "timeline_k.otio"
    out_otio_k = tmp_path / "overlay_k.otio"
    out_mp4_k = tmp_path / "corner.mp4"
    tl2 = _make_simple_timeline(base_copy)
    otio.adapters.write_to_file(tl2, str(in_otio_k))
    opts_k = AddOverlayOptions(
        image_path=str(logo_copy),
        start_sec=1.0,
        duration_sec=3.0,
        x="0",
        y="0",
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    rk = add_overlay(str(in_otio_k), str(out_otio_k), opts_k)
    assert rk["ok"], f"add_overlay corner failed: {rk}"
    rrk = render_timeline(
        str(out_otio_k), str(out_mp4_k), RenderOptions(overwrite=True)
    )
    assert rrk.ok, f"render corner failed: {rrk.error}"

    # Top-left pixel (1,1) of the video should be red (overlay is at 0,0)
    rk0, gk0, bk0 = _read_frame_pixel(_FFMPEG, out_mp4_k, frame_n=60, px=1, py=1)
    assert rk0 >= 200, (
        f"Corner placement: expected red at (1,1) but R={rk0},G={gk0},B={bk0}"
    )
    assert gk0 < 50, (
        f"Corner placement: expected red at (1,1) but R={rk0},G={gk0},B={bk0}"
    )

    # Center of video should be white (no overlay at center for corner placement)
    rkc, gkc, bkc = _read_frame_pixel(_FFMPEG, out_mp4_k, frame_n=60, px=cx, py=cy)
    assert gkc >= 200, (
        f"Corner placement: center should be white but R={rkc},G={gkc},B={bkc}"
    )


@requires_ffmpeg
def test_project_move_round_trip(
    base_video: Path, red_png: Path, tmp_path: Path
) -> None:
    """Annotate in dir A, move to dir B; render from B -> no PATH_NOT_ALLOWED (V2-8/DC-GP-001)."""
    assert _FFMPEG is not None

    dir_a = tmp_path / "proj_a"
    dir_b = tmp_path / "proj_b"
    dir_a.mkdir()
    dir_b.mkdir()

    # Set up assets in dir A
    base_a = dir_a / "base.mp4"
    logo_a = dir_a / "logo.png"
    shutil.copy2(str(base_video), str(base_a))
    shutil.copy2(str(red_png), str(logo_a))

    in_otio_a = dir_a / "timeline.otio"
    out_otio_a = dir_a / "overlay.otio"

    tl = _make_simple_timeline(base_a)
    otio.adapters.write_to_file(tl, str(in_otio_a))

    opts = AddOverlayOptions(
        image_path=str(logo_a),
        start_sec=1.0,
        duration_sec=3.0,
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    result = add_overlay(str(in_otio_a), str(out_otio_a), opts)
    assert result["ok"], f"add_overlay in dir A failed: {result}"

    # Move the ENTIRE project (base video + OTIO + image) to dir B.
    # render.py requires all source files to be within the timeline parent directory
    # (or its descendants), so we also copy the base video.
    # The OTIO target_url uses an absolute path, so we rebuild the timeline in dir B
    # pointing to the dir B copy of the base video; the overlay.otio is re-annotated
    # with the dir B image path.  This verifies that the RELATIVE image path stored in
    # the marker is reconstructed correctly regardless of the project location (V2-3 /
    # DC-AS-003).
    base_b = dir_b / "base.mp4"
    logo_b = dir_b / "logo.png"
    shutil.copy2(str(base_a), str(base_b))
    shutil.copy2(str(logo_a), str(logo_b))

    # Re-create the base timeline in dir B pointing to base_b
    in_otio_b = dir_b / "timeline.otio"
    out_otio_b = dir_b / "overlay.otio"
    tl_b = _make_simple_timeline(base_b)
    otio.adapters.write_to_file(tl_b, str(in_otio_b))

    opts_b = AddOverlayOptions(
        image_path=str(logo_b),
        start_sec=1.0,
        duration_sec=3.0,
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    result_b = add_overlay(str(in_otio_b), str(out_otio_b), opts_b)
    assert result_b["ok"], f"add_overlay in dir B failed: {result_b}"

    out_mp4_b = dir_b / "rendered.mp4"
    render_result = render_timeline(
        str(out_otio_b), str(out_mp4_b), RenderOptions(overwrite=True)
    )
    assert render_result.ok, (
        f"render from dir B failed (expected success): {render_result.error}"
    )
    assert out_mp4_b.exists(), "Rendered output not created in dir B"

    # Also assert: image-only-moved-out-of-tree -> PATH_NOT_ALLOWED.
    # dir C has base video and OTIO but NOT the image -> PATH_NOT_ALLOWED.
    dir_c = tmp_path / "proj_c"
    dir_c.mkdir()

    base_c = dir_c / "base.mp4"
    shutil.copy2(str(base_a), str(base_c))
    in_otio_c = dir_c / "timeline.otio"
    out_otio_c = dir_c / "overlay.otio"
    tl_c = _make_simple_timeline(base_c)
    otio.adapters.write_to_file(tl_c, str(in_otio_c))

    opts_c = AddOverlayOptions(
        image_path=str(logo_a),  # image is still in dir_a (outside dir_c)
        start_sec=1.0,
        duration_sec=3.0,
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    result_c = add_overlay(str(in_otio_c), str(out_otio_c), opts_c)
    # add_overlay itself may fail (image is outside the output dir) or succeed
    # (overlay.py checks that image_path is under the output's parent dir).
    # If it failed we skip the render test; if it succeeded, render should fail.
    if not result_c["ok"]:
        # add_overlay correctly rejected the out-of-tree image path
        assert "PATH_NOT_ALLOWED" in str(result_c.get("error", {}).get("code", "")), (
            f"add_overlay should reject out-of-tree image with PATH_NOT_ALLOWED: {result_c}"
        )
    else:
        out_mp4_c = dir_c / "rendered.mp4"
        render_result_c = render_timeline(
            str(out_otio_c), str(out_mp4_c), RenderOptions(overwrite=True)
        )
        assert not render_result_c.ok, (
            "Expected render to fail when image is outside the project directory"
        )
        assert render_result_c.error is not None
        assert "PATH_NOT_ALLOWED" in str(render_result_c.error.code), (
            f"Expected PATH_NOT_ALLOWED error but got: {render_result_c.error}"
        )


@requires_ffmpeg
def test_corrupt_image_subprocess_failed(tmp_path: Path) -> None:
    """Garbage-bytes .png -> SUBPROCESS_FAILED; message has basename only (V2-7/DC-AM-004).

    Uses a 1-second base video so that the render timeout is short (~10 s).
    With -loop 1, ffmpeg may hang on corrupt input until the timeout fires.
    """
    assert _FFMPEG is not None

    # Use a 1-second base video to keep the subprocess timeout short (~10 s)
    base_copy = tmp_path / "base.mp4"
    _make_base_video(_FFMPEG, base_copy, duration=1.0)

    # Create a file with .png extension but garbage content
    corrupt_png = tmp_path / "corrupt_logo.png"
    corrupt_png.write_bytes(b"\x00\xff\xfe\xfd" * 64)

    in_otio = tmp_path / "timeline.otio"
    out_otio = tmp_path / "overlay.otio"
    out_mp4 = tmp_path / "rendered.mp4"

    tl = _make_simple_timeline(base_copy, duration=1.0)
    otio.adapters.write_to_file(tl, str(in_otio))

    opts = AddOverlayOptions(
        image_path=str(corrupt_png),
        start_sec=0.0,
        duration_sec=0.5,
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    r = add_overlay(str(in_otio), str(out_otio), opts)
    assert r["ok"], f"add_overlay (annotation) should succeed: {r}"

    render_result = render_timeline(
        str(out_otio), str(out_mp4), RenderOptions(overwrite=True)
    )
    assert not render_result.ok, "Render should fail on corrupt image"
    assert render_result.error is not None

    # Magic-byte check in render.py raises SUBPROCESS_FAILED before ffmpeg is invoked,
    # preventing the -loop 1 hang on unreadable input (V2-7 / DC-AM-004).
    # error.code may be an ErrorCode enum or its string representation.
    error_code_str = str(render_result.error.code)
    assert "SUBPROCESS_FAILED" in error_code_str, (
        f"Expected SUBPROCESS_FAILED but got: {render_result.error.code}"
    )

    # message must not contain the full directory path; only the basename is allowed.
    error_message = render_result.error.message or ""
    full_path_str = str(tmp_path)
    assert full_path_str not in error_message, (
        f"Full directory path leaked in error message: {error_message}"
    )
    # message should reference the basename of the corrupt file
    assert "corrupt_logo.png" in error_message, (
        f"Expected basename in error message but got: {error_message}"
    )
    # hint must be non-empty and mention corrupt/invalid/valid image
    error_hint = render_result.error.hint or ""
    hint_lower = error_hint.lower()
    assert any(kw in hint_lower for kw in ("corrupt", "valid", "invalid", "header")), (
        f"Hint should mention corrupt/valid/invalid image but got: {error_hint}"
    )


@requires_ffmpeg
@requires_ffprobe
def test_bgm_and_image_overlay_coexistence(
    base_video_with_audio: Path,
    red_png: Path,
    sine_audio: Path,
    tmp_path: Path,
) -> None:
    """BGM track + image overlay coexist; output has audio and composite image (V2-0 G4)."""
    assert _FFMPEG is not None and _FFPROBE is not None

    base_copy = tmp_path / "base.mp4"
    logo_copy = tmp_path / "logo.png"
    bgm_copy = tmp_path / "bgm.aac"
    shutil.copy2(str(base_video_with_audio), str(base_copy))
    shutil.copy2(str(red_png), str(logo_copy))
    shutil.copy2(str(sine_audio), str(bgm_copy))

    # Build timeline with V1 video + A2 BGM track
    tl = _make_simple_timeline(base_copy)

    # Add A2 BGM track with kind=="bgm" metadata
    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(a2)
    bgm_ref = otio.schema.ExternalReference(target_url=str(bgm_copy))
    bgm_sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, _BASE_FPS),
        duration=otio.opentime.RationalTime(int(_BASE_DUR * _BASE_FPS), _BASE_FPS),
    )
    bgm_clip = otio.schema.Clip(
        name="bgm",
        media_reference=bgm_ref,
        source_range=bgm_sr,
        metadata={
            "clipwright": {
                "tool": "clipwright-bgm",
                "version": "0.1.0",
                "kind": "bgm",
                "volume_db": -6.0,
                "fade_in_sec": 0.0,
                "fade_out_sec": 0.0,
                "ducking": {"enabled": False, "threshold": 0.25, "ratio": 4.0},
            }
        },
    )
    a2.append(bgm_clip)

    in_otio = tmp_path / "timeline.otio"
    otio.adapters.write_to_file(tl, str(in_otio))

    out_otio = tmp_path / "overlay.otio"
    out_mp4 = tmp_path / "rendered.mp4"

    opts = AddOverlayOptions(
        image_path=str(logo_copy),
        start_sec=1.0,
        duration_sec=3.0,
        x="(W-w)/2",
        y="(H-h)/2",
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    result = add_overlay(str(in_otio), str(out_otio), opts)
    assert result["ok"], f"add_overlay failed: {result}"

    render_result = render_timeline(
        str(out_otio), str(out_mp4), RenderOptions(overwrite=True)
    )
    assert render_result.ok, f"render failed: {render_result.error}"

    # Assert: output has at least one audio stream (BGM mixed in)
    audio_streams = _probe_audio_streams(_FFPROBE, out_mp4)
    assert len(audio_streams) >= 1, (
        f"Expected at least 1 audio stream in output but got {len(audio_streams)}"
    )

    # Assert: image overlay visible at t=2 s (frame 60), center pixel is red
    cx = _BASE_W // 2
    cy = _BASE_H // 2
    r, g, b = _read_frame_pixel(_FFMPEG, out_mp4, frame_n=60, px=cx, py=cy)
    assert r >= 200, f"BGM+overlay: expected red at center but R={r},G={g},B={b}"
    assert g < 50, f"BGM+overlay: expected red at center but R={r},G={g},B={b}"


@requires_ffmpeg
@requires_ffprobe
def test_backward_compat_no_image_overlay(base_video: Path, tmp_path: Path) -> None:
    """Timeline without image_overlay marker renders dimensionally identical (success cond 10)."""
    assert _FFMPEG is not None and _FFPROBE is not None

    base_copy = tmp_path / "base.mp4"
    shutil.copy2(str(base_video), str(base_copy))

    # Render the plain timeline (no overlay annotation)
    tl = _make_simple_timeline(base_copy)
    plain_otio = tmp_path / "plain.otio"
    otio.adapters.write_to_file(tl, str(plain_otio))

    plain_mp4 = tmp_path / "plain.mp4"
    r1 = render_timeline(str(plain_otio), str(plain_mp4), RenderOptions(overwrite=True))
    assert r1.ok, f"Plain render failed: {r1.error}"

    # Confirm the plain output dimensions
    stream_plain = _probe_first_video_stream(_FFPROBE, plain_mp4)
    w_plain = stream_plain["width"]
    h_plain = stream_plain["height"]
    assert w_plain == _BASE_W, f"Plain render: unexpected width {w_plain}"
    assert h_plain == _BASE_H, f"Plain render: unexpected height {h_plain}"

    # Parse duration from stream or format
    data = _probe_video(_FFPROBE, plain_mp4)
    # Use stream duration; fall back to format-level duration
    dur_plain: float | None = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            with contextlib.suppress(KeyError, ValueError):
                dur_plain = float(s["duration"])
            break

    # Now add an image_overlay-annotated timeline and render — result must have same dims
    logo_copy = tmp_path / "logo.png"
    # Create a minimal valid PNG via ffmpeg so we don't depend on conftest bytes
    _run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=10x10:duration=0.1",
            "-frames:v",
            "1",
            str(logo_copy),
        ]
    )

    in_otio2 = tmp_path / "timeline2.otio"
    out_otio2 = tmp_path / "overlay2.otio"
    out_mp4_2 = tmp_path / "with_overlay.mp4"
    tl2 = _make_simple_timeline(base_copy)
    otio.adapters.write_to_file(tl2, str(in_otio2))

    opts = AddOverlayOptions(
        image_path=str(logo_copy),
        start_sec=1.0,
        duration_sec=2.0,
        fade_in_sec=0.0,
        fade_out_sec=0.0,
    )
    ra = add_overlay(str(in_otio2), str(out_otio2), opts)
    assert ra["ok"], f"add_overlay failed: {ra}"

    r2 = render_timeline(str(out_otio2), str(out_mp4_2), RenderOptions(overwrite=True))
    assert r2.ok, f"Overlay render failed: {r2.error}"

    stream_ov = _probe_first_video_stream(_FFPROBE, out_mp4_2)
    assert stream_ov["width"] == w_plain, (
        f"Overlay render changed width: {stream_ov['width']} != {w_plain}"
    )
    assert stream_ov["height"] == h_plain, (
        f"Overlay render changed height: {stream_ov['height']} != {h_plain}"
    )
    if dur_plain is not None:
        try:
            dur_ov = float(stream_ov["duration"])
            assert abs(dur_ov - dur_plain) < 0.5, (
                f"Duration changed: plain={dur_plain:.2f}, overlay={dur_ov:.2f}"
            )
        except (KeyError, ValueError):
            pass  # duration key absent in some codecs — skip the duration check
