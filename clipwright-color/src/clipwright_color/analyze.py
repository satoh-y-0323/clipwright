"""analyze.py — ffmpeg signalstats measurement for clipwright-color.

Actual ffmpeg 8.1.1 Windows output format (verified in e2e env):
  metadata=print emits per sampled frame on stderr with
  [Parsed_metadata_N @ addr] prefix:
    [Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMIN=9
    [Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YAVG=125.951
    [Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMAX=242

The parser (_parse_signalstats) accepts a single text blob so the source stream
(stderr vs stdout) is a one-line switch (ADR-CO-6). Default: result.stderr.
"""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median
from typing import Any

from clipwright.errors import ClipwrightError
from clipwright.process import resolve_tool, run
from pydantic import ValidationError

from clipwright_color.schemas import BrightnessMeasured, DetectColorOptions

# ffmpeg execution timeout (seconds).
# Measurement-only pass; same budget as loudness.
_TIMEOUT_SECONDS: float = 300.0

# Regex patterns for signalstats per-frame output lines (ADR-CO-6 / §4.2).
# The [Parsed_metadata_N @ addr] prefix is ignored; we match the key=value portion.
_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=(-?\d+\.?\d*)")
_YMIN_RE = re.compile(r"lavfi\.signalstats\.YMIN=(-?\d+\.?\d*)")
_YMAX_RE = re.compile(r"lavfi\.signalstats\.YMAX=(-?\d+\.?\d*)")
# ADR-CO-9 / FR-2: chroma-cast channels; same single signalstats pass as YAVG.
_UAVG_RE = re.compile(r"lavfi\.signalstats\.UAVG=(-?\d+\.?\d*)")
_VAVG_RE = re.compile(r"lavfi\.signalstats\.VAVG=(-?\d+\.?\d*)")


def _parse_signalstats(text: str) -> dict[str, Any] | None:
    """Parse signalstats metadata output and return aggregated measurements.

    Extracts YAVG (mean across frames), YMIN (min across frames), YMAX (max across
    frames), and sampled_frames count. Returns None when no YAVG lines are found
    or when BrightnessMeasured validation fails (inf/nan/out-of-range -> None, U-1).

    Args:
        text: Single text blob from ffmpeg signalstats output (stderr or stdout).

    Returns:
        Dict with yavg/ymin/ymax/sampled_frames, or None on failure.
    """
    yavg_vals = [float(m) for m in _YAVG_RE.findall(text)]
    if not yavg_vals:
        return None

    yavg = sum(yavg_vals) / len(yavg_vals)
    ymin_vals = [float(m) for m in _YMIN_RE.findall(text)]
    ymax_vals = [float(m) for m in _YMAX_RE.findall(text)]
    # ADR-CO-9 / FR-2: chroma uses median (outlier robustness).
    # Only populate when lines present; absent => None (FR-4 finer-grained degradation).
    uavg_vals = [float(m) for m in _UAVG_RE.findall(text)]
    vavg_vals = [float(m) for m in _VAVG_RE.findall(text)]

    raw: dict[str, Any] = {
        "yavg": yavg,
        "ymin": min(ymin_vals) if ymin_vals else None,
        "ymax": max(ymax_vals) if ymax_vals else None,
        "sampled_frames": len(yavg_vals),
        "uavg": median(uavg_vals) if uavg_vals else None,
        "vavg": median(vavg_vals) if vavg_vals else None,
    }

    try:
        validated = BrightnessMeasured(**raw)
    except ValidationError:
        # inf/nan or out-of-range -> degrade to None (parity with U-1)
        return None

    return validated.model_dump()


def measure_brightness(
    media: Path,
    options: DetectColorOptions,
) -> dict[str, Any]:
    """Measure video brightness using ffmpeg signalstats (architecture-report §4).

    Runs ffmpeg with signalstats=stat=brng,metadata=print to extract per-frame
    luma statistics. Parses YAVG (mean), YMIN (min), YMAX (max) from stderr.

    Args:
        media: Path to the input media file (video stream required).
        options: DetectColorOptions with sample_interval_sec and target_luma.

    Returns:
        {
            "measured": dict | None,  # BrightnessMeasured fields, or None (U-1).
            "warnings": list[str],
        }

    Raises:
        clipwright.errors.ClipwrightError:
            DEPENDENCY_MISSING / SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT.
    """
    ffmpeg_bin = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    interval = options.sample_interval_sec
    # -vf is a single argv element (CWE-78 / NFR-2 / ADR-CO-6).
    # interval is a validated Pydantic float (gt=0) so {interval:g} cannot inject
    # filtergraph syntax.
    vf = f"fps=1/{interval:g},signalstats=stat=brng,metadata=print"

    cmd: list[str] = [
        ffmpeg_bin,
        "-hide_banner",
        "-i",
        str(media),
        "-vf",
        vf,
        "-f",
        "null",
        "-",
    ]

    warnings: list[str] = []

    try:
        result = run(cmd, timeout=_TIMEOUT_SECONDS)
    except ClipwrightError as exc:
        # Prevent absolute paths / raw stderr from leaking into the error message.
        # Re-raise with fixed wording + from None to avoid chaining __cause__ (CWE-209).
        raise ClipwrightError(
            code=exc.code,
            message="ffmpeg signalstats command failed.",
            hint="Check the ffmpeg version and arguments.",
        ) from None

    # Default: read from result.stderr (ADR-CO-6; parser accepts single text blob).
    measured = _parse_signalstats(result.stderr)

    if measured is None:
        warnings.append(
            "Could not retrieve signalstats YAVG values."
            " color directive will not be written (U-1)."
            " ffmpeg stderr did not contain lavfi.signalstats.YAVG fields."
        )

    return {"measured": measured, "warnings": warnings}
