"""analyze.py — ffmpeg vidstabdetect execution for clipwright-stabilize.

Runs ffmpeg with the vidstabdetect filter to generate a .trf transform file,
then estimates shake severity from the binary TRF1 file contents.

Key design decisions:
- cwd + relative basename is the only Windows-safe approach for vid.stab
  result= / input= paths (P-2/P-3: Windows absolute paths are not escaped by
  the filtergraph parser used by libvidstab).
- _TIMEOUT_SECONDS is pinned at 300.0 for the initial release (F-5).
- _estimate_severity is heuristic/best-effort; parse failure returns None (F-2/F-3).
"""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path
from typing import Any

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run

from clipwright_stabilize.schemas import DetectShakeOptions

# ffmpeg execution timeout (seconds). vidstabdetect scans every frame so
# it takes longer than a measurement-only pass. Pinned for initial release (F-5).
_TIMEOUT_SECONDS: float = 300.0

# TRF1 binary file magic header.
_TRF_MAGIC = b"TRF1"

# Normalisation constant for severity estimation (heuristic / pinned — F-2/F-3).
# Mean absolute pixel displacement at or above this value maps to severity=1.0.
# Chosen based on typical camera shake magnitudes; adjust after e2e calibration.
_NORM_PX: float = 30.0

# Regex for detecting libvidstab filter absence in error messages (P-4 / §4-B).
_UNSUPPORTED_RE = re.compile(r"Unknown filter|No such filter", re.IGNORECASE)


def _estimate_severity(trf_path: Path) -> float | None:
    """Best-effort severity in 0.0-1.0 from a binary TRF1 file.

    The .trf body contains a TRF1 magic followed by IEEE-754 doubles describing
    per-frame transforms (translation x/y, rotation, zoom). We scan all
    little-endian doubles, take the mean absolute value of finite doubles,
    and normalise by _NORM_PX (heuristic / pinned constant — F-2/F-3).
    Any parse anomaly (magic mismatch, empty body, all non-finite, struct error,
    OSError) returns None (render does not use severity).

    Args:
        trf_path: Path to the .trf binary file.

    Returns:
        Severity in [0.0, 1.0], or None when the file cannot be parsed.
    """
    try:
        blob = trf_path.read_bytes()
    except OSError:
        return None

    if not blob.startswith(_TRF_MAGIC):
        return None

    body = blob[len(_TRF_MAGIC) :]
    n = len(body) // 8
    if n == 0:
        return None

    try:
        unpacked = struct.unpack_from(f"<{n}d", body, 0)
    except struct.error:
        return None

    doubles: list[float] = [float(v) for v in unpacked]
    finite = [abs(d) for d in doubles if math.isfinite(d)]
    if not finite:
        return None

    mean_abs = sum(finite) / len(finite)
    # Normalise mean pixel displacement to 0..1 (_NORM_PX is a pinned heuristic
    # constant; see ADR-ST-3). Values above _NORM_PX clamp to 1.0.
    severity = mean_abs / _NORM_PX
    if not math.isfinite(severity):
        return None

    return max(0.0, min(1.0, severity))


def run_vidstabdetect(
    media_path: Path,
    output_path: Path,
    options: DetectShakeOptions,
) -> dict[str, Any]:
    """Run ffmpeg vidstabdetect to generate a .trf file and estimate severity.

    Uses cwd + relative trf basename (P-2/P-3): Windows absolute paths cannot be
    safely used in vidstab filtergraph result= / input= parameters.

    Args:
        media_path: Input video file (absolute path).
        output_path: Output .otio path; trf is written to output_path.parent.
        options: DetectShakeOptions with shakiness / accuracy / smoothing.

    Returns:
        {
            "trf_path": str,           # absolute path of the generated .trf file
            "severity": float | None,  # 0.0-1.0 best-effort, None when unparseable
            "warnings": list[str],
        }

    Raises:
        ClipwrightError: DEPENDENCY_MISSING / UNSUPPORTED_OPERATION /
            SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT (sanitised messages, CWE-209).
    """
    ffmpeg_bin = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    trf_dir = output_path.parent
    trf_name = f"{media_path.stem}.stabilize.trf"  # relative basename (cwd-based)
    trf_abs = trf_dir / trf_name

    # -vf is a single argv element (CWE-78).
    # shakiness / accuracy are validated int values from Pydantic — no injection risk.
    vf = (
        f"vidstabdetect=result={trf_name}"
        f":shakiness={options.shakiness}"
        f":accuracy={options.accuracy}"
    )
    cmd: list[str] = [
        ffmpeg_bin,
        "-hide_banner",
        "-i",
        str(media_path.resolve()),  # absolute input path (cwd-independent)
        "-vf",
        vf,
        "-f",
        "null",
        "-",
    ]

    try:
        run(cmd, timeout=_TIMEOUT_SECONDS, cwd=str(trf_dir))
    except ClipwrightError as exc:
        # libvidstab not compiled into this ffmpeg build — stderr contains
        # "Unknown filter" or "No such filter" (P-4 / §4-B).
        if exc.code == ErrorCode.SUBPROCESS_FAILED and _UNSUPPORTED_RE.search(
            exc.message
        ):
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message=(
                    "This ffmpeg build does not support the vidstabdetect filter."
                ),
                hint=(
                    "Install an ffmpeg build compiled with libvidstab "
                    "(--enable-libvidstab), then retry."
                ),
            ) from None  # CWE-209: cut __cause__ to avoid leaking abs paths / stderr

        # All other failures (SUBPROCESS_FAILED without filter keyword,
        # SUBPROCESS_TIMEOUT, DEPENDENCY_MISSING) — sanitise and re-raise.
        raise ClipwrightError(
            code=exc.code,
            message="ffmpeg vidstabdetect command failed.",
            hint="Check the ffmpeg version and that libvidstab is enabled.",
        ) from None  # CWE-209: cut __cause__

    # Defensive check: rc=0 but .trf was not generated (frames parity — §4-D).
    if not trf_abs.exists():
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message="ffmpeg succeeded but the .trf output file was not generated.",
            hint=(
                "Check that libvidstab is enabled and the output directory is writable."
            ),
        )

    warnings: list[str] = []
    severity = _estimate_severity(trf_abs)
    if severity is None:
        warnings.append(
            "Could not estimate shake severity from the .trf file;"
            " severity recorded as null."
        )

    return {
        "trf_path": str(trf_abs.resolve()),
        "severity": severity,
        "warnings": warnings,
    }
