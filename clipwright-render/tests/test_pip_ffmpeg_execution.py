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

Bug 4 (High, CR-NEW from code-review-report-pip-ffmpeg-fix.md) — main audio +
BGM + mix_audio=True PiP double-counts the combined main+BGM signal in amix.
    `_append_pip_audio_pipe` (plan.py:4439) receives `audio_map_label` as
    `[outa_bgm]` (already main+BGM-combined by `_append_bgm_pipe`) when both
    `has_main_audio=True` and `bgm_present=True`. The "Add main audio" block
    (plan.py:4543) correctly converts it into `main_pip_fmt`/`main_mix` for
    the amix mix. But the independent "Add BGM" block (plan.py:4573,
    `if bgm_present:`) unconditionally re-appends the bare `outa_bgm` label
    as a SECOND, separate amix input — the same underlying signal is mixed
    twice (once via `main_pip_fmt`, once directly as `outa_bgm`), inflating
    the combined main+BGM amplitude relative to PiP audio. This does not
    crash ffmpeg (`amix`/`normalize=0` accepts the duplicate reference
    without error), so it is invisible to exit-code-only assertions.
    Expected fix (plan.py:4573): `if bgm_present:` -> `if bgm_present and
    not has_main_audio:`.

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
  - TestOutaBgmDoubleReference / TestMainBgmPipMixExecution (Bug 4 /
    CR-NEW): render succeeds (exit 0, no crash — kept as a passing
    regression guard) but `[outa_bgm]` appears twice as an amix input and
    `amix=inputs=N` is one higher than the correct value — the amix-input
    count / `[outa_bgm]`-occurrence assertions fail.

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

from clipwright_render.plan import (
    PipDuckingDirective,
    PipOverlay,
    _append_pip_audio_pipe,
)
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

# BGM fixture: distinguishable frequency from both main (440 Hz) and PiP
# (880 Hz), matches main clip duration to avoid loop/trim side effects
# (kept simple — this file's Bug 4 tests only need main+BGM+PiP to co-exist
# in the filtergraph, not exercise BGM loop/trim behavior; see test_e2e_bgm.py
# for that coverage).
_BGM_FREQ = 220
_BGM_VOLUME_DB = -6.0

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


def _make_bgm_audio_fixture(
    ffmpeg: str, output: Path, duration: float, freq: int = _BGM_FREQ
) -> None:
    """Generate a BGM fixture (audio-only, wrapped in a minimal video stream
    so the file is a valid media container — same pattern as
    test_e2e_bgm.py's _make_bgm_audio, file-local copy per this codebase's
    convention of no cross-test-file imports)."""
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate={int(_RATE)}:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
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
        f"BGM fixture generation failed: {result.stderr[:400]}"
    )


def _add_bgm_track(
    timeline: otio.schema.Timeline,
    bgm_path: Path,
    bgm_duration_sec: float,
    bgm_rate: float = 48000.0,
    volume_db: float = _BGM_VOLUME_DB,
    ducking_enabled: bool = False,
    ducking_threshold: float = 0.05,
    ducking_ratio: float = 4.0,
) -> None:
    """Add an A2 BGM track to the timeline (equivalent OTIO structure to what
    clipwright-bgm's add_bgm writes; file-local copy of test_e2e_bgm.py's
    _add_bgm_track, no cross-file import per this codebase's convention)."""
    bgm_directive: dict[str, Any] = {
        "tool": "clipwright-bgm",
        "version": "0.1.0",
        "kind": "bgm",
        "volume_db": volume_db,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
        "ducking": {
            "enabled": ducking_enabled,
            "threshold": ducking_threshold,
            "ratio": ducking_ratio,
        },
    }

    ref = otio.schema.ExternalReference(target_url=str(bgm_path))
    bgm_clip = otio.schema.Clip(
        name=bgm_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, bgm_rate),
            duration=otio.opentime.RationalTime(bgm_duration_sec * bgm_rate, bgm_rate),
        ),
        metadata={"clipwright": bgm_directive},
    )

    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    a2.append(bgm_clip)
    timeline.tracks.append(a2)


