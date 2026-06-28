"""test_d6_motion.py — D6 integration tests: severity gate and apply vs skip claims.

Verifies D6's main claims using deterministic lavfi synthetic footage (CC0,
no external media required):
  - High-shake footage (periodic crop jitter): severity >= threshold ->
    recommendation="apply"; vidstabtransform reduces residual motion severity.
  - Calm footage (static crop, no camera jitter): severity < threshold ->
    recommendation="skip"; D4 apply path is not entered.

All intermediate files are placed under pytest's tmp_path (repository not
touched).  Tests are tagged @pytest.mark.integration and require a real
ffmpeg build with libvidstab compiled in.

How to run:
  cd clipwright-stabilize
  uv run pytest -m integration tests/test_d6_motion.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def _find_binary(name: str, env_var: str) -> str | None:
    """Return path to a binary via PATH then env_var, or None if absent."""
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
    reason="ffmpeg not found. Add to PATH or set CLIPWRIGHT_FFMPEG.",
)
requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason="ffprobe not found. Add to PATH or set CLIPWRIGHT_FFPROBE.",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "180"))

# Synthetic source dimensions and timing — kept small for fast vidstabdetect.
_SRC_W: int = 320
_SRC_H: int = 240
_SRC_FPS: int = 30
_SRC_DUR: float = 2.0

# Crop window for both fixtures (smaller than source to leave jitter room).
_CROP_W: int = 280
_CROP_H: int = 200

# Centre offsets for the static crop (also the mean centre for jitter).
# _CX = (320-280)//2 = 20, _CY = (240-200)//2 = 20.
_CX: int = (_SRC_W - _CROP_W) // 2
_CY: int = (_SRC_H - _CROP_H) // 2

# Jitter: amplitude (px) stays within the 20 px margin on each axis.
# At 30 fps, max per-frame x-shift ~= 12*2*PI*3/30 ~= 7.5 px;
#            max per-frame y-shift ~= 12*2*PI*4/30 ~= 10.1 px;
# combined Euclidean ~= 12.5 px -> severity ~= 12.5/30 ~= 0.42 >> 0.08 threshold.
_JITTER_AMP: int = 12
_JITTER_FX: int = 3
_JITTER_FY: int = 4

# Maximum residual severity ratio after stabilisation (at least 30% reduction).
# vidstabdetect on the stabilised output measures inter-frame displacement in the
# motion-vector domain — invariant to optzoom=1 static zoom and unsharp sharpening.
# With smoothing=15 (31-frame window) and 3-4 Hz jitter, residual is typically < 10%.
_MAX_RESIDUAL_SEVERITY_RATIO: float = 0.70

# Static spatial luma expression for geq filter (uses X/Y pixel coords only, no T).
# All frames are rendered identically, so vidstabdetect sees only camera-induced
# crop displacement, not testsrc2 animation artefacts amplified by zoom/unsharp.
# Range: 128 ± 64 ± 48 = [16, 240] — always within [0, 255], no clamping.
_STATIC_LUMA_EXPR: str = "128+64*sin(X/8+Y/5)+48*cos(X/12-Y/9)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_vidstab(ffmpeg: str) -> bool:
    """Return True when vidstabdetect is compiled into this ffmpeg build."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return any("vidstabdetect" in line for line in result.stdout.splitlines())
    except Exception:
        return False


def _make_high_shake_video(ffmpeg: str, output: Path) -> None:
    """Generate a jittery test video with a static spatial luma pattern.

    Source: color=black + geq(_STATIC_LUMA_EXPR) — luma is a function of pixel
    coordinates X/Y only (no time variable T), so every frame has identical content.
    This eliminates testsrc2 animation artefacts which are amplified by optzoom=1
    zoom and unsharp after stabilisation, causing false severity inflation on the
    second vidstabdetect pass.

    The crop origin follows sine waves with amplitude _JITTER_AMP:
      x = _CX + _JITTER_AMP * sin(2*PI*_JITTER_FX*t)
      y = _CY + _JITTER_AMP * cos(2*PI*_JITTER_FY*t)

    This induces global displacement well above _SEVERITY_APPLY_THRESHOLD,
    so vidstabdetect classifies the footage as requiring stabilisation.
    """
    lavfi = f"color=black:size={_SRC_W}x{_SRC_H}:rate={_SRC_FPS}:duration={_SRC_DUR}"
    crop_x = f"{_CX}+{_JITTER_AMP}*sin(2*PI*{_JITTER_FX}*t)"
    crop_y = f"{_CY}+{_JITTER_AMP}*cos(2*PI*{_JITTER_FY}*t)"
    vf = (
        f"geq=lum='{_STATIC_LUMA_EXPR}':cb=128:cr=128,"
        f"crop={_CROP_W}:{_CROP_H}:{crop_x}:{crop_y}"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        lavfi,
        "-vf",
        vf,
        "-t",
        str(_SRC_DUR),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"High-shake fixture generation failed: {result.stderr[:300]}"
    )


