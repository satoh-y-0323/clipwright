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

Findings (2026-06-25, ffmpeg 8.1.1-full_build):
    - ffmpeg av_expr_parse has a HARD LIMIT of 96 additive terms per expression.
    - 96 terms (uniform dt): OK (x_expr ~3233 bytes)
    - 97 terms (uniform dt): FAIL ("Failed to configure input pad on Parsed_crop")
    - N_max=120 yields up to 116 non-zero terms (sine-wave control points) → EXCEEDS LIMIT.
    - RECOMMENDED N_max: 80 (worst-case 80 terms = 80 control points all changing,
      comfortably below 96; with typical dx==0 pruning the effective term count is lower).
    - x_expr and y_expr are parsed independently, so the 96-term limit applies per axis.
    - The ``format=yuv420p`` pre-filter is NOT the root cause; the limit is
      av_expr_parse recursion depth for left-associative addition parsing.

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

# av_expr_parse hard limit discovered by this spike
_FFMPEG_EXPR_TERM_LIMIT = 96  # additive terms per x or y expression


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
def test_spike_expr_len_term_limit_97_fails(ffmpeg_bin: str, tmp_path: Path) -> None:
    """97 uniform additive terms must fail with av_expr_parse error.

    This documents the hard limit: 97 terms exceed av_expr_parse recursion depth.
    If this test starts passing, the ffmpeg version has lifted the limit and
    N_max may be revisited upward.
    """
    xe = _make_uniform_expr(97)
    ye = _make_uniform_expr(97)

    r = _run_ffmpeg_crop(ffmpeg_bin, xe, ye, tmp_path / "limit_97.mp4")
    assert r.returncode != 0, (
        "ffmpeg UNEXPECTEDLY succeeded with 97-term expression. "
        "The av_expr_parse limit may have been raised in this ffmpeg version. "
        "Consider revising N_max upward and re-running the spike."
    )
    # Confirm the failure is a crop filter parse error, not something else
    assert "crop" in r.stderr.lower() or "pad" in r.stderr.lower(), (
        f"Unexpected failure mode (no 'crop' in stderr):\n{r.stderr[:500]}"
    )


@pytest.mark.integration
def test_spike_expr_len_n120_fails(ffmpeg_bin: str, tmp_path: Path) -> None:
    """N_max=120 sine-wave control points produce expressions that exceed the limit.

    This test documents why N_max must be reduced from 120 to at most 80.
    The 120-point sine wave produces ~116 non-zero terms (dx!=0 pruning leaves few zeros),
    which exceeds the 96-term limit.
    """
    xe, term_count = _make_sine_expr(120)
    print(
        f"\n[spike] N=120 sine: {term_count} effective terms, {len(xe)} bytes",
        file=sys.stderr,
    )

    r = _run_ffmpeg_crop(ffmpeg_bin, xe, "0", tmp_path / "n120_sine.mp4")
    assert r.returncode != 0, (
        f"ffmpeg UNEXPECTEDLY succeeded with N=120 ({term_count} terms). "
        "Re-check the effective term count — dx==0 pruning may have reduced it enough."
    )
    print(
        f"[spike] N=120 FAILED as expected (term_count={term_count})", file=sys.stderr
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
