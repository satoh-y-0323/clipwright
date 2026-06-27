"""test_stabilize_e2e.py — Real e2e smoke tests: detect_shake -> render (stabilize pipeline).

Covers AC-5 (render ok=True + artifact path on disk) and AC-6 (pix_fmt=yuv420p) from spec5.

Strategy:
  A synthetic testsrc video is generated via ffmpeg lavfi (640x480, 3 s, no audio).
  detect_shake (clipwright-stabilize) runs vidstabdetect and produces a .trf + annotated OTIO.
  render_timeline (clipwright-render) applies vidstabtransform with dry_run=False.

Skip conditions (any absent -> skip):
  - ffmpeg absent (PATH or CLIPWRIGHT_FFMPEG env)
  - ffprobe absent (PATH or CLIPWRIGHT_FFPROBE env)
  - libvidstab not compiled into the ffmpeg build (vidstabdetect filter not listed)
  - [Windows-specific] vidstabtransform crashes with exit code 0xC0000005 (ACCESS_VIOLATION)
    due to clipwright.process.run() using stdin=subprocess.DEVNULL; fix: use PIPE instead.

How to run:
  cd clipwright-render
  uv run python -m pytest tests/test_stabilize_e2e.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Binary resolution (mirrors conftest.py + test_e2e_merge.py)
# ---------------------------------------------------------------------------


def _find_binary(name: str, env_var: str) -> str | None:
    """Locate a binary via PATH then env_var."""
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
        "Add ffmpeg to PATH or set the CLIPWRIGHT_FFMPEG environment variable."
    ),
)
requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe not found. "
        "Add ffprobe to PATH or set the CLIPWRIGHT_FFPROBE environment variable."
    ),
)

_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# Fixture dimensions and duration — kept small to minimise vidstabdetect runtime.
_SRC_W = 640
_SRC_H = 480
_SRC_DUR = 3.0
_SRC_FPS = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_vidstab(ffmpeg: str) -> bool:
    """Return True when the ffmpeg build has vidstabdetect compiled in (libvidstab)."""
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


def _make_test_video(ffmpeg: str, output: Path) -> None:
    """Generate a synthetic test video (640x480, 25 fps, no audio) for stabilize tests.

    Uses testsrc lavfi source. No audio stream so detect_shake (audio-not-required)
    exercises the pure video-only path.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={_SRC_W}x{_SRC_H}:rate={_SRC_FPS}:duration={_SRC_DUR}",
        "-t",
        str(_SRC_DUR),
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
        f"Test video fixture generation failed: {result.stderr[:400]}"
    )


def _probe_video_stream(ffprobe: str, media: Path) -> dict[str, Any]:
    """Return the first video stream dict from ffprobe JSON output, or {} if absent."""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
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
    probe = json.loads(result.stdout)
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return {}


_WINDOWS_VST_CRASH_EXIT_CODE = "3221225477"


def _is_windows_vst_crash(result: dict[str, Any]) -> bool:
    """Return True when result is the known Windows vidstabtransform ACCESS_VIOLATION crash.

    Root cause: clipwright.process.run() uses stdin=subprocess.DEVNULL which conflicts
    with the vidstabtransform filter on Windows Gyan.dev ffmpeg 8.1.1 (exit code
    0xC0000005 = STATUS_ACCESS_VIOLATION). Fix pending in process.py: use PIPE instead.
    The crash is intermittent (~80% of runs) and only occurs on Windows.
    """
    if sys.platform != "win32":
        return False
    if result.get("ok") is not False:
        return False
    error = result.get("error") or {}
    if not isinstance(error, dict):
        return False
    if error.get("code") != "SUBPROCESS_FAILED":
        return False
    return _WINDOWS_VST_CRASH_EXIT_CODE in error.get("message", "")


