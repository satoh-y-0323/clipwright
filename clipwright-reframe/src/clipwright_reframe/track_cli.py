"""track_cli.py — Separate-process CLI for motion-centroid tracking.

Not imported by the MCP server process (numpy isolation, architecture-report §2.1).
reframe.py spawns this via sys.executable -m clipwright_reframe.track_cli.

CLI contract (architecture-report §2.5):
  - main(argv) catches all exceptions at the top level, always writes stdout JSON,
    and returns 0.
  - Success: {"track": [{"t_s": float, "cx": float, "cy": float}, ...],
              "diagnostics": {...}}
  - Error:   {"error": {"code": str, "message": str, "hint": str}}
  - stdout is JSON only. Progress/debug goes to stderr.
  - CWE-209: error.message must not contain full file paths or stack traces.
"""

from __future__ import annotations

# Module-level __module__ attribute for test-harness patch.object compatibility.
# Standard Python modules expose __name__ but not __module__; this alias allows
# tests to locate this module via cli.__module__ when constructing patch targets.
__module__: str = __name__

import argparse
import contextlib
import json
import math
import os
import sys
import tempfile
from typing import Any

import clipwright.process as _process
from clipwright.cli_io import force_utf8_io
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, safe_subprocess_message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Temp file size limit (SR-L-2 / DC-GP-002 / AC-14): guard against runaway
# rawvideo allocation for very long media.  Estimate before spawning ffmpeg;
# auto-reduce fps/w0 if the estimate exceeds the limit.
_SIZE_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MB

_DEFAULT_TRACK_WIDTH = 160  # rawvideo decode width (architecture-report §2.6)
_DEFAULT_FPS = 4.0
_DEFAULT_EMA_ALPHA = 0.2
_DEFAULT_MOTION_FLOOR = 8
_DEFAULT_RDP_EPSILON0 = 0.01
_DEFAULT_N_MAX = 80  # confirmed by parent adjudication (ffmpeg additive expr cap)

_TRACK_INSTALL_HINT = (
    "Install tracking dependencies with `pip install 'clipwright-reframe[track]'`."
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _error_output(code: str, message: str, hint: str) -> None:
    """Write error JSON to stdout (CWE-209: caller must sanitize paths)."""
    result: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        }
    }
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


# ---------------------------------------------------------------------------
# Argparse type converters (SR-M-3 / CWE-20 / CWE-400)
# ---------------------------------------------------------------------------


def _positive_float(s: str) -> float:
    """Accept only finite positive floats (guards fps, ema_alpha entry points)."""
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a number, got {s!r}") from None
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive finite number, got {s!r}")
    return v


def _positive_int(s: str) -> int:
    """Accept only positive integers (guards width, max-keyframes entry points)."""
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {s!r}") from None
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {s!r}")
    return v


def _ema_alpha_float(s: str) -> float:
    """Accept floats in (0, 1] for EMA alpha."""
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a number, got {s!r}") from None
    if not math.isfinite(v) or v <= 0 or v > 1:
        raise argparse.ArgumentTypeError(
            f"must be a finite number in (0, 1], got {s!r}"
        )
    return v


# ---------------------------------------------------------------------------
# Internal computation helpers (exposed for unit testing)
# ---------------------------------------------------------------------------


def _compute_centroids(
    frames: Any,  # numpy ndarray (N, H, W) uint8 gray
    fps: float,
    motion_floor: int,
    motion_threshold: int,
) -> list[tuple[float, float | None, float | None]]:
    """Compute per-frame motion centroids via consecutive-frame difference.

    Returns list of (t_s, cx_or_None, cy_or_None).
    cx/cy are normalised [0,1] relative to frame dimensions.
    None means the frame had insufficient motion (hold-fill candidate).

    Args:
        frames: (N, H, W) uint8 numpy array (gray8 rawvideo).
        fps: Sampling frame rate used to assign timestamps.
        motion_floor: Pixel-difference threshold below which diff is zeroed.
        motion_threshold: Minimum total diff sum to count as motion.
    """
    import numpy as np  # type: ignore[import-not-found]

    n_full, h0, w0 = frames.shape
    xs = np.arange(w0, dtype=np.float64)
    ys = np.arange(h0, dtype=np.float64)

    raw_pts: list[tuple[float, float | None, float | None]] = []
    for i in range(1, n_full):
        diff = np.abs(frames[i].astype(np.int16) - frames[i - 1].astype(np.int16))
        diff[diff < motion_floor] = 0
        total = int(diff.sum())
        t_s = float(i) / fps
        if total < motion_threshold:
            raw_pts.append((t_s, None, None))
        else:
            cx_n = float((xs * diff.sum(axis=0)).sum() / total)
            cy_n = float((ys * diff.sum(axis=1)).sum() / total)
            raw_pts.append((t_s, cx_n / (w0 - 1), cy_n / (h0 - 1)))

    return raw_pts


