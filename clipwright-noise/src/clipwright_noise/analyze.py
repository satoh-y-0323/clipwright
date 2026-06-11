"""analyze.py — Noise floor measurement and parameter calculation via ffmpeg astats.

Design reference: §2.3.

Measures audio RMS/Noise_floor using the astats filter,
then calculates denoise parameters per backend.
Falls back to nf=-50.0 when measurement is unavailable,
returning a warning (design B-6).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from clipwright.process import resolve_tool, run

# strength → afftdn nr (dB) mapping (design §2.1 fixed values)
_STRENGTH_TO_NR: dict[str, float] = {
    "light": 6.0,
    "medium": 12.0,
    "strong": 24.0,
}

# nf fallback value (when astats measurement is unavailable; design B-6)
_NF_FALLBACK: float = -50.0

# nf clamp range (aligned with AfftdnParams constraints)
_NF_MIN: float = -80.0
_NF_MAX: float = -20.0

# astats execution timeout (seconds)
_TIMEOUT_SECONDS: float = 60.0


def _parse_noise_floor(stderr: str) -> float | None:
    """Extract the noise floor value (dB) from astats stderr.

    Priority:
    1. `Noise floor dB:` field (actual ffmpeg astats output format)
    2. `RMS level dB:` field (fallback)

    Returns None if extraction fails.
    """
    # Prefer Noise floor dB (actual ffmpeg astats output format: "Noise floor dB: -X.X")
    m = re.search(r"Noise floor dB:\s*(-?\d+\.?\d*)", stderr)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # Fall back to RMS level dB (actual ffmpeg astats output: "RMS level dB: -X.X")
    m = re.search(r"RMS level dB:\s*(-?\d+\.?\d*)", stderr)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to the range [lo, hi]."""
    return max(lo, min(hi, value))


def measure_noise(
    media_path: Path,
    strength: str,
    backend: str,
) -> dict[str, Any]:
    """Analyze media audio with astats and return denoise parameters per backend.

    Args:
        media_path: Path to the input media file (video + audio).
        strength: DetectNoiseOptions.strength ("light"/"medium"/"strong").
        backend: "afftdn" or "deepfilternet".

    Returns:
        {
            "params": dict (AfftdnParams-equivalent or {}),
            "measured_noise_floor_db": float | None,
            "warnings": list[str],
        }

    Raises:
        clipwright.errors.ClipwrightError: DEPENDENCY_MISSING / SUBPROCESS_FAILED /
            SUBPROCESS_TIMEOUT.
    """
    # Resolve the ffmpeg binary (B-1: PATH-independent via resolve_tool)
    ffmpeg_bin = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    # Measure noise floor for the entire duration with astats
    # (metadata=1:reset=0 for global stats)
    cmd = [
        ffmpeg_bin,
        "-i",
        str(media_path),
        "-af",
        "astats=metadata=1:reset=0",
        "-f",
        "null",
        "-",
    ]

    warnings: list[str] = []

    # run raises ClipwrightError (SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT, etc.),
    # which propagates directly to the caller.
    result = run(
        cmd,
        timeout=_TIMEOUT_SECONDS,
    )

    # astats statistics are written to stderr (run returns CompletedProcess)
    stderr_text = result.stderr

    measured = _parse_noise_floor(stderr_text)

    if backend == "afftdn":
        nr = _STRENGTH_TO_NR.get(strength, _STRENGTH_TO_NR["medium"])

        if measured is not None:
            nf = _clamp(measured, _NF_MIN, _NF_MAX)
        else:
            nf = _NF_FALLBACK
            warnings.append(
                f"Noise floor measurement failed; using default nf={_NF_FALLBACK}."
                " The astats output did not contain"
                " Noise floor dB / RMS level dB fields."
            )

        params: dict[str, Any] = {"nr": nr, "nf": nf, "nt": "w"}
    else:
        # deepfilternet: params fixed to {} (first release; design DC-AM-002)
        if measured is None:
            warnings.append(
                "Noise floor measurement failed; measured_noise_floor_db will be None."
            )
        params = {}

    return {
        "params": params,
        "measured_noise_floor_db": measured,
        "warnings": warnings,
    }
