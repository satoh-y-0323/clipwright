"""overlay.py — clipwright-overlay orchestration layer.

Handles the full flow: input validation -> load timeline -> validate options
-> idempotency check -> add image_overlay marker -> save timeline -> return envelope.

Design decisions:
- _add_overlay_inner() is the raising implementation; add_overlay() is the public
  boundary that catches ClipwrightError and converts to error_result.
- Value-range validation is performed manually (OQ-1) for precise error hints.
- image_path is stored as a RELATIVE posix path under the output timeline parent
  dir (V2-3) to ensure round-trip stability across project moves.
- x/y allowlist ^[A-Za-z0-9_()+\\-*/. ]+$ prevents filtergraph injection (V2-5).
- Idempotency: exact duplicate (all metadata fields match) -> no-op with warning.
  Comparison uses the stored relative image_path string (V2-3).
- Non-destructive: input OTIO bytes are never modified; output is always new.
- Rate determination: first clip source_range -> existing image_overlay marker
  rate -> fallback 1000.0 with warning.
- Boundary check _check_output_within_timeline_dir is a local copy of the
  clipwright-text implementation to avoid cross-package imports.
  When changing the logic here, ensure behaviour remains in sync with
  clipwright-text's _check_output_within_timeline_dir; the two functions must
  enforce the same boundary contract.
- This module is subprocess-free (annotation layer; no ffmpeg/ffprobe calls).
"""

from __future__ import annotations

import collections.abc
import os
import re
from pathlib import Path
from typing import Any

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import add_marker, get_markers, load_timeline, save_timeline
from clipwright.schemas import RationalTimeModel, TimeRangeModel, ToolResult

from clipwright_overlay import __version__
from clipwright_overlay.schemas import AddOverlayOptions

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Control-character pattern for image_path and position expressions.
# Includes: NUL-US (\x00-\x1f), DEL (\x7f).
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")

# x/y allowlist: permits alphanumeric, underscore, parentheses, arithmetic
# operators, dot, and space. Rejects : ; [ ] , ' and control characters (V2-5).
# This covers ffmpeg overlay expressions such as (W-w)/2, (H-h)/2,
# main_w-overlay_w-10, and simple numeric positions like 0 or 100.
_XY_ALLOWLIST = re.compile(r"^[A-Za-z0-9_()+\-*/. ]+$")

# Allowed image file extensions for overlay (case-insensitive check via .lower()).
_ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp"}
)

# Tolerance for float comparison in idempotency checks (rate-invariant).
_IDEMPOTENCY_EPS: float = 1e-6

# Maximum number of image_overlay markers allowed per timeline (V2-9 / DoS guard).
_MAX_IMAGE_OVERLAYS: int = 64


# ===========================================================================
# Path boundary helpers (local copies; keep in sync with clipwright-text)
# ===========================================================================


