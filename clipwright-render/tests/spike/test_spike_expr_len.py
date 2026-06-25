"""test_spike_expr_len.py — Spike: verify ffmpeg additive-term limit for crop x/y expressions.

Purpose:
    Before wave2 (B/C) implementation, confirm how many additive terms ffmpeg's
    av_expr_parse can handle in a crop x/y expression (architecture §3.3 formula).
    N_max=120 was the original design value; this spike validates whether it is safe.

    Expression form (architecture §3.3):
        x(t) = x0 + Σ_{i≥1} (x_i − x_{i-1}) * min(max((t-t_{i-1})/dt\\,0)\\,1)

    Each term: ``{dx}*min(max((t-{t_prev})/{dt}\\,0)\\,1)``
    Commas escaped as ``\\,`` per ffmpeg filtergraph quoting rules (ADR-T6).
    Single-quoted in the crop filter: ``crop={cw}:{ch}:'{x_expr}':'{y_expr}'``

Findings (2026-06-25, ffmpeg 8.1.1-full_build on Windows / Gyan):
    - ffmpeg av_expr_parse rejected expressions above 96 additive terms on THIS build.
    - 96 terms (uniform dt): OK (x_expr ~3233 bytes)
    - 97 terms (uniform dt): FAIL ("Failed to configure input pad on Parsed_crop")
    - N_max=120 yields up to 116 non-zero terms (sine-wave control points) → EXCEEDED here.
    - x_expr and y_expr are parsed independently, so the limit applies per axis.
    - The ``format=yuv420p`` pre-filter is NOT the root cause; the limit is
      av_expr_parse recursion depth for left-associative addition parsing.

IMPORTANT — the upper limit is ffmpeg-BUILD-SPECIFIC (not a portable constant):
    Other builds accept more. CI's Ubuntu ffmpeg parses 97 and 116 terms fine, so the
    "must fail above 96" assertions are NOT portable and would red the CI. What IS
    portable — and all that production correctness depends on — is the FLOOR:
    N_max=80 (worst-case ~78–80 terms) parses successfully on every build observed
    (Windows / macOS / Ubuntu), comfortably below even the strictest limit (96). The
    "above the limit it fails" tests below therefore only assert the failure MODE when a
    build does reject the expression, and skip when a build accepts it (limit > 96).
    RECOMMENDED / shipped N_max: 80 (conservative, build-independent).

Spike report: .claude/reports/test-report-spike_exprlen.md

How to run:
    cd clipwright-render
    pytest -m integration -k spike_expr_len -v -s

Skipped when CLIPWRIGHT_FFMPEG is unset and ffmpeg is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SRC_W = 640
SRC_H = 360
CW = 202  # 9:16 crop window: floor(360 * 9/16) = 202 (even)
CH = 360
TW = 202
TH = 360

# Lowest av_expr_parse additive-term limit observed (Windows / Gyan ffmpeg 8.1.1).
# Build-specific: other builds (e.g. Ubuntu CI) accept more. Production N_max=80 stays
# below this floor on every build, so it is the portable, conservative choice.
_FFMPEG_EXPR_TERM_LIMIT_FLOOR = 96  # additive terms per x or y expression


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    env_val = os.environ.get("CLIPWRIGHT_FFMPEG")
    if env_val and Path(env_val).is_file():
        return env_val
    return None


def _format_f(v: float, precision: int = 4) -> str:
    """Format float to fixed decimal, stripping trailing zeros (mirrors §3.3 ``_f``)."""
    s = f"{v:.{precision}f}"
    return s.rstrip("0").rstrip(".") or "0"


def _make_uniform_expr(n_terms: int, x0: int = 100, dx: int = 1) -> str:
    """Build an additive expr with exactly n_terms terms, each of equal length.

    Uses fixed dt=0.02 so we can control term count independently of byte length.
    """
    parts = [str(x0)]
    for i in range(1, n_terms):
        t_prev = f"{(i - 1) * 0.02:.4f}"
        parts.append(rf"{dx}*min(max((t-{t_prev})/0.02\,0)\,1)")
    return "+".join(parts)


def _make_sine_expr(n_ctrl: int, duration: float = 2.0) -> tuple[str, int]:
    """Build x_expr from N sine-wave control points (mirrors architecture §3.3).

    Returns (expr_string, effective_term_count) where effective_term_count
    is the number of additive terms after dx==0 pruning (ADR-T7).
    """
    import math

    x_min, x_max = 0, SRC_W - CW
    x_span = x_max - x_min
    t_secs = [i * duration / max(n_ctrl - 1, 1) for i in range(n_ctrl)]
    x_pixels = [
        round(
            x_min
            + x_span * (0.5 + 0.5 * math.sin(2 * math.pi * i / max(n_ctrl - 1, 1)))
        )
        for i in range(n_ctrl)
    ]
    x_pixels = [max(x_min, min(x_max, v)) for v in x_pixels]

    parts = [str(x_pixels[0])]
    for i in range(1, n_ctrl):
        dx = x_pixels[i] - x_pixels[i - 1]
        if dx == 0:
            continue
        dt = t_secs[i] - t_secs[i - 1]
        dt_s = _format_f(dt)
        t_s = _format_f(t_secs[i - 1])
        parts.append(rf"{dx}*min(max((t-{t_s})/{dt_s}\,0)\,1)")
    return "+".join(parts), len(parts)


def _run_ffmpeg_crop(
    ffmpeg: str, xe: str, ye: str, out: Path
) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg testsrc → crop(xe, ye) → 1-frame output.

    Prepends ``format=yuv420p`` because testsrc emits rgb24 which the crop
    filter handles fine, but this matches the production filter chain where
    input is already yuv.
    """
    vf = f"format=yuv420p,crop={CW}:{CH}:'{xe}':'{ye}',scale={TW}:{TH},setsar=1"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={SRC_W}x{SRC_H}:rate=10:duration=2",
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-c:v",
        "libx264",
        "-y",
        str(out),
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ffmpeg_bin() -> str:
    """Skip module when ffmpeg is unavailable."""
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        pytest.skip(
            "ffmpeg not found. Set CLIPWRIGHT_FFMPEG env var or add ffmpeg to PATH."
        )
    return ffmpeg


