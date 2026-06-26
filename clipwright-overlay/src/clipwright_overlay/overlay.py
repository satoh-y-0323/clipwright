"""overlay.py — clipwright-overlay orchestration layer.

Handles the full flow: input validation -> load timeline -> validate options
-> idempotency check -> add image_overlay marker -> save timeline -> return envelope.

Design decisions:
- _add_overlay_inner() is the raising implementation; add_overlay() is the public
  boundary that catches ClipwrightError and converts to error_result.
- Value-range validation is performed manually (OQ-1) for precise error hints.
- image_path is stored via media_ref_for_otio (ADR-PP-1): relative posix when
  the image is under the output timeline's parent directory; absolute path when
  outside.  render resolves both forms via check_media_ref at materialisation time.
- x/y allowlist ^[A-Za-z0-9_()+\\-*/. ]+$ prevents filtergraph injection (V2-5).
- Idempotency: exact duplicate (all metadata fields match) -> no-op with warning.
  Comparison uses the same media_ref_for_otio result as storage (handles both
  relative and absolute stored paths).
- Non-destructive: input OTIO bytes are never modified; output is always new.
- Output path: may be placed anywhere; only restriction is output != source
  (checked via check_output_not_source from pathpolicy).
- Rate determination: first clip source_range -> existing image_overlay marker
  rate -> fallback 1000.0 with warning.
- This module is subprocess-free (annotation layer; no ffmpeg/ffprobe calls).
"""

from __future__ import annotations

import collections.abc
import re
from pathlib import Path
from typing import Any

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import add_marker, get_markers, load_timeline, save_timeline
from clipwright.pathpolicy import check_output_not_source, media_ref_for_otio
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
# Validation helpers
# ===========================================================================


def _validate_overlay_fields(options: AddOverlayOptions, output: str) -> None:
    """Validate value-range, image_path (3-stage), and position expression fields.

    Validation order (fixed to keep error messages deterministic — ADR-OV-2):
      1. Value ranges (start_sec, duration_sec, scale, opacity, fade_in_sec,
         fade_out_sec, fade sum)
      2. image_path 3-stage:
         a. path safety: single-quote or control char (INVALID_INPUT)
            — checked before resolve() to prevent ValueError from control chars
              and to ensure safety always precedes existence/extension checks
         b. existence (FILE_NOT_FOUND, basename only)
         c. extension allowlist (INVALID_INPUT)
      3. x/y allowlist (INVALID_INPUT) (V2-5)

    Co-location restriction removed (ADR-PP-1 / impl-overlay): images may be
    placed anywhere; media_ref_for_otio stores relative posix when inside the
    output OTIO's parent dir, absolute when outside.

    Path safety is placed first because:
    - control chars in the path cause Path.resolve() to raise ValueError on Windows
    - single-quotes must be rejected before existence/extension checks
    All violations raise ClipwrightError on the first failure.

    Args:
        options: AddOverlayOptions to validate.
        output: Output OTIO file path (used only for boundary context in callers).

    Raises:
        ClipwrightError: On the first validation failure.
    """
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

    # --- 2. image_path 3-stage validation ---

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

    # 2b. existence
    if not resolved.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Image file not found: {Path(options.image_path).name}",
            hint="Verify the image path and ensure the file exists.",
        )

    # 2c. extension allowlist
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

    Stores image_path via media_ref_for_otio (ADR-PP-1):
      - inside output OTIO's parent directory -> relative POSIX path
      - outside -> absolute path (no '../' traversal stored)

    render resolves both forms via check_media_ref at materialisation time.

    Args:
        options: Validated AddOverlayOptions.
        output: Output OTIO file path (determines the relative/absolute split).
        version: Package version string to embed in metadata.

    Returns:
        Dict to store under marker.metadata["clipwright"].
    """
    otio_dir = Path(output).resolve().parent
    stored_path = media_ref_for_otio(options.image_path, otio_dir)
    return {
        "tool": "clipwright-overlay",
        "version": version,
        "kind": "image_overlay",
        "image_path": stored_path,
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
    fields to be rate-invariant. image_path comparison uses media_ref_for_otio to
    compute the same representation used at storage time (relative or absolute
    depending on whether the image is inside the output OTIO's parent directory).

    Args:
        marker: An existing image_overlay marker to compare.
        options: Current AddOverlayOptions to check for duplication.
        output: Output OTIO file path (for computing stored image_path).

    Returns:
        True if all fields match (complete duplicate -> no-op).
    """
    cw = marker.metadata.get("clipwright", {})
    if not isinstance(cw, collections.abc.Mapping):
        return False
    if cw.get("kind") != "image_overlay":
        return False

    # Compute the stored path for the new options using media_ref_for_otio
    try:
        otio_dir = Path(output).resolve().parent
        new_stored_path = media_ref_for_otio(options.image_path, otio_dir)
    except Exception:
        return False

    # String fields: exact match (image_path via media_ref_for_otio, x, y)
    if cw.get("image_path") != new_stored_path:
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
      3. output != timeline
      4. field validation (_validate_overlay_fields, including image_path 3-stage)
      5. load timeline (FILE_NOT_FOUND / OTIO_ERROR propagate)
      6. first TrackKind.Video track exists
      7. rate determination
      8. _MAX_IMAGE_OVERLAYS cap check (V2-9)
      9. idempotency check (exact duplicate -> no-op)
     10. add marker (image_{n}, all metadata fields, via media_ref_for_otio)
     11. save timeline atomically
     12. return ok_result

    Output boundary restriction removed (impl-overlay): output may be placed
    anywhere; only the output != source constraint is enforced.

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

    # --- Step 3: output != timeline ---
    # check_output_not_source raises PATH_NOT_ALLOWED when paths resolve equal.
    check_output_not_source(out, [timeline])

    # --- Step 4: field validation (value ranges + image_path 3-stage + x/y) ---
    _validate_overlay_fields(options, output)

    # --- Step 5: load timeline ---
    if not inp.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Timeline file not found: {inp.name}",
            hint="Verify the timeline path and ensure the file exists.",
        )
    timeline_obj = load_timeline(timeline)

    # --- Step 6: find first Video track ---
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

    # --- Step 7: rate determination ---
    rate, rate_warnings = _resolve_rate(video_track)

    # --- Step 8: _MAX_IMAGE_OVERLAYS cap (V2-9) ---
    existing_markers = get_markers(timeline_obj, kind="image_overlay")
    if len(existing_markers) >= _MAX_IMAGE_OVERLAYS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Too many image overlays on this timeline.",
            hint=(
                f"The timeline already has {len(existing_markers)} image overlays "
                f"and the maximum is {_MAX_IMAGE_OVERLAYS}. "
                f"Remove some image_overlay markers before adding more."
            ),
        )

    # --- Step 9: idempotency check ---
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

    # --- Step 10: add marker ---
    # Count existing image_overlay markers to determine name index
    n = len(existing_markers)
    marker_name = f"image_{n}"

    # Build marker metadata via media_ref_for_otio (relative or absolute)
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

    # --- Step 11: save timeline ---
    save_timeline(timeline_obj, output)

    # --- Step 12: build result ---
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

    Accumulate pattern: each distinct call appends image_0, image_1, ... markers.
    Output may be placed anywhere (not constrained to the input timeline's
    directory); only the restriction output != timeline is enforced.

    image_path storage follows media_ref_for_otio (ADR-PP-1):
      - inside output OTIO's parent directory -> relative POSIX path
      - outside -> absolute path (no '../' traversal)
    clipwright-render resolves both forms at materialisation time.

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