def _make_default_pip_overlay(**overrides: Any) -> PipOverlay:
    """Build a real PipOverlay for direct unit-level calls to
    _append_pip_audio_pipe (mirrors the report's repro command in
    code-review-report-pip-ffmpeg-fix.md)."""
    ducking = overrides.pop("ducking", None)
    if ducking is None:
        ducking = PipDuckingDirective(enabled=False)
    defaults: dict[str, Any] = dict(
        media_path="pip.mp4",
        media_start_s=0.0,
        duration_s=_PIP_DURATION_SEC,
        start_s=_PIP_START_SEC,
        end_s=_PIP_START_SEC + _PIP_DURATION_SEC,
        x="(W-w)/2",
        y="(H-h)/2",
        scale=0.3,
        opacity=1.0,
        fade_in_s=0.0,
        fade_out_s=0.0,
        input_index=1,
        mix_audio=True,
        audio_volume=1.0,
        ducking=ducking,
    )
    defaults.update(overrides)
    return PipOverlay(**defaults)


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


# ===========================================================================
# Test D: main audio + BGM + PiP audio mix (Bug 4 / CR-NEW —
# code-review-report-pip-ffmpeg-fix.md — [outa_bgm] double-referenced)
# ===========================================================================


class TestOutaBgmDoubleReference:
    """Direct unit-level repro of CR-NEW, calling _append_pip_audio_pipe the
    same way the report's repro command does (no ffmpeg involved — this
    isolates the filter_complex STRING defect from the "does ffmpeg crash"
    question, which Bug 4 does NOT trigger)."""

    def test_outa_bgm_referenced_exactly_once_no_ducking(self) -> None:
        pip = _make_default_pip_overlay(input_index=1, mix_audio=True)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [pip], "[outa_bgm]", True, 10.0, True)
        joined = ";".join(filter_parts)

        assert joined.count("[outa_bgm]") == 1, (
            "CR-NEW: [outa_bgm] (main audio already combined with BGM by"
            " _append_bgm_pipe) must be referenced exactly once — as the"
            " input converted into main_pip_fmt for the amix mix. The"
            " current implementation ALSO re-appends a bare 'outa_bgm' as a"
            " second, independent amix input (plan.py:4573 `if"
            " bgm_present:` is missing an `and not has_main_audio` guard),"
            " double-counting the same main+BGM signal and skewing the mix"
            f" balance. filter_parts={joined!r}"
        )
        assert "amix=inputs=2:normalize=0" in joined, (
            "Expected amix inputs = 1 (combined main+BGM signal) + 1 (the"
            " single mix_audio=True PiP) = 2. Bug 4 currently produces"
            f" amix=inputs=3 (main+BGM counted twice). filter_parts={joined!r}"
        )

    def test_outa_bgm_referenced_exactly_once_with_ducking(self) -> None:
        pip = _make_default_pip_overlay(
            input_index=1,
            mix_audio=True,
            ducking=PipDuckingDirective(enabled=True, threshold=0.3, ratio=4.0),
        )
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [pip], "[outa_bgm]", True, 10.0, True)
        joined = ";".join(filter_parts)

        assert joined.count("[outa_bgm]") == 1, (
            "CR-NEW (ducking-enabled branch): same double-reference defect"
            " as the non-ducking case — the sidechain correctly uses"
            " main_pip_fmt/main_mix (derived from [outa_bgm]), but the BGM"
            f" block still re-adds a bare 'outa_bgm' amix input."
            f" filter_parts={joined!r}"
        )
        assert "amix=inputs=2:normalize=0" in joined, (
            "Expected amix inputs = 1 (combined main+BGM signal, asplit into"
            " main_mix + sidechain) + 1 (the single ducked PiP). Bug 4"
            f" currently produces amix=inputs=3. filter_parts={joined!r}"
        )


