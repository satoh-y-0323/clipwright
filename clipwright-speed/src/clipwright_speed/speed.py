"""speed.py — clipwright-speed orchestration layer (stub).

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
"""

from __future__ import annotations

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError
from clipwright.schemas import ToolResult

from clipwright_speed.schemas import SetSpeedOptions


def _set_speed_inner(
    timeline: str,
    output: str,
    options: SetSpeedOptions,
) -> ToolResult:
    """Internal implementation of set_speed. Raises ClipwrightError directly.

    Not yet implemented — stub raises NotImplementedError so tests fail for
    the correct reason (feature not implemented, not broken imports).
    """
    raise NotImplementedError("_set_speed_inner is not yet implemented")


def set_speed(
    timeline: str,
    output: str,
    options: SetSpeedOptions,
) -> ToolResult:
    """Apply a LinearTimeWarp speed change to clips in an OTIO timeline.

    Non-destructive: does not modify the input timeline file.
    Idempotent: applying twice with the same speed replaces rather than stacks
    the clipwright warp.

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
