"""speed.py — clipwright-speed orchestration layer.

Handles the full flow: input validation -> load timeline -> apply LinearTimeWarp
-> save timeline -> envelope return.

Design decisions:
- _set_speed_inner() is the raising implementation; set_speed() is the public
  boundary that catches ClipwrightError and converts to error_result.
- Speed range (0.25-8.0) is validated manually inside _set_speed_inner (OQ-1).
- Idempotency (AC-4): any existing clipwright warp on a clip is replaced rather
  than stacked; a single clipwright LinearTimeWarp is maintained per clip.
- Foreign warps (non-clipwright LinearTimeWarp) are preserved (R-3).
- Non-destructive (AC-1): input file bytes are never modified.
- clip_index is the clip-only index space (gaps/transitions excluded), matching
  render ordering. Sub-range speed is expressed by splitting the region into its
  own clip before calling (ADR-SP-1). speed=1.0 is a valid no-op-at-render
  annotation.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import (
    get_clipwright_metadata,
    load_timeline,
    save_timeline,
    set_clipwright_metadata,
)
from clipwright.schemas import ToolResult

from clipwright_speed import __version__
from clipwright_speed.schemas import SetSpeedOptions

# Speed range boundaries (OQ-1: validated manually, not via Pydantic constraints)
_SPEED_MIN = 0.25
_SPEED_MAX = 8.0


def _is_clipwright_speed_warp(effect: object) -> bool:
    """Return True if effect is a clipwright-authored LinearTimeWarp for speed.

    ADR-SP-4 conservative predicate: removes only when the effect is a
    LinearTimeWarp AND the clip's clipwright metadata has kind == 'speed'.
    Foreign LinearTimeWarps (no clipwright metadata) are NOT removed (R-3).
    """
    if not isinstance(effect, otio.schema.LinearTimeWarp):
        return False
    cw = get_clipwright_metadata(effect)
    return cw.get("kind") == "speed"


def _set_speed_inner(
    timeline: str,
    output: str,
    options: SetSpeedOptions,
) -> ToolResult:
    """Internal implementation of set_speed. Raises ClipwrightError on failure.

    Validation order:
      1. output suffix == .otio; output parent exists; output != timeline
      2. speed in [0.25, 8.0]
      3. load_timeline (FILE_NOT_FOUND / OTIO_ERROR propagate)
      4. first TrackKind.Video track exists
      5. clip-only index space; clip_index range check
      6. apply: remove old clipwright warp, append new, set metadata
      7. save_timeline atomically
      8. return ok_result
    """
    out = Path(output)
    inp = Path(timeline)

    # --- Step 1: output path validation ---
    if out.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Output path must have a .otio extension, got: {out.suffix!r}",
            hint="Change the output file extension to .otio (e.g., 'result.otio').",
        )

    if not out.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Output directory does not exist: {out.parent}",
            hint="Create the output directory before calling clipwright_set_speed.",
        )

    if out.resolve() == inp.resolve():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path must differ from the input timeline path.",
            hint=(
                "Provide a distinct output path (e.g., append '_speed' before .otio)."
            ),
        )

    # --- Step 2: speed range validation (OQ-1) ---
    speed = options.speed
    if speed < _SPEED_MIN or speed > _SPEED_MAX:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Speed must be between {_SPEED_MIN} and {_SPEED_MAX} inclusive.",
            hint=f"Set speed within {_SPEED_MIN}-{_SPEED_MAX}.",
        )

    # --- Step 3: load timeline ---
    # ClipwrightError(FILE_NOT_FOUND / OTIO_ERROR) propagates to set_speed boundary.
    if not inp.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Timeline file not found: {inp.name}",
            hint="Verify the timeline path and ensure the file exists.",
        )
    timeline_obj = load_timeline(timeline)

    # --- Step 4: select first Video track ---
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
                "clipwright_set_speed requires at least one video track. "
                "Provide a timeline that includes a Video track."
            ),
        )

    # --- Step 5: build clip-only index space (gaps/transitions excluded) ---
    clips: list[otio.schema.Clip] = [
        item for item in video_track if isinstance(item, otio.schema.Clip)
    ]

    if not clips:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="No clips found in the video track.",
            hint=(
                "clipwright_set_speed requires at least one clip in the video track."
            ),
        )

    # --- Step 5b: resolve target clip indices ---
    clip_index = options.clip_index
    if clip_index is None:
        target_indices = list(range(len(clips)))
    else:
        max_index = len(clips) - 1
        if clip_index > max_index:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"clip_index {clip_index} is out of range for a track with "
                    f"{len(clips)} clip(s)."
                ),
                hint=(
                    f"Provide a clip_index within 0-{max_index}, or omit it to "
                    "apply the speed change to all clips."
                ),
            )
        target_indices = [clip_index]

    # --- Step 6: per-target clip: remove old warp, append new, set metadata ---
    for idx in target_indices:
        clip = clips[idx]

        # Remove existing clipwright-authored LinearTimeWarp (ADR-SP-4 predicate).
        # Foreign LinearTimeWarps are preserved (R-3).
        clip.effects[:] = [e for e in clip.effects if not _is_clipwright_speed_warp(e)]

        # Append new LinearTimeWarp.
        new_warp = otio.schema.LinearTimeWarp(
            name="clipwright_speed",
            time_scalar=speed,
        )
        set_clipwright_metadata(
            new_warp,
            {
                "tool": "clipwright-speed",
                "version": __version__,
                "kind": "speed",
                "speed": speed,
            },
        )
        clip.effects.append(new_warp)

        # Record clipwright metadata on the clip itself (convention §4.3).
        set_clipwright_metadata(
            clip,
            {
                "tool": "clipwright-speed",
                "version": __version__,
                "kind": "speed",
                "speed": speed,
            },
        )

    # --- Step 7: save atomically; input file is never written ---
    save_timeline(timeline_obj, output)

    # --- Step 8: build result ---
    applied_count = len(target_indices)
    out_resolved = out.resolve()
    summary = (
        f"Applied speed {speed}x to {applied_count} clip(s). "
        f"Output: {out.name}. "
        f"Estimated rendered duration scales by 1/{speed}."
    )
    return ok_result(
        summary=summary,
        data={
            "applied_count": applied_count,
            "speed": speed,
            "clip_indices": target_indices,
        },
        artifacts=[
            {
                "role": "timeline",
                "path": str(out_resolved),
                "format": "otio",
            }
        ],
    )


def set_speed(
    timeline: str,
    output: str,
    options: SetSpeedOptions,
) -> ToolResult:
    """Apply a LinearTimeWarp speed change to clips in an OTIO timeline.

    Non-destructive: does not modify the input timeline file.
    Idempotent: applying twice with the same speed replaces rather than stacks
    the clipwright warp on each clip.

    clip_index is the clip-only index space (gaps/transitions excluded from
    indexing), matching render ordering. Sub-range speed is expressed by
    splitting the region into its own clip before calling (ADR-SP-1).
    speed=1.0 is a valid no-op-at-render annotation.

    Args:
        timeline: Input OTIO timeline file path.
        output: Output OTIO file path (must end in .otio, must differ from timeline).
        options: SetSpeedOptions with required speed and optional clip_index.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _set_speed_inner(timeline, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