def _check_output_within_timeline_dir(timeline: Path, output: Path) -> None:
    """Verify that output is within the timeline's parent directory tree.

    Mirrors clipwright-text _check_output_within_timeline_dir boundary contract.
    Allows recursive subdirectories; raises PATH_NOT_ALLOWED only when the
    resolved output is outside the timeline directory tree.

    Intentionally re-implemented locally to avoid cross-package import of
    clipwright-text (NR-L-4: cross-package imports between satellite tools
    create tight coupling and break independent packaging/deployment).

    Args:
        timeline: Path to the input OTIO timeline file.
        output: Output path to validate against the boundary.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when output is outside the
            timeline's parent directory tree.
    """
    try:
        allowed_base = timeline.parent.resolve()
        target_resolved = output.resolve()
        target_str = str(target_resolved)
        base_str = str(allowed_base)
        if not (
            target_str == base_str
            or target_str.startswith(base_str + "/")
            or target_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Output path points outside the project boundary.",
                hint=(
                    "Place the output file within the same directory as the "
                    "OTIO timeline, or in a subdirectory of it."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve() failed (network path, symlink loop, etc.): fall back to
        # absolute()-based best-effort comparison.
        try:
            allowed_base_abs = str(timeline.parent.absolute())
            target_abs = str(output.absolute())
            if not (
                target_abs == allowed_base_abs
                or target_abs.startswith(allowed_base_abs + "/")
                or target_abs.startswith(allowed_base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Output path points outside the project boundary.",
                    hint=(
                        "Place the output file within the same directory as the "
                        "OTIO timeline, or in a subdirectory of it."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            # Skip only when absolute() also fails (truly unresolvable path).
            pass


def _check_within_image_overlay_dir(output_otio_path: Path, resolved: Path) -> None:
    """Verify the resolved image path is within the output timeline's parent dir tree.

    Uses the same boundary contract as _check_output_within_timeline_dir but
    applied to the image file: the allowed base is the output OTIO's parent dir
    (V2-3 / ADR-OV-3). This ensures that render-side re-validation (which uses
    the timeline parent as the base) will also pass, since the output timeline
    is the input to render.

    Args:
        output_otio_path: Path to the output OTIO file (determines allowed base).
        resolved: Resolved absolute path to the image file to validate.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when the image is outside the
            output timeline's parent directory tree.
    """
    try:
        allowed_base = output_otio_path.resolve().parent
        target_str = str(resolved)
        base_str = str(allowed_base)
        if not (
            target_str == base_str
            or target_str.startswith(base_str + "/")
            or target_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Image path points outside the project boundary.",
                hint=(
                    "Place the image file within the same directory as the "
                    "output OTIO timeline, or in a subdirectory of it."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        try:
            allowed_base_abs = str(output_otio_path.absolute().parent)
            target_abs = str(resolved)
            if not (
                target_abs == allowed_base_abs
                or target_abs.startswith(allowed_base_abs + "/")
                or target_abs.startswith(allowed_base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Image path points outside the project boundary.",
                    hint=(
                        "Place the image file within the same directory as the "
                        "output OTIO timeline, or in a subdirectory of it."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            pass


# ===========================================================================
# Validation helpers
# ===========================================================================


def _validate_overlay_fields(options: AddOverlayOptions, output: str) -> None:
    """Validate value-range, image_path (4-stage), and position expression fields.

    Validation order (fixed to keep error messages deterministic — ADR-OV-2):
      1. Value ranges (start_sec, duration_sec, scale, opacity, fade_in_sec,
         fade_out_sec, fade sum)
      2. image_path 4-stage:
         a. path safety: single-quote or control char (INVALID_INPUT)
            — checked before resolve() to prevent ValueError from control chars
              and to ensure safety always precedes existence/extension checks
         b. co-location (PATH_NOT_ALLOWED)
         c. existence (FILE_NOT_FOUND, basename only)
         d. extension allowlist (INVALID_INPUT)
      3. x/y allowlist (INVALID_INPUT) (V2-5)

    Path safety is placed before co-location because:
    - control chars in the path cause Path.resolve() to raise ValueError on Windows
    - single-quotes in the path must be rejected before the file's existence is
      checked (the file is typically absent with a bad path)
    All violations raise ClipwrightError on the first failure.

    Args:
        options: AddOverlayOptions to validate.
        output: Output OTIO file path (used for co-location boundary).

    Raises:
        ClipwrightError: On the first validation failure.
    """
    out = Path(output)

    # --- 1. Value ranges ---
    if options.start_sec < 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Overlay start time must be 0 or greater.",
            hint="Set start_sec to a non-negative value.",
        )
    if options.duration_sec <= 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Overlay duration must be greater than 0.",
            hint="Set duration_sec to a positive number of seconds.",
        )
    # Manual recheck for scale (V2-9): schema is first line of defence; manual
    # recheck provides a precise hint for values that slip through or are set
    # programmatically after construction.
    if options.scale <= 0 or options.scale > 8.0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Scale must be in the range (0, 8.0].",
            hint=(
                "Set scale to a positive value no greater than 8.0 "
                "(e.g. 1.0 for original size)."
            ),
        )
    if options.opacity < 0 or options.opacity > 1.0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Opacity must be between 0.0 and 1.0.",
            hint="Set opacity to a value in the range [0.0, 1.0].",
        )
    if options.fade_in_sec < 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Fade-in duration must be 0 or greater.",
            hint="Set fade_in_sec to a non-negative value.",
        )
    if options.fade_out_sec < 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Fade-out duration must be 0 or greater.",
            hint="Set fade_out_sec to a non-negative value.",
        )
    if options.fade_in_sec + options.fade_out_sec > options.duration_sec:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Fade-in plus fade-out exceeds the overlay duration.",
            hint=(
                "Reduce fade durations or increase duration_sec so fades fit within it."
            ),
        )

    # --- 2. image_path 4-stage validation ---

    # 2a. path safety: single-quote or control char — checked BEFORE resolve() to:
    #     (1) avoid ValueError from control chars embedded in the path on Windows,
    #     (2) ensure safety violations surface before existence/extension checks.
    if "'" in options.image_path:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Image path must not contain single-quote characters.",
            hint=(
                "Remove single-quotes from the image file path "
                "(they would corrupt filtergraph quoting)."
            ),
        )
    if _CONTROL_CHAR_PATTERN.search(options.image_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Image path must not contain control characters.",
            hint="Remove control characters from the image file path.",
        )

    resolved = Path(options.image_path).resolve()

    # 2b. co-location: image must be within the output timeline's parent dir tree (V2-3)
    _check_within_image_overlay_dir(out, resolved)

    # 2c. existence
    if not resolved.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Image file not found: {Path(options.image_path).name}",
            hint="Verify the image path and ensure the file exists.",
        )

    # 2d. extension allowlist
    if resolved.suffix.lower() not in _ALLOWED_IMAGE_EXTENSIONS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Image extension '{resolved.suffix}' is not supported. "
                f"Allowed: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}."
            ),
            hint="Use a supported image format: .png, .jpg, .jpeg, or .webp.",
        )

    # --- 3. x/y allowlist (V2-5) ---
    if not _XY_ALLOWLIST.fullmatch(options.x):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="x expression contains forbidden characters.",
            hint=(
                "Use only alphanumeric characters, underscores, parentheses, "
                "arithmetic operators (+, -, *, /), dot, and space. "
                "Forbidden: : ; [ ] , ' and control characters."
            ),
        )
    if not _XY_ALLOWLIST.fullmatch(options.y):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="y expression contains forbidden characters.",
            hint=(
                "Use only alphanumeric characters, underscores, parentheses, "
                "arithmetic operators (+, -, *, /), dot, and space. "
                "Forbidden: : ; [ ] , ' and control characters."
            ),
        )


