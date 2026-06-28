"""analyze.py — ffmpeg vidstabdetect execution for clipwright-stabilize.

Runs ffmpeg with the vidstabdetect filter to generate a .trf transform file,
then estimates shake severity from the .trf file contents.

Key design decisions:
- cwd + relative basename is the only Windows-safe approach for vid.stab
  result= / input= paths (P-2/P-3: Windows absolute paths are not escaped by
  the filtergraph parser used by libvidstab).
- _TIMEOUT_SECONDS is pinned at 300.0 for the initial release (F-5).
- _estimate_severity is heuristic/best-effort; parse failure returns None (F-2/F-3).
- _estimate_severity uses median aggregation for robustness to scene-cut outliers
  in multi-shot footage; apparent motion at cuts can reach 100+ px (F-4).
- libvidstab writes text ("VID.STAB") on Linux/most apt/brew builds and binary
  ("TRF1") on the Gyan Windows build; both carry per-LM (vx, vy) translation
  vectors. _estimate_severity dispatches on the magic header (D3-crossplat).
"""

from __future__ import annotations

import math
import re
import statistics
import struct
from pathlib import Path
from typing import Any, Literal

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run

from clipwright_stabilize.schemas import DetectShakeOptions

# ffmpeg execution timeout (seconds). vidstabdetect scans every frame so
# it takes longer than a measurement-only pass. Pinned for initial release (F-5).
_TIMEOUT_SECONDS: float = 300.0

# TRF1 binary file magic header (Gyan Windows ffmpeg builds).
_TRF_MAGIC = b"TRF1"

# VID.STAB text file magic header (Linux apt / macOS brew libvidstab builds).
_TRF_TEXT_MAGIC = b"VID.STAB"

# Normalisation constant for severity estimation (heuristic / pinned — F-2/F-3).
# Mean absolute pixel displacement at or above this value maps to severity=1.0.
# Chosen based on typical camera shake magnitudes; adjust after e2e calibration.
_NORM_PX: float = 30.0

# Regex for detecting libvidstab filter absence in error messages (P-4 / §4-B).
_UNSUPPORTED_RE = re.compile(r"Unknown filter|No such filter", re.IGNORECASE)

# Maximum .trf file size accepted by _estimate_severity (OOM DoS guard — SR-MEM-001).
# Files larger than this are treated as unparseable (best-effort, return None).
_TRF_MAX_BYTES: int = 100 * 1024 * 1024  # 100 MB

# TRF1 per-frame header and LocalMotion layout constants (empirically verified).
# Global header: TRF1 magic (4 B) + 3×int32 (12 B) + double (8 B) = 24 B total.
# Per-frame prefix: frame_num (int32) + count_of_lms (int32) = 8 B.
# LocalMotion (26 B, packed — no alignment padding):
#   int16 vx, vy (Euclidean displacement), int16 fx, fy, fsize (field position/size),
#   double contrast, double match (quality metrics).
_HDR: int = 24  # global header size in bytes
_PFX_FMT: str = "<2i"  # struct format for frame prefix (frame_num, count)
_PFX_SZ: int = 8  # frame prefix size in bytes
_VEC_FMT: str = "<2h"  # struct format for vx/vy (first 4 B of LocalMotion)
_LM_SZ: int = 26  # full LocalMotion size in bytes

# Maximum LocalMotion entries per frame accepted by _estimate_severity (SR-MEM-001).
# A real vidstabdetect output on a 4K frame with accuracy=15 produces at most a few
# thousand LMs.  Values above this limit indicate corrupt data and trigger a
# best-effort return None to avoid MemoryError from range(count) allocation.
_MAX_LM_PER_FRAME: int = (
    1_000_000  # 1 million LMs per frame far exceeds any real output
)

# Regex to extract (vx, vy) from a single LocalMotion entry in the text TRF format.
# Format per entry: (LM vx vy fx fy size contrast match)
# Only the first two integers (vx, vy) are needed for displacement estimation.
_LM_TEXT_RE = re.compile(r"\(LM\s+(-?\d+)\s+(-?\d+)\s")

# Sanitise filtergraph-unsafe characters from a trf stem (SR-INJ-002).
# vid.stab result=/input= cannot be safely escaped with backslash sequences;
# instead we replace any character that is not alphanumeric, '-', or '_' with '_'.
# An empty result falls back to "media" to guarantee a non-empty basename.
_TRF_STEM_SANITIZE_RE = re.compile(r"[^A-Za-z0-9\-_]")

