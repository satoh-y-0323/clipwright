"""test_pip_ffmpeg_execution.py — Real ffmpeg-execution Red tests for 3
runtime bugs found by the real MCP e2e smoke test
(clipwright_test/pip_e2e_smoke.py) that the existing string-assertion-only
PiP unit tests (test_pip_video.py / test_pip_audio.py /
test_pip_render_wiring.py / test_pip_ducking_integration.py) could not
catch, because those tests only assert on the *shape* of `filter_complex`
strings without ever handing them to a real ffmpeg process.

Bugs under test (all reproduced against real ffmpeg 8.1.1 / Gyan build):

  Bug 1 (Critical) — PiP video is never composited.
    `_append_pip_video_filter` (plan.py:1174) is defined but never called
    from either `_build_filter_complex` (plan.py:3774) or
    `_build_multi_source_filter_complex` (plan.py:4181). `build_plan()`
    still threads `pip_sources` into the ffmpeg `-i` list (render.py), so
    ffmpeg exits 0 — there is no SUBPROCESS_FAILED signal. The failure is
    only visible by inspecting actual output pixels: the PiP overlay
    window never shows the PiP source's color.

  Bug 2 (High) — mix_audio=True always fails with SUBPROCESS_FAILED.
    `_append_pip_audio_pipe` (plan.py:4423) builds each PiP audio branch's
    `base_branch` using `trim=start=...:duration=...` (plan.py:4480) — the
    VIDEO trim filter — against an AUDIO stream (`[{pip.input_index}:a]`).
    ffmpeg rejects this with "Media type mismatch between corresponding
    streams" and exits non-zero. The correct filter is `atrim=`.

  Bug 3 (High) — ducking.enabled=True always fails with SUBPROCESS_FAILED.
    Still inside `_append_pip_audio_pipe`, when ducking is enabled the code
    does `asplit[pip{i}_audio][pip{i}_sc_in]` (plan.py:4490-4492) intending
    to route `pip{i}_sc_in` into a later sidechaincompress call. But the
    sidechaincompress wiring further down (plan.py:4526-4544) only ever
    references `pip_branch_labels[i][0]` (== `pip{i}_audio`, the FIRST
    asplit output) as the compressed signal and `main_pip_fmt` / `outa_bgm`
    as the sidechain trigger — `pip{i}_sc_in` (the SECOND asplit output) is
    never referenced by ANY filter afterwards. ffmpeg rejects this with
    "Filter 'asplit' has output N (pip{i}_sc_in) unconnected" (a graph
    validation error auto-detects unconnected pads only at actual
    execution time — dry_run's filter_complex string looks superficially
    fine).

Expected Red state (bugs 1-3 unfixed):
  - TestPipVideoCompositionExecution: render succeeds (exit 0) but the
    in-window pixel does NOT show the PiP source's color (Bug 1) —
    assertion failure, not a crash.
  - TestPipAudioMixExecution: render_timeline() returns ok=False /
    SUBPROCESS_FAILED (Bug 2) — the `ok is True` assertion fails.
  - TestPipAudioDuckingExecution: the asplit-output-connectivity check on
    the dry_run filter_complex fails BEFORE any ffmpeg call is made (static
    proof of Bug 3), AND the real (dry_run=False) render also fails with
    SUBPROCESS_FAILED (Bug 2 + Bug 3 both apply to the ducking-enabled
    branch, since it shares the same buggy base_branch construction).

How to run (skipped when ffmpeg is absent — see conftest.py / CLIPWRIGHT_FFMPEG):
  uv run --package clipwright-render pytest -k pip_ffmpeg_execution

IMPORTANT: run with `uv run pytest` (not bare `pytest`) — a bare interpreter
without the workspace venv is a known environment pitfall in this repo.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# ===========================================================================
# ffmpeg binary resolution (same pattern as conftest.py / test_e2e_bgm.py)
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
# Constants
# ===========================================================================

_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

_RATE = 25.0  # fps
_WIDTH = 320
_HEIGHT = 240

_MAIN_DUR = 5.0
_MAIN_COLOR = "white"
_MAIN_FREQ = 440

_PIP_DUR = 3.0  # PiP source media duration (>= overlay window duration)
_PIP_COLOR = "red"
_PIP_FREQ = 880

# PiP overlay placement window: [1.0, 4.0) seconds on the main timeline.
_PIP_START_SEC = 1.0
_PIP_DURATION_SEC = 3.0

# ===========================================================================
# Helpers: fixture generation
# ===========================================================================


def _make_color_video_with_audio(
    ffmpeg: str,
    output: Path,
    *,
    color: str,
    freq: int,
    duration: float,
    width: int = _WIDTH,
    height: int = _HEIGHT,
    rate: float = _RATE,
) -> None:
    """Generate a solid-color video with a sine-tone audio track.

    -g 1 forces every frame to be a keyframe so downstream pixel extraction
    does not depend on GOP structure.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color={color}:size={width}x{height}:rate={int(rate)}:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-g",
        "1",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
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
        f"Fixture generation failed ({output.name}): {result.stderr[:400]}"
    )