# ===========================================================================
# Metadata and idempotency helpers
# ===========================================================================


def _overlay_metadata_dict(
    options: AddOverlayOptions,
    output: str,
    version: str,
) -> dict[str, Any]:
    """Build the clipwright metadata dict for an image_overlay marker.

    Stores the image_path as a RELATIVE posix path from the output timeline
    parent dir (V2-3): this ensures round-trip stability when the project is
    moved, as long as the relative layout between the timeline and image is
    preserved. render reconstructs the absolute path as:
      (Path(timeline_path).resolve().parent / rel).resolve()

    Args:
        options: Validated AddOverlayOptions.
        output: Output OTIO file path (determines the relative base).
        version: Package version string to embed in metadata.

    Returns:
        Dict to store under marker.metadata["clipwright"].

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED if relpath contains '..', which
            indicates the image is outside the output parent tree (defense-in-depth).
    """
    resolved = Path(options.image_path).resolve()
    timeline_parent = Path(output).resolve().parent
    try:
        rel_str = os.path.relpath(resolved, timeline_parent)
    except ValueError:
        # Different drive on Windows: relpath raises ValueError -> co-location violation
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message="Image path points outside the project boundary.",
            hint=(
                "Place the image file within the same directory as the "
                "output OTIO timeline, or in a subdirectory of it."
            ),
        ) from None
    rel = Path(rel_str).as_posix()
    # Defense-in-depth (V2-3): if co-location check passed but relpath still yields
    # a '..' prefix, treat as PATH_NOT_ALLOWED.
    if rel.startswith(".."):
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message="Image path points outside the project boundary.",
            hint=(
                "Place the image file within the same directory as the "
                "output OTIO timeline, or in a subdirectory of it."
            ),
        )
    return {
        "tool": "clipwright-overlay",
        "version": version,
        "kind": "image_overlay",
        "image_path": rel,
        "start_sec": options.start_sec,
        "duration_sec": options.duration_sec,
        "x": options.x,
        "y": options.y,
        "scale": options.scale,
        "opacity": options.opacity,
        "fade_in_sec": options.fade_in_sec,
        "fade_out_sec": options.fade_out_sec,
    }


