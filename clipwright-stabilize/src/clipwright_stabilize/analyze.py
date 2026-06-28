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
import statistics
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

# Maximum .trf file size accepted by _estimate_severity (OOM DoS guard — SR-MEM-001).
# Files larger than this are treated as unparseable (best-effort, return None).
_TRF_MAX_BYTES: int = 100 * 1024 * 1024  # 100 MB

# Sanitise filtergraph-unsafe characters from a trf stem (SR-INJ-002).
# vid.stab result=/input= cannot be safely escaped with backslash sequences;
# instead we replace any character that is not alphanumeric, '-', or '_' with '_'.
# An empty result falls back to "media" to guarantee a non-empty basename.
_TRF_STEM_SANITIZE_RE = re.compile(r"[^A-Za-z0-9\-_]")


def _estimate_severity(trf_path: Path) -> float | None:
    """Best-effort severity in 0.0-1.0 from a binary TRF1 file.

    Parses the TRF1 binary format produced by ffmpeg vidstabdetect (ADR-D3-1).
    Layout (empirically verified against real vidstabdetect output):

    - Global header: TRF1 magic (4 B) + 3×int32 (12 B) + double (8 B) = 24 B
    - Per-frame records: frame_num (int32) + count (int32) + count × LocalMotion
    - LocalMotion (26 B, packed — no alignment padding):
        int16 vx, vy         — Euclidean displacement components in pixels
        int16 fx, fy, fsize  — measurement field position/size (unused here)
        double contrast, double match  — quality metrics (unused here)

    Severity = statistics.fmean of per-frame mean Euclidean displacement,
    normalised by _NORM_PX and clamped to [0.0, 1.0] (ADR-D3-2 / FR-2).

    Replaces the old flat-double scan that misread int32 header/field bytes as
    IEEE-754 doubles (~1e308 each), causing sum() overflow to inf → None.

    Any parse error or arithmetic anomaly returns None (best-effort, F-2/F-3).

    Args:
        trf_path: Path to the .trf binary file produced by vidstabdetect.

    Returns:
        Severity in [0.0, 1.0], or None when the file cannot be parsed.
    """
    try:
        blob = trf_path.read_bytes()
    except OSError:
        return None

    if len(blob) > _TRF_MAX_BYTES:
        return None  # best-effort; oversized .trf treated as unparseable (SR-MEM-001)

    if not blob.startswith(_TRF_MAGIC):
        return None

    # TRF1 header = 24 bytes (magic + 3×int32 + double).
    # Per-frame prefix = 8 bytes (frame_num + count, each int32).
    # LocalMotion = 26 bytes (5×int16 + 2×double, packed).
    _HDR = 24
    _PFX_FMT = "<2i"  # frame_num, count_of_lms
    _PFX_SZ = 8
    _VEC_FMT = "<2h"  # vx, vy — first 4 bytes of each LocalMotion
    _LM_SZ = 26

    if len(blob) < _HDR:
        return None

    try:
        pos = _HDR
        frame_disps: list[float] = []

        while pos + _PFX_SZ <= len(blob):
            _, count = struct.unpack_from(_PFX_FMT, blob, pos)
            pos += _PFX_SZ

            if count < 0:
                return None  # corrupt: negative LM count

            lm_disps: list[float] = []
            for _ in range(count):
                if pos + _LM_SZ > len(blob):
                    break  # truncated frame — accept partial
                vx, vy = struct.unpack_from(_VEC_FMT, blob, pos)
                lm_disps.append(math.hypot(vx, vy))
                pos += _LM_SZ

            if lm_disps:
                frame_disps.append(statistics.fmean(lm_disps))

        if not frame_disps:
            return None

        mean_disp = statistics.fmean(frame_disps)
        # Normalise to [0, 1]; values at or above _NORM_PX pixels clamp to 1.0.
        severity = mean_disp / _NORM_PX
        if not math.isfinite(severity):
            return None
        return max(0.0, min(1.0, severity))

    except Exception:  # broad catch is intentional: corrupt TRF1 must not propagate
        # struct.error, OverflowError, MemoryError, or arithmetic failures on
        # adversarial input all return None (best-effort severity, F-2/F-3).
        return None


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
    # Sanitise stem: replace filtergraph-unsafe chars with '_' (SR-INJ-002).
    # cwd+relative basename approach (ADR-ST-1/P-2/P-3) is preserved.
    sanitized_stem = _TRF_STEM_SANITIZE_RE.sub("_", media_path.stem) or "media"
    trf_name = f"{sanitized_stem}.stabilize.trf"  # relative basename (cwd-based)
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