@requires_ffmpeg
class TestMainBgmPipMixExecution:
    """Real-ffmpeg regression coverage for CR-NEW: main audio + BGM + a
    mix_audio=True PiP must render successfully (kept as a crash-regression
    guard — Bug 4 does NOT crash ffmpeg, since amix/normalize=0 silently
    accepts the duplicate [outa_bgm] reference) AND the generated
    filter_complex's amix input count must reflect ONE combined main+BGM
    signal + ONE PiP signal, not two independent "main" and "bgm" entries."""

    def _build_timeline(
        self, tmp_path: Path, *, ducking: dict[str, Any] | None
    ) -> Path:
        main_src, pip_src = _make_main_and_pip_fixtures(tmp_path)
        bgm_src = tmp_path / "bgm.mp4"
        _make_bgm_audio_fixture(_FFMPEG, bgm_src, duration=_MAIN_DUR)  # type: ignore[arg-type]

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _MAIN_DUR)
        _add_pip_overlay_marker(
            timeline,
            media_path=str(pip_src),
            start_sec=_PIP_START_SEC,
            duration_sec=_PIP_DURATION_SEC,
            mix_audio=True,
            audio_volume=1.0,
            ducking=ducking
            if ducking is not None
            else {"enabled": False, "threshold": 0.05, "ratio": 4.0},
        )
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)
        return timeline_path

    def test_main_bgm_pip_mix_no_ducking(self, tmp_path: Path) -> None:
        assert _FFMPEG is not None
        timeline_path = self._build_timeline(tmp_path, ducking=None)

        # Crash-regression guard FIRST: real ffmpeg execution must still
        # succeed (Bug 4 does not crash ffmpeg — amix/normalize=0 silently
        # accepts the duplicate [outa_bgm] reference). This is expected to
        # PASS even in the current (buggy) state.
        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, (
            "real ffmpeg execution must succeed (Bug 4 does not crash"
            f" ffmpeg — kept as a regression guard). render result: {result}"
        )
        assert out_path.exists(), "Output file was not created"

        # Mix-correctness check SECOND: this is the part that is expected
        # to be Red until CR-NEW (plan.py:4573) is fixed.
        dry = render_timeline(
            str(timeline_path),
            str(tmp_path / "out_dry.mp4"),
            RenderOptions(),
            dry_run=True,
        )
        assert dry["ok"] is True, f"dry_run failed unexpectedly: {dry}"
        fc = dry["data"]["filter_complex"]

        # [outa_bgm] is declared once (the amix output label of the earlier
        # BGM stage from _append_bgm_pipe) plus consumed once (as the input
        # converted into main_pip_fmt) = 2 total occurrences when Bug 4 is
        # fixed. The buggy implementation re-adds a THIRD occurrence (a bare
        # 'outa_bgm' amix input), so this fails at 3 today.
        assert fc.count("[outa_bgm]") == 2, (
            "CR-NEW: [outa_bgm] must occur exactly twice in the full"
            " build_plan()-generated filter_complex (1 declaration as the"
            " BGM amix output + 1 consumption as the input converted into"
            " main_pip_fmt) — NOT a third time as an independent amix"
            f" input. filter_complex={fc!r}"
        )
        assert "amix=inputs=2:normalize=0" in fc, (
            "Expected amix inputs = 1 (main+BGM combined) + 1 (PiP audio) ="
            f" 2. filter_complex={fc!r}"
        )

    def test_main_bgm_pip_mix_with_ducking(self, tmp_path: Path) -> None:
        assert _FFMPEG is not None
        timeline_path = self._build_timeline(
            tmp_path,
            ducking={"enabled": True, "threshold": 0.3, "ratio": 4.0},
        )

        # Crash-regression guard FIRST (see non-ducking variant above for
        # rationale). Expected to PASS even in the current (buggy) state.
        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, (
            "real ffmpeg execution must succeed (Bug 4 does not crash"
            f" ffmpeg — kept as a regression guard). render result: {result}"
        )
        assert out_path.exists(), "Output file was not created"

        # Mix-correctness check SECOND: this is the part that is expected
        # to be Red until CR-NEW (plan.py:4573) is fixed.
        dry = render_timeline(
            str(timeline_path),
            str(tmp_path / "out_dry.mp4"),
            RenderOptions(),
            dry_run=True,
        )
        assert dry["ok"] is True, f"dry_run failed unexpectedly: {dry}"
        fc = dry["data"]["filter_complex"]

        assert fc.count("[outa_bgm]") == 2, (
            "CR-NEW (ducking-enabled branch): [outa_bgm] must occur exactly"
            " twice (1 declaration as the BGM amix output + 1 consumption"
            " as the input converted into main_pip_fmt) — NOT a third time"
            f" as an independent amix input. filter_complex={fc!r}"
        )
        assert "amix=inputs=2:normalize=0" in fc, (
            "Expected amix inputs = 1 (main+BGM combined, asplit for the"
            " sidechain) + 1 (the single ducked PiP) = 2."
            f" filter_complex={fc!r}"
        )