def _is_duplicate_overlay(
    marker: otio.schema.Marker, options: AddOverlayOptions, output: str
) -> bool:
    """Return True if marker is an exact duplicate of the given options.

    Compares all AddOverlayOptions fields stored in marker.metadata["clipwright"]
    against the current options. Uses approximate float comparison for numeric
    fields to be rate-invariant. image_path comparison is done on the relative
    posix string (V2-3): both the stored value and the computed relative path
    of the new options must match exactly.

    Args:
        marker: An existing image_overlay marker to compare.
        options: Current AddOverlayOptions to check for duplication.
        output: Output OTIO file path (for computing relative image_path).

    Returns:
        True if all fields match (complete duplicate -> no-op).
    """
    cw = marker.metadata.get("clipwright", {})
    if not isinstance(cw, collections.abc.Mapping):
        return False
    if cw.get("kind") != "image_overlay":
        return False

    # Compute the relative posix path for the new options
    try:
        resolved = Path(options.image_path).resolve()
        timeline_parent = Path(output).resolve().parent
        try:
            rel_str = os.path.relpath(resolved, timeline_parent)
        except ValueError:
            return False
        new_rel = Path(rel_str).as_posix()
        if new_rel.startswith(".."):
            return False
    except Exception:
        return False

    # String fields: exact match (image_path as relative, x, y)
    if cw.get("image_path") != new_rel:
        return False
    if cw.get("x") != options.x:
        return False
    if cw.get("y") != options.y:
        return False

    # Float fields: approximate comparison (rate-invariant tolerance)
    def _approx_eq(a: object, b: float) -> bool:
        if not isinstance(a, (int, float)):
            return False
        return abs(float(a) - b) <= _IDEMPOTENCY_EPS

    if not _approx_eq(cw.get("start_sec"), options.start_sec):
        return False
    if not _approx_eq(cw.get("duration_sec"), options.duration_sec):
        return False
    if not _approx_eq(cw.get("scale"), options.scale):
        return False
    if not _approx_eq(cw.get("opacity"), options.opacity):
        return False
    if not _approx_eq(cw.get("fade_in_sec"), options.fade_in_sec):
        return False
    return _approx_eq(cw.get("fade_out_sec"), options.fade_out_sec)


# ===========================================================================
# Rate resolution
# ===========================================================================