# ===========================================================================
# Helpers: pixel-level output inspection
# ===========================================================================


def _extract_pixel_track(ffmpeg: str, video: Path, px: int, py: int) -> bytes:
    """Extract a single (px, py) pixel from EVERY frame of `video` as a
    contiguous sequence of RGB24 bytes (3 bytes per frame, in decode order).

    A single ffmpeg call with `crop=2:2:{px}:{py},scale=1:1` + rawvideo/rgb24
    output is far cheaper than extracting a PNG per frame (mirrors the
    spirit of clipwright_test/pip_e2e_smoke.py's read_frame_pixel but reads
    the whole per-frame pixel track in one subprocess call). A 1x1 crop is
    avoided: yuv420p's subsampled chroma planes make ffmpeg reject an
    odd-numbered 1x1 crop ("Invalid too big or non positive size for width
    '0'"), so a 2x2 region is cropped and then downscaled to 1x1.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-vf",
        f"crop=2:2:{px}:{py},scale=1:1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=_E2E_TIMEOUT)
    assert result.returncode == 0, (
        f"Pixel-track extraction failed: {result.stderr[:400]!r}"
    )
    return result.stdout


def _pixel_at_frame(pixel_track: bytes, frame_index: int) -> tuple[int, int, int]:
    """Return the RGB tuple for a given 0-based frame index in a pixel
    track produced by _extract_pixel_track."""
    offset = frame_index * 3
    assert offset + 3 <= len(pixel_track), (
        f"frame_index={frame_index} out of range"
        f" (pixel track has {len(pixel_track) // 3} frames,"
        f" {len(pixel_track)} bytes)"
    )
    return (
        pixel_track[offset],
        pixel_track[offset + 1],
        pixel_track[offset + 2],
    )


# ===========================================================================
# Helpers: OTIO timeline construction (mirrors test_e2e_bgm.py /
# test_pip_ducking_integration.py's marker-building convention)
# ===========================================================================


def _make_base_timeline(
    source_path: Path,
    duration_sec: float = _MAIN_DUR,
    rate: float = _RATE,
) -> otio.schema.Timeline:
    """Generate a single-clip main OTIO timeline (Video track only)."""
    ref = otio.schema.ExternalReference(target_url=str(source_path))
    clip = otio.schema.Clip(
        name=source_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    timeline = otio.schema.Timeline(name="e2e_pip_ffmpeg_execution_test")
    timeline.tracks.append(track)
    return timeline


def _add_pip_overlay_marker(
    timeline: otio.schema.Timeline,
    *,
    media_path: str,
    start_sec: float = _PIP_START_SEC,
    duration_sec: float = _PIP_DURATION_SEC,
    media_start_sec: float = 0.0,
    x: str = "(W-w)/2",
    y: str = "(H-h)/2",
    scale: float = 0.3,
    opacity: float = 1.0,
    fade_in_sec: float = 0.0,
    fade_out_sec: float = 0.0,
    mix_audio: bool = False,
    audio_volume: float = 1.0,
    ducking: dict[str, Any] | None = None,
    rate: float = _RATE,
    name: str = "pip_0",
) -> None:
    """Attach a pip_overlay marker to the first video track.

    Metadata shape mirrors what clipwright-overlay's clipwright_add_pip
    actually writes (see test_pip_video.py::_add_pip_overlay_marker and
    test_pip_ducking_integration.py::_make_pip_marker — this helper is a
    file-local copy, no cross-file import, per this codebase's convention).
    Fade defaults to 0 here (unlike test_pip_video.py's 0.3/0.3) so the
    pixel-composition test in this file gets a crisp, unblended color at
    the sample points instead of a partially-faded one.
    """
    if ducking is None:
        ducking = {"enabled": False, "threshold": 0.05, "ratio": 4.0}
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"

    marked_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start_sec * rate, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    marker = otio.schema.Marker(
        name=name,
        marked_range=marked_range,
        metadata={
            "clipwright": {
                "kind": "pip_overlay",
                "tool": "clipwright-overlay",
                "version": "0.1.0",
                "media_path": media_path,
                "start_sec": start_sec,
                "duration_sec": duration_sec,
                "media_start_sec": media_start_sec,
                "x": x,
                "y": y,
                "scale": scale,
                "opacity": opacity,
                "fade_in_sec": fade_in_sec,
                "fade_out_sec": fade_out_sec,
                "mix_audio": mix_audio,
                "audio_volume": audio_volume,
                "ducking": ducking,
            }
        },
    )
    video_track.markers.append(marker)


def _save_timeline(timeline: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(timeline, str(path))


def _make_main_and_pip_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    assert _FFMPEG is not None
    main_src = tmp_path / "main.mp4"
    pip_src = tmp_path / "pip.mp4"
    _make_color_video_with_audio(
        _FFMPEG, main_src, color=_MAIN_COLOR, freq=_MAIN_FREQ, duration=_MAIN_DUR
    )
    _make_color_video_with_audio(
        _FFMPEG, pip_src, color=_PIP_COLOR, freq=_PIP_FREQ, duration=_PIP_DUR
    )
    return main_src, pip_src


# ===========================================================================
# Test A: PiP video composition (Bug 1 — _append_pip_video_filter dead code)
# ===========================================================================


@requires_ffmpeg
class TestPipVideoCompositionExecution:
    """mix_audio=False PiP: render must succeed AND actually composite the
    PiP source's color into the placement window (Bug 1 real repro)."""

    def test_pip_composited_in_window_absent_out_of_window(
        self, tmp_path: Path
    ) -> None:
        assert _FFMPEG is not None
        main_src, pip_src = _make_main_and_pip_fixtures(tmp_path)

        timeline = _make_base_timeline(main_src)
        _add_pip_overlay_marker(
            timeline,
            media_path=str(pip_src),
            start_sec=_PIP_START_SEC,
            duration_sec=_PIP_DURATION_SEC,
            mix_audio=False,
        )
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render failed unexpectedly: {result}"
        assert out_path.exists(), "Output file was not created"

        cx, cy = _WIDTH // 2, _HEIGHT // 2
        pixel_track = _extract_pixel_track(_FFMPEG, out_path, cx, cy)

        # In-window: t=2.0s (window is [1.0, 4.0)) -> frame index 50 @25fps.
        r_in, g_in, b_in = _pixel_at_frame(pixel_track, frame_index=50)
        # Out-of-window: t=0.4s (before start=1.0s) -> frame index 10 @25fps.
        r_out, g_out, b_out = _pixel_at_frame(pixel_track, frame_index=10)

        assert r_in >= 150 and g_in < 100, (
            "PiP was not composited in-window (Bug 1:"
            " _append_pip_video_filter is defined in plan.py but never"
            " called from _build_filter_complex /"
            " _build_multi_source_filter_complex, so pip_sources are added"
            " as ffmpeg -i inputs but never referenced by filter_complex)."
            f" Expected a reddish pixel at center (t=2.0s, in [1.0,4.0)"
            f" window); got RGB=({r_in},{g_in},{b_in})."
        )
        assert r_out >= 230 and g_out >= 230 and b_out >= 230, (
            "Out-of-window center pixel (t=0.4s, before window start=1.0s)"
            f" is not the base white color: got RGB=({r_out},{g_out},{b_out})."
            " This assertion isolates the base video from PiP contamination"
            " so a failure here means the FIXTURE is wrong, not the bug"
            " under test."
        )


# ===========================================================================
# Test B: PiP audio mix without ducking (Bug 2 — trim= instead of atrim=)
# ===========================================================================


@requires_ffmpeg
class TestPipAudioMixExecution:
    """mix_audio=True, ducking disabled: real ffmpeg execution must
    succeed (exit code 0) — Bug 2 real repro."""

    def test_mix_audio_true_ducking_disabled_render_succeeds(
        self, tmp_path: Path
    ) -> None:
        assert _FFMPEG is not None
        main_src, pip_src = _make_main_and_pip_fixtures(tmp_path)

        timeline = _make_base_timeline(main_src)
        _add_pip_overlay_marker(
            timeline,
            media_path=str(pip_src),
            start_sec=_PIP_START_SEC,
            duration_sec=_PIP_DURATION_SEC,
            mix_audio=True,
            audio_volume=1.0,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 4.0},
        )
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, (
            "render_timeline(mix_audio=True, ducking=False) failed (Bug 2:"
            " _append_pip_audio_pipe's base_branch applies trim= — the"
            " VIDEO trim filter — to an audio stream [N:a] instead of"
            " atrim=, so ffmpeg rejects the graph with 'Media type"
            f" mismatch'). render result: {result}"
        )
        assert out_path.exists(), "Output file was not created"


# ===========================================================================
# Test C: PiP audio mix WITH ducking (Bug 3 — asplit unconnected output)
# ===========================================================================


@requires_ffmpeg
class TestPipAudioDuckingExecution:
    """mix_audio=True, ducking.enabled=True: the asplit outputs used to
    route the ducking sidechain must ALL be consumed downstream, and the
    real ffmpeg execution must succeed (exit code 0) — Bug 3 real repro."""

    def _build_ducking_timeline(self, tmp_path: Path) -> Path:
        main_src, pip_src = _make_main_and_pip_fixtures(tmp_path)
        timeline = _make_base_timeline(main_src)
        _add_pip_overlay_marker(
            timeline,
            media_path=str(pip_src),
            start_sec=_PIP_START_SEC,
            duration_sec=_PIP_DURATION_SEC,
            mix_audio=True,
            audio_volume=1.0,
            ducking={"enabled": True, "threshold": 0.3, "ratio": 4.0},
        )
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)
        return timeline_path

    def test_ducking_asplit_outputs_are_all_consumed_in_filter_complex(
        self, tmp_path: Path
    ) -> None:
        """Static proof of Bug 3, ahead of any ffmpeg call: for every
        asplit[A][B] stage emitted for a ducking-enabled PiP audio branch,
        both A and B must be referenced as an INPUT by some later filter
        (i.e. appear as "[label]" at least twice total in filter_complex —
        once as the asplit output declaration, at least once more as a
        consumer). A label occurring only once is an unconnected asplit
        output pad, which ffmpeg rejects at graph-validation time even
        though the filter_complex STRING looks superficially well-formed.
        """
        timeline_path = self._build_ducking_timeline(tmp_path)
        out_path = tmp_path / "out_dry.mp4"

        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run failed unexpectedly: {result}"
        fc = result["data"]["filter_complex"]

        asplit_pairs = re.findall(r"asplit\[(\w+)\]\[(\w+)\]", fc)
        assert asplit_pairs, (
            "Expected at least one asplit stage for the ducking-enabled PiP"
            f" audio branch: filter_complex={fc!r}"
        )

        unconnected: list[str] = []
        for label_a, label_b in asplit_pairs:
            for label in (label_a, label_b):
                occurrences = fc.count(f"[{label}]")
                if occurrences < 2:
                    unconnected.append(label)

        assert not unconnected, (
            "Bug 3: the following asplit output labels are produced but"
            f" never consumed downstream: {unconnected!r} (each must appear"
            " as an INPUT to some later filter, e.g. sidechaincompress)."
            " ffmpeg would reject this graph at execution time with"
            " \"Filter 'asplit' has output N (<label>) unconnected\"."
            f" filter_complex={fc!r}"
        )

    def test_ducking_enabled_render_succeeds(self, tmp_path: Path) -> None:
        assert _FFMPEG is not None
        timeline_path = self._build_ducking_timeline(tmp_path)
        out_path = tmp_path / "out.mp4"

        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, (
            "render_timeline(mix_audio=True, ducking.enabled=True) failed"
            " (Bug 3: the asplit output routed for the sidechain input"
            " [pip{i}_sc_in] is never consumed by any later filter, so"
            " ffmpeg rejects the graph with 'Filter asplit has output N"
            " unconnected' — compounded by Bug 2's trim=/atrim= mismatch"
            f" on the same branch). render result: {result}"
        )
        assert out_path.exists(), "Output file was not created"