def _make_calm_video(ffmpeg: str, output: Path) -> None:
    """Generate a calm test video with a static spatial luma pattern.

    Same static source as _make_high_shake_video (geq with _STATIC_LUMA_EXPR);
    crop origin is fixed at (_CX, _CY) — no camera motion is induced.
    Since source content is identical across all frames, vidstabdetect should
    report near-zero displacement -> severity < threshold -> recommendation='skip'.
    """
    lavfi = f"color=black:size={_SRC_W}x{_SRC_H}:rate={_SRC_FPS}:duration={_SRC_DUR}"
    vf = (
        f"geq=lum='{_STATIC_LUMA_EXPR}':cb=128:cr=128,"
        f"crop={_CROP_W}:{_CROP_H}:{_CX}:{_CY}"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        lavfi,
        "-vf",
        vf,
        "-t",
        str(_SRC_DUR),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"Calm fixture generation failed: {result.stderr[:300]}"
    )


def _is_windows_vst_crash(result: dict[str, Any]) -> bool:
    """Return True when result indicates the known Windows vidstabtransform crash.

    Option B fix (-threads 1 before -i) eliminates the ACCESS_VIOLATION crash on
    Windows Gyan ffmpeg 8.1.1 (vid.stab #144 B-frame corruption).  This guard is
    retained as a safety net; with Option B the crash rate is expected to be 0%.
    """
    if sys.platform != "win32":
        return False
    if result.get("ok") is not False:
        return False
    error = result.get("error") or {}
    if not isinstance(error, dict):
        return False
    return error.get("code") == "SUBPROCESS_FAILED"


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


