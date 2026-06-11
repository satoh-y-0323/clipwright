"""analyze.py — Loudness measurement using ffmpeg loudnorm/volumedetect.

Design §3.1, ADR-L1/L2.

Actual ffmpeg 8.1.1 Windows output format (ADR-L3, verified on real hardware):

  loudnorm print_format=json:
    [Parsed_loudnorm_0 @ 0x...] <- empty line
    {
    \t"input_i" : "-21.75",
    \t"input_tp" : "-18.06",
    \t"input_lra" : "0.00",
    \t"input_thresh" : "-31.75",
    \t"output_i" : "-14.03",
    ...
    \t"target_offset" : "0.03"
    }
    Note: values are output as quoted strings. "-inf" may appear (silent input).

  volumedetect:
    [Parsed_volumedetect_0 @ 0x...] max_volume: -18.1 dB
    Note: "max_volume: <VALUE> dB" format.

When measurement is not possible, returns measured=None + warning
(U-1 confirmed policy, DC-AM-003).
ClipwrightError failures are propagated as-is.
Absolute paths are never included in messages (fixed wording).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from clipwright.errors import ClipwrightError
from clipwright.process import resolve_tool, run
from pydantic import ValidationError

from clipwright_loudness.schemas import LoudnormMeasured, PeakMeasured

# ffmpeg execution timeout (seconds).
# Measurement-only passes (loudnorm 1-pass / volumedetect) complete much faster
# than re-encoding, so 300 seconds is sufficient even for long-duration media.
# Dynamic calculation is not needed.
_TIMEOUT_SECONDS: float = 300.0


def _parse_loudnorm_measured(stderr: str) -> dict[str, Any] | None:
    """Extract measured values from the loudnorm print_format=json stderr JSON block.

    ffmpeg writes a JSON block to stderr. Values are quoted strings.
    If "-inf" / "inf" is present, LoudnormMeasured's allow_inf_nan=False causes
    a ValidationError and None is returned (U-1).

    Returns:
        Dict of {input_i, input_tp, input_lra, input_thresh, target_offset},
        or None if extraction fails.
    """
    # Collect all JSON block candidates ({ ... }) from stderr.
    # re.search only matches the first occurrence, so if an earlier {} appears
    # before the loudnorm JSON block it may be missed (H-1).
    # Use re.findall to collect all candidates, then search in reverse for the
    # last block containing all required_keys.
    candidates = re.findall(r"\{[^{}]+\}", stderr, re.DOTALL)
    if not candidates:
        return None

    required_keys = [
        "input_i",
        "input_tp",
        "input_lra",
        "input_thresh",
        "target_offset",
    ]

    raw: dict[str, Any] | None = None
    for block in reversed(candidates):
        try:
            parsed: dict[str, Any] = json.loads(block)
        except json.JSONDecodeError:
            continue
        if all(k in parsed for k in required_keys):
            raw = parsed
            break

    if raw is None:
        return None

    # Convert required fields from string to float
    extracted: dict[str, Any] = {}
    for key in required_keys:
        val = raw[key]
        try:
            extracted[key] = float(val)
        except (ValueError, TypeError):
            return None

    # Validate with LoudnormMeasured (inf/nan -> ValidationError -> degrade to None)
    try:
        validated = LoudnormMeasured(**extracted)
    except ValidationError:
        return None

    return validated.model_dump()


def _parse_volumedetect_measured(stderr: str) -> dict[str, Any] | None:
    """Extract max_volume from volumedetect stderr.

    Format: "[Parsed_volumedetect_0 @ 0x...] max_volume: -X.X dB"

    Returns:
        Dict of {max_volume_db: float}, or None if extraction fails.
    """
    m = re.search(r"max_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)
    if not m:
        return None

    try:
        val = float(m.group(1))
    except ValueError:
        return None

    # Validate with PeakMeasured (out-of-range -> ValidationError -> degrade to None)
    try:
        validated = PeakMeasured(max_volume_db=val)
    except ValidationError:
        return None

    return validated.model_dump()


def measure_loudness(
    media: Path,
    *,
    mode: str,
    target_i: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
    target_peak_db: float = -1.0,
) -> dict[str, Any]:
    """Measure audio loudness of media and return the measured values (ADR-L1/L2/L7).

    Args:
        media: Path to the input media file (video + audio).
        mode: "loudnorm" or "peak".
        target_i: loudnorm integrated loudness target (LUFS).
        target_tp: loudnorm true peak target (dBTP).
        target_lra: loudnorm LRA target (LU).
        target_peak_db: peak mode peak target (dB).

    Returns:
        {
            "measured": dict | None,  # Mode-specific measured values.
                                      # None means U-1 (not measurable).
            "warnings": list[str],
        }

    Raises:
        clipwright.errors.ClipwrightError:
            DEPENDENCY_MISSING / SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT.
    """
    # Resolve ffmpeg binary (PATH-independent)
    ffmpeg_bin = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    warnings: list[str] = []

    if mode == "loudnorm":
        # Single-pass measurement with
        # loudnorm=I=<I>:TP=<TP>:LRA=<LRA>:print_format=json (ADR-L1)
        af_filter = (
            f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json"
        )
        cmd: list[str] = [
            ffmpeg_bin,
            "-i",
            str(media),
            "-af",
            af_filter,
            "-f",
            "null",
            "-",
        ]

        try:
            result = run(cmd, timeout=_TIMEOUT_SECONDS)
        except ClipwrightError as exc:
            # Prevent absolute paths from leaking into the error message from run().
            # Preserve the ErrorCode and re-raise with fixed wording (CWE-209).
            # Use "from None" to avoid chaining __cause__ (SR L-1).
            raise ClipwrightError(
                code=exc.code,
                message="ffmpeg loudnorm command failed.",
                hint="Check the ffmpeg version and arguments.",
            ) from None
        measured = _parse_loudnorm_measured(result.stderr)

        if measured is None:
            warnings.append(
                "Could not retrieve loudnorm measured values."
                " loudness directive will not be written (U-1, DC-AM-003)."
                " ffmpeg stderr did not contain a valid loudnorm JSON block."
            )

        return {"measured": measured, "warnings": warnings}

    else:
        # mode == "peak": measure max_volume with volumedetect (ADR-L2)
        cmd = [
            ffmpeg_bin,
            "-i",
            str(media),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]

        try:
            result = run(cmd, timeout=_TIMEOUT_SECONDS)
        except ClipwrightError as exc:
            # Use "from None" to avoid chaining __cause__ (SR L-1).
            raise ClipwrightError(
                code=exc.code,
                message="ffmpeg volumedetect command failed.",
                hint="Check the ffmpeg version and arguments.",
            ) from None
        measured = _parse_volumedetect_measured(result.stderr)

        if measured is None:
            warnings.append(
                "Could not retrieve volumedetect measured values."
                " loudness directive will not be written (U-1, DC-AM-003)."
                " ffmpeg stderr did not contain a max_volume field."
            )

        return {"measured": measured, "warnings": warnings}