# Severity threshold above which stabilization is recommended (calibrated
# against real fixtures; advisory only — final decision belongs to the caller).
# Median per-frame displacement values against committed fixtures:
#   calm fixture  ≈ 1.70 px → severity ≈ 0.057  (below threshold → skip)
#   shaky fixture ≈ 2.89 px → severity ≈ 0.096  (above threshold → apply)
#   conservative midpoint ≈ 2.4 px → severity ≈ 0.08
_SEVERITY_APPLY_THRESHOLD: float = 0.08


def _severity_from_frame_disps(frame_disps: list[float]) -> float | None:
    """Compute final severity from per-frame median displacement values.

    Common aggregation step used by both binary and text TRF parsers (DRY).
    Severity = median(frame_disps) / _NORM_PX, clamped to [0.0, 1.0].

    Args:
        frame_disps: Per-frame median Euclidean displacement (pixels).

    Returns:
        Severity in [0.0, 1.0], or None when frame_disps is empty or result
        is non-finite.
    """
    if not frame_disps:
        return None
    median_disp = statistics.median(frame_disps)
    severity = median_disp / _NORM_PX
    if not math.isfinite(severity):
        return None
    return max(0.0, min(1.0, severity))


def _severity_from_binary_trf(blob: bytes) -> float | None:
    """Parse a TRF1 binary blob and return severity, or None on failure.

    Layout (empirically verified against real vidstabdetect output):

    - Global header: TRF1 magic (4 B) + 3×int32 (12 B) + double (8 B) = 24 B
    - Per-frame records: frame_num (int32) + count (int32) + count × LocalMotion
    - LocalMotion (26 B, packed — no alignment padding):
        int16 vx, vy         — Euclidean displacement components in pixels
        int16 fx, fy, fsize  — measurement field position/size (unused here)
        double contrast, double match  — quality metrics (unused here)

    Any parse error returns None (best-effort, F-2/F-3).
    Broad exception catch is intentional: corrupt TRF1 input must not propagate.

    Args:
        blob: Raw bytes of the TRF1 file (already size-checked, magic-confirmed).

    Returns:
        Severity in [0.0, 1.0], or None when parsing fails.
    """
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
            if count > _MAX_LM_PER_FRAME:
                return None  # best-effort OOM guard: realistic upper bound exceeded

            lm_disps: list[float] = []
            for _ in range(count):
                if pos + _LM_SZ > len(blob):
                    break  # truncated frame — accept partial
                vx, vy = struct.unpack_from(_VEC_FMT, blob, pos)
                lm_disps.append(math.hypot(vx, vy))
                pos += _LM_SZ

            if lm_disps:
                frame_disps.append(statistics.median(lm_disps))

        return _severity_from_frame_disps(frame_disps)

    except Exception:  # broad catch is intentional: corrupt TRF1 must not propagate
        # struct.error, OverflowError, MemoryError, or arithmetic failures on
        # adversarial input all return None (best-effort severity, F-2/F-3).
        return None