def _resolve_rate(
    video_track: otio.schema.Track,
) -> tuple[float, list[str]]:
    """Determine the rate for RationalTime construction.

    Priority:
      1. source_range.rate of the first Clip in the V1 track.
      2. marked_range.start_time.rate of the first existing image_overlay marker.
      3. Fallback: 1000.0 with a warning.

    Args:
        video_track: The first Video track from the loaded timeline.

    Returns:
        Tuple of (rate: float, warnings: list[str]). warnings is non-empty only
        when the fallback rate is used.
    """
    # Priority 1: first clip's source_range rate
    for item in video_track:
        if isinstance(item, otio.schema.Clip) and item.source_range is not None:
            return float(item.source_range.start_time.rate), []

    # Priority 2: existing image_overlay marker rate
    for marker in video_track.markers:
        cw = marker.metadata.get("clipwright", {})
        if (
            isinstance(cw, collections.abc.Mapping)
            and cw.get("kind") == "image_overlay"
        ):
            return float(marker.marked_range.start_time.rate), []

    # Priority 3: fallback
    return 1000.0, [
        "Could not determine timeline rate from clips or existing markers; "
        "using fallback rate 1000.0. Consider providing a timeline with clips."
    ]


# ===========================================================================
# Core implementation
# ===========================================================================


def _add_overlay_inner(
    timeline: str,
    output: str,
    options: AddOverlayOptions,
) -> ToolResult:
    """Internal implementation of add_overlay. Raises ClipwrightError on failure.

    Validation order:
      1. output suffix == .otio
      2. output parent directory exists
      3. output boundary check (PATH_NOT_ALLOWED when outside timeline dir)
      4. output != timeline
      5. field validation (_validate_overlay_fields, including image_path 4-stage)
      6. load timeline (FILE_NOT_FOUND / OTIO_ERROR propagate)
      7. first TrackKind.Video track exists
      8. rate determination
      9. _MAX_IMAGE_OVERLAYS cap check (V2-9)
     10. idempotency check (exact duplicate -> no-op)
     11. add marker (image_{n}, all metadata fields, relative image_path)
     12. save timeline atomically
     13. return ok_result

    Args:
        timeline: Input OTIO timeline file path.
        output: Output OTIO file path.
        options: Validated AddOverlayOptions.

    Returns:
        ToolResult from ok_result.

    Raises:
        ClipwrightError: On any validation or I/O failure.
    """
    out = Path(output)
    inp = Path(timeline)

    # --- Step 1: output suffix validation ---
    if out.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path must have a .otio extension.",
            hint="Change the output file extension to .otio (e.g., 'result.otio').",
        )

    # --- Step 2: output parent directory exists ---
    if not out.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="Output directory does not exist.",
            hint="Create the output directory before calling clipwright_add_overlay.",
        )

    # --- Step 3: output boundary check ---
    _check_output_within_timeline_dir(inp, out)

    # --- Step 4: output != timeline ---
    if out.resolve() == inp.resolve():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path must differ from the input timeline path.",
            hint=(
                "Provide a distinct output path (e.g., append '_overlay' before .otio)."
            ),
        )

    # --- Step 5: field validation (value ranges + image_path 4-stage + x/y) ---
    _validate_overlay_fields(options, output)

    # --- Step 6: load timeline ---
    if not inp.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Timeline file not found: {inp.name}",
            hint="Verify the timeline path and ensure the file exists.",
        )
    timeline_obj = load_timeline(timeline)

    # --- Step 7: find first Video track ---
    video_track: otio.schema.Track | None = None
    for track in timeline_obj.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break

    if video_track is None:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="No video track found in the timeline.",
            hint=(
                "clipwright_add_overlay requires a timeline with at least one "
                "video track to attach image overlay markers."
            ),
        )

    # --- Step 8: rate determination ---
    rate, rate_warnings = _resolve_rate(video_track)

    # --- Step 9: _MAX_IMAGE_OVERLAYS cap (V2-9) ---
    existing_markers = get_markers(timeline_obj, kind="image_overlay")
    if len(existing_markers) >= _MAX_IMAGE_OVERLAYS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Cannot add image overlay: the timeline already has "
                f"{len(existing_markers)} image overlays "
                f"(maximum is {_MAX_IMAGE_OVERLAYS})."
            ),
            hint=(
                f"Remove some image_overlay markers before adding more. "
                f"The limit is {_MAX_IMAGE_OVERLAYS} per timeline."
            ),
        )

    # --- Step 10: idempotency check ---
    for existing in existing_markers:
        if _is_duplicate_overlay(existing, options, output):
            # Exact duplicate: save a copy of the timeline and return no-op result
            save_timeline(timeline_obj, output)
            overlay_count = len(existing_markers)
            basename = Path(options.image_path).name
            return ok_result(
                summary=(
                    f"Image overlay '{basename}' at {options.start_sec}s for "
                    f"{options.duration_sec}s already exists; no marker added. "
                    f"Timeline has {overlay_count} image overlay(s). "
                    f"Output: {out.name}."
                ),
                data={
                    "applied": 0,
                    "overlay_count": overlay_count,
                    "start_sec": options.start_sec,
                    "duration_sec": options.duration_sec,
                },
                artifacts=[
                    {
                        "role": "timeline",
                        "path": str(out.resolve()),
                        "format": "otio",
                    }
                ],
                warnings=["Identical image overlay already exists; no marker added."],
            )

    # --- Step 11: add marker ---
    # Count existing image_overlay markers to determine name index
    n = len(existing_markers)
    marker_name = f"image_{n}"

    # Build marker metadata with relative image_path (V2-3)
    metadata = _overlay_metadata_dict(options, output, __version__)

    # Build marked_range using the resolved rate
    marked_range = TimeRangeModel(
        start_time=RationalTimeModel(
            value=options.start_sec * rate,
            rate=rate,
        ),
        duration=RationalTimeModel(
            value=options.duration_sec * rate,
            rate=rate,
        ),
    )

    add_marker(
        video_track,
        marked_range=marked_range,
        name=marker_name,
        color=None,
        metadata=metadata,
    )

    # --- Step 12: save timeline ---
    save_timeline(timeline_obj, output)

    # --- Step 13: build result ---
    overlay_count = n + 1
    out_resolved = out.resolve()
    basename = Path(options.image_path).name
    summary = (
        f"Added image overlay '{basename}' at {options.start_sec}s for "
        f"{options.duration_sec}s. "
        f"Timeline now has {overlay_count} image overlay(s). "
        f"Output: {out.name}."
    )

    all_warnings = list(rate_warnings)

    return ok_result(
        summary=summary,
        data={
            "applied": 1,
            "overlay_count": overlay_count,
            "start_sec": options.start_sec,
            "duration_sec": options.duration_sec,
        },
        artifacts=[
            {
                "role": "timeline",
                "path": str(out_resolved),
                "format": "otio",
            }
        ],
        warnings=all_warnings if all_warnings else None,
    )