def _clamp01(v: float) -> float:
    """Clamp value to [0.0, 1.0]."""
    return max(0.0, min(1.0, v))


def _apply_seed_and_ema(
    raw_pts: list[tuple[float, float | None, float | None]],
    ema_alpha: float,
) -> list[tuple[float, float, float]]:
    """Apply onset-seed hold-fill and EMA smoothing to raw centroid sequence.

    Onset seed (DC-AS-004): static prefix is seeded with the first detected
    motion centroid (not 0.5) to prevent initial jump on motion start.

    Args:
        raw_pts: List of (t_s, cx_or_None, cy_or_None).
        ema_alpha: EMA smoothing factor in (0, 1].

    Returns:
        List of (t_s, cx, cy) with all values in [0.0, 1.0].
    """
    if not raw_pts:
        return []

    # Seed: find first motion centroid; fall back to 0.5 if all-static.
    first_motion = next((p for p in raw_pts if p[1] is not None), None)
    seed_x = first_motion[1] if first_motion is not None else 0.5
    seed_y = first_motion[2] if first_motion is not None else 0.5

    prev_cx: float = float(seed_x)  # type: ignore[arg-type]
    prev_cy: float = float(seed_y)  # type: ignore[arg-type]

    hold_filled: list[tuple[float, float, float]] = []
    for t_s, cx, cy in raw_pts:
        if cx is None:
            cx_f, cy_f = prev_cx, prev_cy
        else:
            cx_f, cy_f = float(cx), float(cy)
        prev_cx, prev_cy = cx_f, cy_f
        hold_filled.append((t_s, cx_f, cy_f))

    # EMA smoothing.
    alpha = ema_alpha
    sx, sy = hold_filled[0][1], hold_filled[0][2]
    smoothed: list[tuple[float, float, float]] = []
    for t_s, cx_f, cy_f in hold_filled:
        sx = alpha * cx_f + (1.0 - alpha) * sx
        sy = alpha * cy_f + (1.0 - alpha) * sy
        smoothed.append((t_s, _clamp01(sx), _clamp01(sy)))

    return smoothed


# ---------------------------------------------------------------------------
# RDP decimation (DC-AS-006 / DC-AM-003 / DC-AM-005)
# ---------------------------------------------------------------------------


def _rdp_keep_indices(
    pts: list[tuple[float, float, float]], epsilon: float
) -> set[int]:
    """Return index set to keep using RDP applied independently to cx and cy axes.

    The kept set is the union of indices from the (t,cx) and (t,cy) projections.
    """
    n = len(pts)
    if n <= 2:
        return set(range(n))

    def _rdp_1d(values: list[float], lo: int, hi: int, eps: float) -> set[int]:
        """Recursive RDP on a 1-D value list with uniform x=index."""
        if hi <= lo + 1:
            return {lo, hi}
        # Perpendicular distance from point[i] to line(lo→hi)
        # Line: y = values[lo] + (values[hi]-values[lo])*(i-lo)/(hi-lo)
        max_dist = 0.0
        max_idx = lo
        span = hi - lo
        dv = values[hi] - values[lo]
        for i in range(lo + 1, hi):
            predicted = values[lo] + dv * (i - lo) / span
            dist = abs(values[i] - predicted)
            if dist > max_dist:
                max_dist = dist
                max_idx = i
        if max_dist <= eps:
            return {lo, hi}
        return _rdp_1d(values, lo, max_idx, eps) | _rdp_1d(values, max_idx, hi, eps)

    cx_vals = [p[1] for p in pts]
    cy_vals = [p[2] for p in pts]
    keep_cx = _rdp_1d(cx_vals, 0, n - 1, epsilon)
    keep_cy = _rdp_1d(cy_vals, 0, n - 1, epsilon)
    return keep_cx | keep_cy