def _severity_from_text_trf(blob: bytes) -> float | None:
    """Parse a VID.STAB text blob and return severity, or None on failure.

    libvidstab writes text ("VID.STAB") on Linux/most apt/brew builds and
    binary ("TRF1") on the Gyan Windows build; both carry per-LM (vx, vy)
    translation vectors that represent frame-to-frame Euclidean displacement.

    Text format (lines):
      VID.STAB 1
      # comment lines ...
      Frame N (List count [(LM vx vy fx fy size contrast match), ...])

    Only "Frame ..." lines are parsed.  Per-frame displacement = median(hypot(vx, vy))
    over all LM entries in that frame.  Empty frames (count=0 / no LM matches) are
    skipped.  Any frame whose LM count exceeds _MAX_LM_PER_FRAME is also skipped
    (OOM guard, SR-MEM-001).

    Any parse error or arithmetic anomaly returns None (best-effort, F-2/F-3).
    Broad exception catch is intentional: corrupt text input must not propagate.

    Args:
        blob: Raw bytes of the VID.STAB text file (already size-checked,
              magic-confirmed).

    Returns:
        Severity in [0.0, 1.0], or None when parsing fails.
    """
    try:
        text = blob.decode("utf-8", errors="replace")
        frame_disps: list[float] = []

        for line in text.splitlines():
            if not line.startswith("Frame "):
                continue

            # Use finditer instead of findall to bound allocation even for adversarial
            # single-line blobs: stop collecting after _MAX_LM_PER_FRAME matches so
            # that the list is at most _MAX_LM_PER_FRAME+1 entries long (SR-MEM-001).
            lm_matches: list[tuple[str, str]] = []
            for m in _LM_TEXT_RE.finditer(line):
                lm_matches.append((m.group(1), m.group(2)))
                if len(lm_matches) > _MAX_LM_PER_FRAME:
                    break  # OOM guard limit reached — this frame will be skipped
            if not lm_matches:
                continue  # Frame N (List 0 []) — no LM entries, skip
            if len(lm_matches) > _MAX_LM_PER_FRAME:
                continue  # OOM guard: skip this frame (SR-MEM-001)

            lm_disps = [math.hypot(int(vx), int(vy)) for vx, vy in lm_matches]
            frame_disps.append(statistics.median(lm_disps))

        return _severity_from_frame_disps(frame_disps)

    except Exception:  # broad catch is intentional: corrupt text must not propagate
        # UnicodeDecodeError, ValueError, OverflowError, or arithmetic failures
        # on adversarial input all return None (best-effort severity, F-2/F-3).
        return None


def _estimate_severity(trf_path: Path) -> float | None:
    """Best-effort severity in 0.0-1.0 from a .trf file (binary or text format).

    Dispatches on the file magic header:
    - b"TRF1"     → binary format produced by Gyan Windows ffmpeg builds
    - b"VID.STAB" → text format produced by Linux apt / macOS brew libvidstab builds

    Both formats carry per-LM (vx, vy) translation vectors.  Severity is the
    median of per-frame median Euclidean displacement, normalised by _NORM_PX
    and clamped to [0.0, 1.0] (ADR-D3-2 / FR-2).  Median is used for robustness
    to scene-cut outliers in multi-shot footage; per-frame apparent motion at cuts
    can reach 100+ px and would dominate a mean.

    Unknown magic bytes return None without raising (forward-compat, NF-2).
    Any parse error or arithmetic anomaly also returns None (F-2/F-3).

    Args:
        trf_path: Path to the .trf file produced by vidstabdetect.

    Returns:
        Severity in [0.0, 1.0], or None when the file cannot be parsed.
    """
    try:
        blob = trf_path.read_bytes()
    except OSError:
        return None

    if len(blob) > _TRF_MAX_BYTES:
        return None  # best-effort; oversized .trf treated as unparseable (SR-MEM-001)

    if blob.startswith(_TRF_MAGIC):
        return _severity_from_binary_trf(blob)

    if blob.startswith(_TRF_TEXT_MAGIC):
        return _severity_from_text_trf(blob)

    return None  # unknown magic — forward-compat (NF-2)


def recommend(severity: float | None) -> Literal["skip", "apply"]:
    """Advisory recommendation for whether to apply shake stabilization.

    Uses _SEVERITY_APPLY_THRESHOLD as the decision boundary.  Returns 'apply'
    as the safe-default when severity is None (unparseable .trf), because a
    missed stabilization is more harmful than a no-op apply on stable footage.

    Calibrated against real fixtures; advisory only — the calling agent makes
    the final decision on whether to apply stabilization.

    Args:
        severity: Shake severity in [0.0, 1.0], or None when estimation failed.

    Returns:
        'apply' when severity is None or >= _SEVERITY_APPLY_THRESHOLD, else 'skip'.
    """
    if severity is None or severity >= _SEVERITY_APPLY_THRESHOLD:
        return "apply"
    return "skip"


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
            "severity": float | None,  # 0.0-1.0 best-effort; None when unparseable
            "recommendation": str,     # "skip"|"apply" advisory; "apply" when None
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
    recommendation = recommend(severity)
    if severity is None:
        # Consolidated warning: severity null + recommendation defaulted (CR-M-001).
        # stabilize.py must not add a duplicate warning for this condition.
        warnings.append(
            "Could not estimate shake severity from the .trf file;"
            " severity recorded as null; recommendation defaulted to 'apply'."
        )

    return {
        "trf_path": str(trf_abs.resolve()),
        "severity": severity,
        "recommendation": recommendation,
        "warnings": warnings,
    }