@pytest.fixture(scope="module")
def ffmpeg_version(ffmpeg_bin: str) -> str:
    r = subprocess.run(
        [ffmpeg_bin, "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    return r.stdout.splitlines()[0] if r.stdout else "unknown"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_spike_expr_len_term_limit_96_ok(
    ffmpeg_bin: str, tmp_path: Path, ffmpeg_version: str
) -> None:
    """96 uniform additive terms must parse and execute successfully.

    This is the confirmed upper limit of av_expr_parse for additive crop expressions.
    Passing this test asserts that the limit is at least 96 terms.
    """
    xe = _make_uniform_expr(96)
    ye = _make_uniform_expr(96)
    print(f"\n[spike] ffmpeg: {ffmpeg_version}", file=sys.stderr)
    print(f"[spike] 96-term x_expr: {len(xe)} bytes", file=sys.stderr)

    r = _run_ffmpeg_crop(ffmpeg_bin, xe, ye, tmp_path / "limit_96.mp4")
    assert r.returncode == 0, (
        f"ffmpeg failed with 96-term expression (expected to pass).\n"
        f"returncode={r.returncode}\nstderr:\n{r.stderr[:1000]}"
    )


@pytest.mark.integration
def test_spike_expr_len_term_limit_97_failure_mode(
    ffmpeg_bin: str, tmp_path: Path
) -> None:
    """Document the failure MODE for 97 terms on builds that reject it.

    The exact term limit is ffmpeg-build-specific. On builds at the 96-term floor
    (e.g. Windows / Gyan) 97 terms are rejected; this asserts the rejection is a crop
    filter parse error (not a crash or unrelated failure). On builds that accept >96
    terms (e.g. Ubuntu CI) the call succeeds and the test skips — N_max=80 stays safe
    regardless, so a higher build limit is not a regression.
    """
    xe = _make_uniform_expr(97)
    ye = _make_uniform_expr(97)

    r = _run_ffmpeg_crop(ffmpeg_bin, xe, ye, tmp_path / "limit_97.mp4")
    if r.returncode == 0:
        pytest.skip(
            "This ffmpeg build accepts 97 additive terms (av_expr limit > 96). "
            "N_max=80 remains conservatively safe; nothing to assert."
        )
    # Build rejected it — confirm the failure is a crop filter parse error.
    assert "crop" in r.stderr.lower() or "pad" in r.stderr.lower(), (
        f"Unexpected failure mode (no 'crop' in stderr):\n{r.stderr[:500]}"
    )


@pytest.mark.integration
def test_spike_expr_len_n120_over_floor(ffmpeg_bin: str, tmp_path: Path) -> None:
    """N_max=120 sine-wave control points exceed the 96-term floor — why 120 was rejected.

    The 120-point sine wave produces ~116 non-zero terms (dx!=0 pruning leaves few zeros),
    above the 96-term floor. On builds at that floor (Windows / Gyan) this is rejected,
    which is the reason N_max was reduced from the original 120 to 80. On builds that
    accept >116 terms (e.g. Ubuntu CI) the call succeeds and the test skips — the higher
    limit is not a regression, and shipped N_max=80 stays safe on every build.
    """
    xe, term_count = _make_sine_expr(120)
    print(
        f"\n[spike] N=120 sine: {term_count} effective terms, {len(xe)} bytes",
        file=sys.stderr,
    )

    r = _run_ffmpeg_crop(ffmpeg_bin, xe, "0", tmp_path / "n120_sine.mp4")
    if r.returncode == 0:
        pytest.skip(
            f"This ffmpeg build accepts N=120 ({term_count} terms; av_expr limit > 116). "
            "N_max=80 remains conservatively safe; nothing to assert."
        )
    print(
        f"[spike] N=120 rejected as on the 96-term floor (term_count={term_count})",
        file=sys.stderr,
    )


@pytest.mark.integration
def test_spike_expr_len_n80_passes(
    ffmpeg_bin: str, tmp_path: Path, ffmpeg_version: str
) -> None:
    """N_max=80 sine-wave control points must parse and execute successfully.

    N=80 worst-case yields 80 additive terms (all dx!=0), which is safely below
    the 96-term limit. This test validates the recommended N_max=80 value.

    If this test fails, the production N_max must be reduced further.
    """
    xe, term_count = _make_sine_expr(80)
    ye, y_terms = _make_sine_expr(80)

    x_bytes = len(xe.encode("utf-8"))
    y_bytes = len(ye.encode("utf-8"))
    print(
        f"\n[spike] N=80 sine: x={term_count} terms ({x_bytes}B), y={y_terms} terms ({y_bytes}B)",
        file=sys.stderr,
    )

    r = _run_ffmpeg_crop(ffmpeg_bin, xe, ye, tmp_path / "n80_sine.mp4")
    if r.returncode != 0:
        print(f"[spike] FAIL stderr:\n{r.stderr[:500]}", file=sys.stderr)

    assert r.returncode == 0, (
        f"ffmpeg failed with N=80 ({term_count} terms, {x_bytes}B).\n"
        f"stderr:\n{r.stderr[:1000]}"
    )
    out = tmp_path / "n80_sine.mp4"
    assert out.exists() and out.stat().st_size > 0

    print(
        f"[spike] N=80 PASSED: output {out.stat().st_size} bytes, "
        f"ffmpeg={ffmpeg_version}",
        file=sys.stderr,
    )