def add_overlay(
    timeline: str,
    output: str,
    options: AddOverlayOptions | None,
) -> dict[str, Any]:
    """Add an image_overlay marker to an OTIO timeline.

    Non-destructive: does not modify the input timeline file.
    Idempotent: calling with the same options on an already-annotated timeline
    produces applied=0 and a warning rather than duplicating the marker.

    The image_path is stored as a relative posix path in the marker metadata
    (V2-3) to ensure round-trip stability across project directory moves.

    Returns a plain dict (ToolResult.model_dump()) so callers can use both
    dict-style access (``result["ok"]``, ``result.get(...)``) and
    ``isinstance(result, dict)`` checks.  server.py wraps this in ToolResult
    for FastMCP's typed outputSchema.

    Args:
        timeline: Input OTIO timeline file path.
        output: Output OTIO file path (must end in .otio, must differ from timeline).
        options: AddOverlayOptions with required image_path/start_sec/duration_sec
            and optional style fields. None returns INVALID_INPUT.

    Returns:
        dict with the ToolResult envelope keys: ok, summary, data, artifacts, warnings,
        error.
    """
    if options is None:
        return error_result(
            "INVALID_INPUT",
            "options is required but was not provided.",
            "Pass an AddOverlayOptions with at least image_path, start_sec, "
            "and duration_sec.",
        ).model_dump()
    try:
        return _add_overlay_inner(timeline, output, options).model_dump()
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint).model_dump()