def _enforce_min_interval(
    pts: list[tuple[float, float, float]], min_gap: float
) -> list[tuple[float, float, float]]:
    """Merge consecutive points with t_s gap < min_gap (DC-AM-005).

    When two adjacent points are too close in time, the later one is dropped
    (average would change values which hurts the signal; dropping is simpler).
    """
    if not pts:
        return pts
    result = [pts[0]]
    for p in pts[1:]:
        if p[0] - result[-1][0] >= min_gap - 1e-9:
            result.append(p)
    return result


def _decimate(
    pts: list[tuple[float, float, float]],
    n_max: int,
    eps0: float = _DEFAULT_RDP_EPSILON0,
) -> tuple[list[tuple[float, float, float]], dict[str, Any]]:
    """Decimate pts to at most n_max keyframes using RDP binary-search (DC-AS-006).

    Args:
        pts: Sorted (t_s, cx, cy) list.
        n_max: Maximum number of output keyframes.
        eps0: Initial RDP epsilon for binary search.

    Returns:
        (kept_points, diagnostics_dict)
    """
    n_before = len(pts)

    if n_before <= n_max:
        diag: dict[str, Any] = {
            "keyframes_before_decimation": n_before,
            "keyframes_after_decimation": n_before,
            "dropped": 0,
        }
        return pts, diag

    # Binary search on epsilon to find fewest points that fit in n_max.
    lo, hi = eps0, 1.0

    def _apply_rdp(eps: float) -> list[tuple[float, float, float]]:
        idx = sorted(_rdp_keep_indices(pts, eps))
        return [pts[i] for i in idx]

    kept = _apply_rdp(lo)
    if len(kept) <= n_max:
        # Initial epsilon already reduces enough — unlikely for small eps0 but handle.
        pass
    else:
        # Cache the last candidate that satisfies the n_max constraint so we avoid
        # an extra _apply_rdp(hi) call after the loop (CR-M-2 / CR-Q-001).
        final_candidate = kept  # initial fallback (eps=lo, may exceed n_max)
        for _ in range(24):
            mid = (lo + hi) / 2.0
            candidate = _apply_rdp(mid)
            if len(candidate) > n_max:
                lo = mid
            else:
                hi = mid
                final_candidate = candidate  # cache when constraint satisfied
        kept = final_candidate

    # Hard guard: truncate to n_max if binary search overshot.
    if len(kept) > n_max:
        kept = kept[:n_max]

    n_after = len(kept)
    diag = {
        "keyframes_before_decimation": n_before,
        "keyframes_after_decimation": n_after,
        "dropped": n_before - n_after,
    }
    return kept, diag


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Track CLI entry point.

    Catches all exceptions at top level, writes JSON to stdout, returns 0.

    Args:
        argv: Command-line argument list. Uses sys.argv[1:] if None.

    Returns:
        Exit code (always 0).
    """
    force_utf8_io()

    parser = argparse.ArgumentParser(
        description=(
            "Detect motion centroids in a video and output keyframe JSON to stdout."
        )
    )
    parser.add_argument("--media", required=True, help="Input video file path")
    parser.add_argument(
        "--fps",
        type=_positive_float,
        default=_DEFAULT_FPS,
        help=f"Sampling frame rate, positive finite (default {_DEFAULT_FPS})",
    )
    parser.add_argument(
        "--width",
        type=_positive_int,
        default=_DEFAULT_TRACK_WIDTH,
        help=(
            f"Decode width for rawvideo, positive int (default {_DEFAULT_TRACK_WIDTH})"
        ),
    )
    parser.add_argument(
        "--ema-alpha",
        type=_ema_alpha_float,
        default=_DEFAULT_EMA_ALPHA,
        help=f"EMA smoothing alpha in (0,1] (default {_DEFAULT_EMA_ALPHA})",
    )
    parser.add_argument(
        "--motion-threshold",
        type=_positive_int,
        default=None,
        help="Motion detection threshold, positive int (default: width*height*2)",
    )
    parser.add_argument(
        "--max-keyframes",
        type=_positive_int,
        default=_DEFAULT_N_MAX,
        help=(
            f"Maximum output keyframes after decimation,"
            f" positive int (default {_DEFAULT_N_MAX})"
        ),
    )
    parser.add_argument(
        "--media-duration",
        type=_positive_float,
        default=None,
        help="Total media duration (seconds), positive finite, for timeout calculation",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        _error_output(
            code=str(ErrorCode.INVALID_INPUT),
            message=f"Argument parsing failed: exit code {exc.code}",
            hint="Specify --media <path> as a required argument.",
        )
        return 0

    media: str = args.media
    fps: float = args.fps
    w0: int = args.width
    ema_alpha: float = args.ema_alpha
    n_max: int = args.max_keyframes
    media_duration: float | None = args.media_duration

    try:
        # Lazy import of numpy (keep out of server process).
        try:
            import numpy as np  # type: ignore[import-not-found]  # optional [track]
        except ImportError:
            _error_output(
                code=str(ErrorCode.DEPENDENCY_MISSING),
                message="numpy is not installed",
                hint=_TRACK_INSTALL_HINT,
            )
            return 0

        # --- Resolve ffmpeg ---
        ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

        # --- Determine frame dimensions (DC-AS-003: explicit both dimensions) ---
        # Probe src dimensions to compute h0.
        from clipwright.media import inspect_media

        try:
            media_info = inspect_media(media)
        except ClipwrightError:
            media_info = None

        src_w: int | None = None
        src_h: int | None = None
        if media_info is not None:
            for stream in media_info.streams:
                if stream.codec_type == "video":
                    src_w = getattr(stream, "width", None)
                    src_h = getattr(stream, "height", None)
                    break

        if src_w and src_h and src_w > 0 and src_h > 0:
            # Both dimensions explicitly computed and rounded to even (DC-AS-003).
            h0 = max(2, (round(src_h * w0 / src_w) // 2) * 2)
        else:
            # Fallback: assume 16:9.
            h0 = max(2, (round(w0 * 9 / 16) // 2) * 2)

        motion_threshold = (
            args.motion_threshold if args.motion_threshold is not None else w0 * h0 * 2
        )

        # --- Temp file size guard (SR-L-2 / DC-GP-002 / AC-14) ---
        # Estimate rawvideo size before spawning ffmpeg; auto-reduce fps/w0 if
        # the estimate exceeds _SIZE_LIMIT_BYTES to avoid disk/OOM exhaustion.
        size_warnings: list[str] = []
        if media_duration is not None and media_duration > 0:
            estimated_bytes = w0 * h0 * int(math.ceil(fps * media_duration))
            if estimated_bytes > _SIZE_LIMIT_BYTES:
                scale = (_SIZE_LIMIT_BYTES / estimated_bytes) ** 0.5
                fps = max(1.0, fps * scale)
                w0 = max(64, (max(1, int(w0 * scale))) // 2 * 2)
                # Recompute h0 and motion_threshold after dimension change.
                if src_w and src_h and src_w > 0 and src_h > 0:
                    h0 = max(2, (round(src_h * w0 / src_w) // 2) * 2)
                else:
                    h0 = max(2, (round(w0 * 9 / 16) // 2) * 2)
                if args.motion_threshold is None:
                    motion_threshold = w0 * h0 * 2
                limit_mb = _SIZE_LIMIT_BYTES // (1024 * 1024)
                size_warnings.append(
                    f"Input size estimate exceeded {limit_mb} MB;"
                    f" downsampled to fps={fps:.1f} width={w0}."
                )
                print(
                    f"[track_cli] size guard: {size_warnings[-1]}",
                    file=sys.stderr,
                )

        # --- Compute ffmpeg timeout ---
        if media_duration is not None:
            ffmpeg_timeout = float(max(60, math.ceil(media_duration * 4)))
        else:
            ffmpeg_timeout = 120.0

        # --- Extract rawvideo to temp file (ADR-T1: temp file, not pipe) ---
        # Open with delete=False to get the name, then delete in try/finally (AC-14).
        tmp_path: str = ""
        tmp_file = tempfile.NamedTemporaryFile(suffix=".raw", delete=False)  # noqa: SIM115
        tmp_path = tmp_file.name
        tmp_file.close()

        # frames is assigned inside the try block only when n_full >= 2;
        # the early-return path (n_full < 2) exits before reaching the usage
        # below.  Initialise to None here for scope clarity (CR-M-1).
        frames: Any = None

        try:
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                media,
                "-an",
                "-vf",
                f"fps={fps},scale={w0}:{h0},format=gray",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-y",
                tmp_path,
            ]
            _process.run(cmd, timeout=ffmpeg_timeout)

            # --- Read rawvideo from temp file ---
            raw = np.fromfile(tmp_path, dtype=np.uint8)
            frame_size = w0 * h0
            n_full = raw.size // frame_size
            if n_full < 2:
                # Too few frames for difference-based tracking.
                track_out = [{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]
                diag: dict[str, Any] = {
                    "frames_analyzed": n_full,
                    "keyframes_before_decimation": 1,
                    "keyframes_after_decimation": 1,
                    "dropped": 0,
                    "hold_fraction": 0.0,
                    "sampling_fps": fps,
                    "width": w0,
                    "height": h0,
                }
                result: dict[str, Any] = {"track": track_out, "diagnostics": diag}
                print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
                return 0

            frames = raw[: n_full * frame_size].reshape(n_full, h0, w0)

        finally:
            # Always remove temp file (AC-14 / DC-GP-002).
            if tmp_path and os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

        # --- Compute motion centroids ---
        raw_pts = _compute_centroids(
            frames,
            fps=fps,
            motion_floor=_DEFAULT_MOTION_FLOOR,
            motion_threshold=motion_threshold,
        )

        hold_count = sum(1 for p in raw_pts if p[1] is None)
        hold_fraction = hold_count / len(raw_pts) if raw_pts else 0.0

        # --- Apply onset seed and EMA smoothing ---
        smoothed = _apply_seed_and_ema(raw_pts, ema_alpha=ema_alpha)

        if not smoothed:
            smoothed = [(0.0, 0.5, 0.5)]

        # --- RDP decimation ---
        kept, decim_diag = _decimate(smoothed, n_max=n_max)

        # --- Minimum interval guard (DC-AM-005) ---
        min_gap = 1.0 / fps
        kept = _enforce_min_interval(kept, min_gap)

        # Build output.
        track_out = [
            {"t_s": round(t, 6), "cx": round(cx, 6), "cy": round(cy, 6)}
            for t, cx, cy in kept
        ]

        diag = {
            "frames_analyzed": n_full,
            "keyframes_before_decimation": decim_diag["keyframes_before_decimation"],
            "keyframes_after_decimation": decim_diag["keyframes_after_decimation"],
            "dropped": decim_diag["dropped"],
            "hold_fraction": round(hold_fraction, 4),
            "sampling_fps": fps,
            "width": w0,
            "height": h0,
        }
        if size_warnings:
            diag["size_guard_warnings"] = size_warnings

        result = {"track": track_out, "diagnostics": diag}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except ClipwrightError as exc:
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            safe_msg = safe_subprocess_message(exc)
        else:
            # Use a fixed message for non-subprocess errors to avoid leaking internal
            # state (CWE-209 / SR-M-1).
            safe_msg = "Track CLI encountered an error."
        _error_output(
            code=str(exc.code),
            message=safe_msg,
            # hint is fixed to avoid leaking internal paths or state (CWE-209).
            hint="See track CLI diagnostics for details.",
        )
        return 0

    except ImportError:
        _error_output(
            code=str(ErrorCode.DEPENDENCY_MISSING),
            message="numpy is not installed",
            hint=_TRACK_INSTALL_HINT,
        )
        return 0

    except Exception:
        import traceback

        traceback.print_exc(file=sys.stderr)
        _error_output(
            code=str(ErrorCode.INTERNAL),
            message="An unexpected error occurred in track CLI",
            hint="Please report with reproduction steps.",
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
