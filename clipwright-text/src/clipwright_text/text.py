"""text.py — clipwright-text orchestration layer.

Handles the full flow: input validation -> load timeline -> validate options
-> idempotency check -> add text_overlay marker -> save timeline -> return envelope.

Design decisions:
- _add_text_inner() is the raising implementation; add_text() is the public
  boundary that catches ClipwrightError and converts to error_result.
- Value-range validation is performed manually (OQ-1) for precise error hints.
- Color allowlist ^[A-Za-z0-9#@._-]+$ prevents filtergraph injection (FR-5/ADR-T7).
- Idempotency: exact duplicate (all metadata fields match) -> no-op with warning.
- Non-destructive: input OTIO bytes are never modified; output is always new.
- Rate determination (OQ-2): first clip source_range -> existing text_overlay
  marker rate -> fallback 1000.0 with warning.
- Boundary check uses pathpolicy.check_output_not_source (core helper).
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
from clipwright.pathpolicy import check_output_not_source
from clipwright.schemas import RationalTimeModel, TimeRangeModel, ToolResult

from clipwright_text import __version__
from clipwright_text.schemas import AddTextOptions

# ---------------------------------------------------------------------------
# Color allowlist (shared with clipwright-render; keep in sync — ADR-T4/ADR-T7)
# Permits: named colors, #RRGGBB, #RRGGBBAA, name@alpha.
# Excludes: spaces, single-quotes, colon, comma (filtergraph separators).
# clipwright-render counterpart: _COLOR_ALLOWLIST_RE in plan.py
# ---------------------------------------------------------------------------
_COLOR_PATTERN = re.compile(r"^[A-Za-z0-9#@._-]+$")

# Control-character pattern for text / position expressions / font_path.
# Includes: NUL-US (\x00-\x1f), DEL (\x7f), and line terminators (\n, \r).
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")

# Tolerance for float comparison in idempotency checks (rate-invariant).
# Keep in sync with _is_duplicate_overlay float comparison logic.
_IDEMPOTENCY_EPS: float = 1e-6


# ===========================================================================
# Validation helpers
# ===========================================================================


def _validate_text_overlay_fields(options: AddTextOptions) -> None:
    """Validate value-range, text content, color, and position expression fields.

    Validation order (fixed to keep error messages deterministic — ADR-T4):
      1. Value ranges (start_sec, duration_sec, font_size, fade_in_sec, fade_out_sec,
         fade sum)
      2. text content (empty, control characters)
      3. Color allowlist (font_color, box_color)
      4. Position expression control characters (x, y)

    All violations raise ClipwrightError(INVALID_INPUT).

    Args:
        options: AddTextOptions to validate.

    Raises:
        ClipwrightError: INVALID_INPUT on the first validation failure.
    """
    # --- 1. Value ranges ---
    if options.start_sec < 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Text start time must be 0 or greater.",
            hint="Set start_sec to a non-negative value.",
        )
    if options.duration_sec <= 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Text duration must be greater than 0.",
            hint="Set duration_sec to a positive number of seconds.",
        )
    if options.font_size <= 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Font size must be a positive integer.",
            hint="Set font_size to a value greater than 0 (e.g. 48).",
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
            message="Fade-in plus fade-out exceeds the text duration.",
            hint=(
                "Reduce fade durations or increase duration_sec so fades fit within it."
            ),
        )

    # --- 2. text content ---
    if not options.text.strip():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Text must not be empty or whitespace-only.",
            hint="Provide a non-empty text string to display.",
        )
    if _CONTROL_CHAR_PATTERN.search(options.text):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Text must not contain newlines or control characters.",
            hint=(
                "Remove line breaks and control characters; this version "
                "supports single-line overlays only."
            ),
        )

    # --- 3. Color allowlist (font_color, box_color) ---
    if not _COLOR_PATTERN.match(options.font_color):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Color value is not in the allowed format.",
            hint=(
                "Use a named color, #RRGGBB, or name@alpha "
                "(e.g. white, #FFCC00, black@0.5)."
            ),
        )
    if not _COLOR_PATTERN.match(options.box_color):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Color value is not in the allowed format.",
            hint=(
                "Use a named color, #RRGGBB, or name@alpha "
                "(e.g. white, #FFCC00, black@0.5)."
            ),
        )

    # --- 4. Position expression control characters (x, y) ---
    _pos_msg = "Position expression must not contain newlines or control characters."
    _pos_hint = "Provide a single-line ffmpeg drawtext expression for x/y."
    if _CONTROL_CHAR_PATTERN.search(options.x):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=_pos_msg,
            hint=_pos_hint,
        )
    if _CONTROL_CHAR_PATTERN.search(options.y):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=_pos_msg,
            hint=_pos_hint,
        )

    # --- 5. font_path: single-quote, newline, control characters ---
    # Keep in sync with render-side _marker_to_text_overlay font_path validator
    # (same rules: _CONTROL_CHAR_PATTERN + explicit single-quote check).
    if options.font_path is not None:
        if "'" in options.font_path:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Font path must not contain single-quote characters.",
                hint=(
                    "Remove single-quotes from the font file path "
                    "(they would corrupt filtergraph fontfile='...' quoting)."
                ),
            )
        if _CONTROL_CHAR_PATTERN.search(options.font_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Font path must not contain newlines or control characters.",
                hint="Remove newlines and control characters from the font file path.",
            )


# ===========================================================================
# Idempotency helpers
# ===========================================================================


def _overlay_metadata_dict(options: AddTextOptions) -> dict[str, Any]:
    """Build the clipwright metadata dict for a text_overlay marker.

    Stores tool/version/kind plus all AddTextOptions fields so that
    clipwright-render can reconstruct the overlay from the marker alone.

    Args:
        options: Validated AddTextOptions.

    Returns:
        Dict to store under marker.metadata["clipwright"].
    """
    return {
        "tool": "clipwright-text",
        "version": __version__,
        "kind": "text_overlay",
        "text": options.text,
        "start_sec": options.start_sec,
        "duration_sec": options.duration_sec,
        "x": options.x,
        "y": options.y,
        "font_size": options.font_size,
        "font_color": options.font_color,
        "box": options.box,
        "box_color": options.box_color,
        "fade_in_sec": options.fade_in_sec,
        "fade_out_sec": options.fade_out_sec,
        "font_path": options.font_path,
    }


def _is_duplicate_overlay(marker: otio.schema.Marker, options: AddTextOptions) -> bool:
    """Return True if marker is an exact duplicate of the given options.

    Compares all AddTextOptions fields stored in marker.metadata["clipwright"]
    against the current options. Uses approximate float comparison for seconds
    fields to be rate-invariant (ADR-T1).

    Args:
        marker: An existing text_overlay marker to compare.
        options: Current AddTextOptions to check for duplication.

    Returns:
        True if all fields match (complete duplicate -> no-op).
    """
    cw = marker.metadata.get("clipwright", {})
    if not isinstance(cw, collections.abc.Mapping):
        return False
    if cw.get("kind") != "text_overlay":
        return False

    # String / bool / int fields: exact match
    if cw.get("text") != options.text:
        return False
    if cw.get("x") != options.x:
        return False
    if cw.get("y") != options.y:
        return False
    if cw.get("font_size") != options.font_size:
        return False
    if cw.get("font_color") != options.font_color:
        return False
    if cw.get("box") != options.box:
        return False
    if cw.get("box_color") != options.box_color:
        return False
    if cw.get("font_path") != options.font_path:
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
    if not _approx_eq(cw.get("fade_in_sec"), options.fade_in_sec):
        return False
    return _approx_eq(cw.get("fade_out_sec"), options.fade_out_sec)


# ===========================================================================
# Rate resolution (OQ-2)
# ===========================================================================


def _resolve_rate(
    video_track: otio.schema.Track,
) -> tuple[float, list[str]]:
    """Determine the rate for RationalTime construction (OQ-2 priority order).

    Priority:
      1. source_range.rate of the first Clip in the V1 track.
      2. marked_range.start_time.rate of the first existing text_overlay marker.
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

    # Priority 2: existing text_overlay marker rate
    for marker in video_track.markers:
        cw = marker.metadata.get("clipwright", {})
        if isinstance(cw, collections.abc.Mapping) and cw.get("kind") == "text_overlay":
            return float(marker.marked_range.start_time.rate), []

    # Priority 3: fallback
    return 1000.0, [
        "Could not determine timeline rate from clips or existing markers; "
        "using fallback rate 1000.0. Consider providing a timeline with clips."
    ]