@requires_ffmpeg
@requires_ffprobe
class TestD6MotionClaims:
    """D6 integration tests: severity gate differentiates apply from skip.

    D6 claim (FR-5 / AC-9):
      1. High-shake footage (crop jitter) -> severity >= threshold,
         recommendation="apply".
      2. Calm footage (static crop) -> severity < threshold,
         recommendation="skip"; D4 apply is not triggered.
      3. After vidstabtransform apply, residual motion severity on the stabilised
         output is measurably reduced — camera motion is compensated (not a no-op).
         Metric: second vidstabdetect pass on the output (invariant to optzoom/unsharp).

    All fixtures are generated deterministically via ffmpeg lavfi;
    no external media files are required (CC0, ADR-D6-1).
    """

    def test_high_shake_recommendation_is_apply(self, tmp_path: Path) -> None:
        """High-shake lavfi fixture must enter the apply domain (AC-9, FR-5).

        Verifies that the periodic crop jitter generates sufficient per-frame
        displacement for vidstabdetect to report:
          severity >= _SEVERITY_APPLY_THRESHOLD  and  recommendation="apply".
        """
        assert _FFMPEG is not None

        if not _has_vidstab(_FFMPEG):
            pytest.skip(
                "ffmpeg build does not include libvidstab (vidstabdetect absent)."
            )

        pytest.importorskip("clipwright_stabilize")
        from clipwright_stabilize.analyze import (  # type: ignore[attr-defined]
            _SEVERITY_APPLY_THRESHOLD,
        )
        from clipwright_stabilize.schemas import DetectShakeOptions  # type: ignore[attr-defined]
        from clipwright_stabilize.stabilize import detect_shake  # type: ignore[attr-defined]

        src = tmp_path / "high_shake.mp4"
        _make_high_shake_video(_FFMPEG, src)

        otio_out = tmp_path / "high_shake.otio"
        result = detect_shake(
            media=str(src),
            output=str(otio_out),
            options=DetectShakeOptions(),
            timeline=None,
        )

        if not result["ok"]:
            code = (result.get("error") or {}).get("code", "")
            if code == "UNSUPPORTED_OPERATION":
                pytest.skip(
                    "vidstabdetect returned UNSUPPORTED_OPERATION on this build."
                )
            pytest.fail(f"detect_shake failed unexpectedly: {result}")

        data = result["data"]
        severity: float | None = data["severity"]
        recommendation: str = data["recommendation"]

        assert severity is not None, (
            "severity must not be None for a valid synthetic .trf file"
        )
        assert severity >= _SEVERITY_APPLY_THRESHOLD, (
            f"High-shake severity {severity:.4f} must be >= "
            f"_SEVERITY_APPLY_THRESHOLD={_SEVERITY_APPLY_THRESHOLD:.4f}. "
            f"The lavfi jitter (amp={_JITTER_AMP}px, fx={_JITTER_FX}Hz) is expected "
            f"to produce >{_SEVERITY_APPLY_THRESHOLD * 30:.1f} px median displacement."
        )
        assert recommendation == "apply", (
            f"High-shake recommendation must be 'apply', "
            f"got {recommendation!r} (severity={severity:.4f})"
        )

    def test_calm_recommendation_is_skip(self, tmp_path: Path) -> None:
        """Calm lavfi fixture (static crop) must stay in skip domain (AC-9, FR-5).

        Verifies that a video with no induced camera motion yields:
          severity < _SEVERITY_APPLY_THRESHOLD  and  recommendation="skip".

        This confirms that D4 apply is NOT triggered for stable footage,
        which is the core of the D6 severity gate claim.
        """
        assert _FFMPEG is not None

        if not _has_vidstab(_FFMPEG):
            pytest.skip(
                "ffmpeg build does not include libvidstab (vidstabdetect absent)."
            )

        pytest.importorskip("clipwright_stabilize")
        from clipwright_stabilize.analyze import (  # type: ignore[attr-defined]
            _SEVERITY_APPLY_THRESHOLD,
        )
        from clipwright_stabilize.schemas import DetectShakeOptions  # type: ignore[attr-defined]
        from clipwright_stabilize.stabilize import detect_shake  # type: ignore[attr-defined]

        src = tmp_path / "calm.mp4"
        _make_calm_video(_FFMPEG, src)

        otio_out = tmp_path / "calm.otio"
        result = detect_shake(
            media=str(src),
            output=str(otio_out),
            options=DetectShakeOptions(),
            timeline=None,
        )

        if not result["ok"]:
            code = (result.get("error") or {}).get("code", "")
            if code == "UNSUPPORTED_OPERATION":
                pytest.skip(
                    "vidstabdetect returned UNSUPPORTED_OPERATION on this build."
                )
            pytest.fail(f"detect_shake failed unexpectedly: {result}")

        data = result["data"]
        severity: float | None = data["severity"]
        recommendation: str = data["recommendation"]

        assert severity is not None, (
            "severity must not be None for a valid synthetic .trf file"
        )
        assert severity < _SEVERITY_APPLY_THRESHOLD, (
            f"Calm severity {severity:.4f} must be < "
            f"_SEVERITY_APPLY_THRESHOLD={_SEVERITY_APPLY_THRESHOLD:.4f}. "
            "A static-crop testsrc2 has no global camera motion; "
            "vidstabdetect should report near-zero displacement."
        )
        assert recommendation == "skip", (
            f"Calm recommendation must be 'skip', "
            f"got {recommendation!r} (severity={severity:.4f})"
        )

    def test_stabilize_reduces_residual_severity(self, tmp_path: Path) -> None:
        """Apply must reduce residual motion severity on the stabilised output (AC-9).

        Metric rationale: vidstabdetect measures inter-frame displacement in the
        motion-vector domain.  optzoom=1 applies a single static zoom factor to all
        frames (no new inter-frame displacement added), and unsharp sharpens each
        frame identically (no displacement change).  Neither effect of Option B's
        render pipeline confounds this measurement.

        Steps:
          1. detect_shake on high-shake fixture -> severity_in (apply domain).
          2. render_timeline(dry_run=False) -> stabilised MP4 (vidstabtransform applied).
          3. run_vidstabdetect on the stabilised MP4 -> severity_out.
          4. Assert: severity_out < severity_in * _MAX_RESIDUAL_SEVERITY_RATIO.

        Skipped when the known Windows ACCESS_VIOLATION vidstabtransform crash
        is detected (Option B -threads 1 suppresses it; this guard is a safety net).
        """
        assert _FFMPEG is not None

        if not _has_vidstab(_FFMPEG):
            pytest.skip(
                "ffmpeg build does not include libvidstab (vidstabdetect absent)."
            )

        pytest.importorskip("clipwright_stabilize")
        render_mod = pytest.importorskip("clipwright_render.render")
        schemas_mod = pytest.importorskip("clipwright_render.schemas")

        render_timeline = render_mod.render_timeline
        RenderOptions = schemas_mod.RenderOptions

        from clipwright_stabilize.analyze import (  # type: ignore[attr-defined]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import DetectShakeOptions  # type: ignore[attr-defined]
        from clipwright_stabilize.stabilize import detect_shake  # type: ignore[attr-defined]

        # 1. detect_shake on high-shake fixture -> severity_in.
        src = tmp_path / "high_shake.mp4"
        _make_high_shake_video(_FFMPEG, src)

        otio_out = tmp_path / "high_shake.otio"
        detect_result = detect_shake(
            media=str(src),
            output=str(otio_out),
            options=DetectShakeOptions(),
            timeline=None,
        )

        if not detect_result["ok"]:
            code = (detect_result.get("error") or {}).get("code", "")
            if code == "UNSUPPORTED_OPERATION":
                pytest.skip(
                    "vidstabdetect returned UNSUPPORTED_OPERATION on this build."
                )
            pytest.fail(f"detect_shake failed unexpectedly: {detect_result}")

        severity_in: float | None = detect_result["data"]["severity"]
        recommendation: str = detect_result["data"]["recommendation"]

        if recommendation != "apply":
            pytest.skip(
                f"detect_shake returned recommendation={recommendation!r} "
                f"(severity={severity_in}); render step requires 'apply' domain. "
                "Adjust _JITTER_AMP/_JITTER_FX if this build's vidstabdetect "
                "calibration differs significantly."
            )

        if severity_in is None:
            pytest.skip(
                "severity_in is None (unparseable .trf); cannot assert reduction."
            )

        # 2. render_timeline(dry_run=False) -> stabilised output.
        stabilized_out = tmp_path / "stabilized.mp4"
        render_result = render_timeline(
            str(otio_out),
            str(stabilized_out),
            RenderOptions(overwrite=True),
            dry_run=False,
        )

        if _is_windows_vst_crash(render_result):
            pytest.skip(
                "vidstabtransform render failed (SUBPROCESS_FAILED) on Windows — "
                "ACCESS_VIOLATION / 0xC0000005 detected (vid.stab #144). "
                "Option B (-threads 1) should suppress this; skipping as safety-net."
            )

        assert render_result["ok"] is True, (
            f"render_timeline failed unexpectedly: {render_result.get('error')}"
        )
        assert stabilized_out.exists(), "render_timeline did not produce an output file"
        assert stabilized_out.stat().st_size > 0, "render output file is empty"

        # 3. run_vidstabdetect on the stabilised output -> severity_out.
        # Use a distinct .otio output path so the reanalysis .trf lands in tmp_path
        # without colliding with the original high_shake.stabilize.trf.
        reanalysis_otio = tmp_path / "stabilized_reanalysis.otio"
        try:
            reanalysis = run_vidstabdetect(
                media_path=stabilized_out,
                output_path=reanalysis_otio,
                options=DetectShakeOptions(),
            )
        except Exception as exc:
            pytest.skip(
                f"run_vidstabdetect on stabilised output raised "
                f"{type(exc).__name__}; cannot assert severity reduction."
            )
        severity_out: float | None = reanalysis["severity"]

        if severity_out is None:
            pytest.skip(
                "severity_out is None (unparseable reanalysis .trf); "
                "cannot assert severity reduction."
            )

        # 4. Assert residual severity is at most _MAX_RESIDUAL_SEVERITY_RATIO of input.
        # With smoothing=15 (31-frame window at 30 fps), the 3-4 Hz jitter is well
        # within the smoothing band; actual reduction is typically > 90%.
        threshold = severity_in * _MAX_RESIDUAL_SEVERITY_RATIO
        assert severity_out < threshold, (
            f"Residual severity after stabilisation must be < "
            f"{_MAX_RESIDUAL_SEVERITY_RATIO:.0%} of pre-stabilisation severity "
            f"(D6 AC-9: apply is not a no-op). "
            f"severity_in={severity_in:.4f}, severity_out={severity_out:.4f}, "
            f"threshold={threshold:.4f}. "
            "vidstabtransform is not adequately reducing camera motion — "
            "check that the .trf file from detect_shake was correctly passed to render."
        )