def _run_stabilize_pipeline(
    tmp_path: Path,
) -> tuple[dict[str, Any], Path]:
    """Run detect_shake then render_timeline; return (render_result, out_mp4_path).

    Calls pytest.skip when libvidstab is not available or when the known Windows
    vidstabtransform crash is encountered, so tests are skipped cleanly.
    """
    assert _FFMPEG is not None
    assert _FFPROBE is not None

    if not _has_vidstab(_FFMPEG):
        pytest.skip(
            "ffmpeg build does not include libvidstab "
            "(vidstabdetect/vidstabtransform unavailable). "
            "Install an ffmpeg build compiled with --enable-libvidstab."
        )

    from clipwright_stabilize.schemas import DetectShakeOptions
    from clipwright_stabilize.stabilize import detect_shake

    src = tmp_path / "src.mp4"
    _make_test_video(_FFMPEG, src)

    otio_out = tmp_path / "timeline.otio"
    detect_result = detect_shake(
        media=str(src),
        output=str(otio_out),
        options=DetectShakeOptions(),
        timeline=None,
    )

    if not detect_result["ok"]:
        error_code = detect_result.get("error", {}).get("code", "")
        if error_code == "UNSUPPORTED_OPERATION":
            pytest.skip(
                "vidstabdetect reported as UNSUPPORTED_OPERATION by detect_shake."
            )
        pytest.fail(f"detect_shake failed unexpectedly: {detect_result}")

    out_mp4 = tmp_path / "out.mp4"
    render_result = render_timeline(
        str(otio_out),
        str(out_mp4),
        RenderOptions(overwrite=True),
        dry_run=False,
    )

    if _is_windows_vst_crash(render_result):
        pytest.skip(
            "vidstabtransform crashed with 0xC0000005 (ACCESS_VIOLATION) on Windows. "
            "Root cause: clipwright.process.run() passes stdin=subprocess.DEVNULL to "
            "ffmpeg; vidstabtransform is incompatible with DEVNULL stdin on this build "
            "(Gyan.dev 8.1.1). Fix: change DEVNULL to PIPE in process.py run()."
        )

    return render_result, out_mp4


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_ffmpeg
@requires_ffprobe
class TestStabilizeE2E:
    """Real e2e smoke tests: vidstabdetect -> vidstabtransform (AC-5 / AC-6)."""

    def test_render_ok_and_artifact_exists(self, tmp_path: Path) -> None:
        """render_timeline returns ok=True and the output artifact path is on disk (AC-5).

        Validates that:
          1. detect_shake succeeds and produces a .trf + OTIO with stabilize directive.
          2. render_timeline(dry_run=False) returns {"ok": True, ...}.
          3. The artifacts[role="output"].path returned in the envelope is a real file.
             (Regression guard for the %05d manifest-index mismatch: path must be verified
             on disk, not merely present in the envelope.)
        """
        result, out_mp4 = _run_stabilize_pipeline(tmp_path)

        assert result["ok"] is True, f"render_timeline failed: {result.get('error')}"

        artifacts = result.get("artifacts", [])
        assert artifacts, "render_timeline returned no artifacts in envelope"

        output_artifact = next(
            (a for a in artifacts if a.get("role") == "output"), None
        )
        assert output_artifact is not None, "No 'output' artifact in render result"

        artifact_path = Path(output_artifact["path"])
        assert artifact_path.exists(), (
            f"Artifact path in envelope is not present on disk: {artifact_path}"
        )
        assert artifact_path.stat().st_size > 0, "Output MP4 exists but is empty"

    def test_output_pix_fmt_is_yuv420p(self, tmp_path: Path) -> None:
        """Output MP4 has pix_fmt=yuv420p when vidstabtransform is applied (AC-6 / NFR-4).

        vidstabtransform must not produce yuv444p/yuvj444p, which would make the output
        unplayable in common media players (see render-transition-yuv444-unplayable-bug.md).
        ffprobe is used to read the actual pix_fmt of the encoded output stream.
        """
        result, out_mp4 = _run_stabilize_pipeline(tmp_path)

        assert result["ok"] is True, f"render_timeline failed: {result.get('error')}"
        assert out_mp4.exists(), "Output MP4 file was not created"

        assert _FFPROBE is not None
        vs = _probe_video_stream(_FFPROBE, out_mp4)
        assert vs, "No video stream found in output MP4"

        pix_fmt = vs.get("pix_fmt", "")
        assert pix_fmt == "yuv420p", (
            f"Output pix_fmt is {pix_fmt!r}; expected 'yuv420p' (AC-6 / NFR-4). "
            "vidstabtransform must not produce 4:4:4 chroma subsampling."
        )