# ===========================================================================
# Core implementation
# ===========================================================================


def _add_text_inner(
    timeline: str,
    output: str,
    options: AddTextOptions,
) -> ToolResult:
    """Internal implementation of add_text. Raises ClipwrightError on failure.

    Validation order:
      1. output suffix == .otio
      2. output parent directory exists
      3. output != timeline (PATH_NOT_ALLOWED via check_output_not_source)
      4. field validation (_validate_text_overlay_fields)
      5. load timeline (FILE_NOT_FOUND / OTIO_ERROR propagate)
      6. first TrackKind.Video track exists
      7. rate determination (OQ-2)
      8. idempotency check (exact duplicate -> no-op)
      9. add marker (text_{n}, all metadata fields)
     10. save timeline atomically
     11. return ok_result

    Output may reside in any directory (transform tool: no co-location
    constraint).  check_output_not_source raises PATH_NOT_ALLOWED when
    output and timeline resolve to the same file.

    Args:
        timeline: Input OTIO timeline file path.
        output: Output OTIO file path.
        options: Validated AddTextOptions.

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
            hint="Create the output directory before calling clipwright_add_text.",
        )

    # --- Step 3: output must not resolve to the same file as the timeline ---
    # PATH_NOT_ALLOWED (not INVALID_INPUT) for consistent transform tool contract.
    check_output_not_source(out, [timeline])

    # --- Step 4: field validation ---
    _validate_text_overlay_fields(options)

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
                "clipwright_add_text requires a timeline with at least one "
                "video track to attach text overlay markers."
            ),
        )

    # --- Step 8: rate determination (OQ-2) ---
    rate, rate_warnings = _resolve_rate(video_track)

    # --- Step 9: idempotency check ---
    existing_markers = get_markers(timeline_obj, kind="text_overlay")
    for existing in existing_markers:
        if _is_duplicate_overlay(existing, options):
            # Exact duplicate: save a copy of the timeline and return no-op result
            save_timeline(timeline_obj, output)
            overlay_count = len(existing_markers)
            return ok_result(
                summary=(
                    f'Text overlay "{options.text}" at {options.start_sec}s for '
                    f"{options.duration_sec}s already exists; no marker added. "
                    f"Timeline has {overlay_count} text overlay(s). "
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
                warnings=["Identical text overlay already exists; no marker added."],
            )

    # --- Step 10: add marker ---
    # Count existing text_overlay markers to determine name index
    n = len(existing_markers)
    marker_name = f"text_{n}"

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
        metadata=_overlay_metadata_dict(options),
    )

    # --- Step 11: save timeline ---
    save_timeline(timeline_obj, output)

    # --- Step 12: build result ---
    overlay_count = n + 1
    out_resolved = out.resolve()
    text_preview = options.text[:40] + "..." if len(options.text) > 40 else options.text
    summary = (
        f'Added text overlay "{text_preview}" at {options.start_sec}s for '
        f"{options.duration_sec}s. "
        f"Timeline now has {overlay_count} text overlay(s). "
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


def add_text(
    timeline: str,
    output: str,
    options: AddTextOptions | None,
) -> ToolResult:
    """Add a text_overlay marker to an OTIO timeline.

    Non-destructive: does not modify the input timeline file.
    Idempotent: calling with the same options on an already-annotated timeline
    produces applied=0 and a warning rather than duplicating the marker.

    Args:
        timeline: Input OTIO timeline file path.
        output: Output OTIO file path (must end in .otio, must differ from timeline).
        options: AddTextOptions with required text/start_sec/duration_sec and
            optional style fields. None returns INVALID_INPUT.

    Returns:
        ToolResult from ok_result or error_result.
    """
    if options is None:
        return error_result(
            "INVALID_INPUT",
            "options is required but was not provided.",
            "Pass an AddTextOptions with at least text, start_sec, and duration_sec.",
        )
    try:
        return _add_text_inner(timeline, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording
        # to prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Adding the text overlay failed due to an internal error.",
            "Retry after verifying that the output directory is writable.",
        )
