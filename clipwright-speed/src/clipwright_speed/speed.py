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
- NLE mirror-sync (ADR-MS-1/2/3): if the input timeline has been conformed for
  an NLE (Resolve_OTIO metadata present, see clipwright.nle_interop), each
  target V1 clip's linked Audio-track mirror clips (found via a single
  find_mirror_clips_batch call made once before the per-clip loop, rather
  than one find_mirror_clips call per clip -- CWE-400 hardening) receive a
  matching clipwright LinearTimeWarp -- same time_scalar, strip-then-append
  like the V1 clip itself -- so the round-trip through an NLE keeps picture
  and its linked audio mirrors in sync. Mirrors never have their
  source_range/available_range rewritten and never receive clip-level
  metadata["clipwright"] (effect-level metadata only, ADR-MS-3). A
  non-conform timeline yields zero mirrors and behaves exactly as before
  (ADR-MS-1 backward-compat pin).
"""

from __future__ import annotations

import collections.abc
from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.nle_interop import find_mirror_clips_batch
from clipwright.otio_utils import (
    get_clipwright_metadata,
    load_timeline,
    save_timeline,
    set_clipwright_metadata,
)
from clipwright.pathpolicy import check_output_not_source, validate_source_or_basename
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
    CR L-4: guard get_clipwright_metadata return value with isinstance(cw, Mapping)
    before calling .get() to handle AnyDictionary and non-dict return types.
    """
    if not isinstance(effect, otio.schema.LinearTimeWarp):
        return False
    cw = get_clipwright_metadata(effect)
    if not isinstance(cw, collections.abc.Mapping):
        return False
    return cw.get("kind") == "speed"


def _set_speed_inner(
    timeline: str,
    output: str,
    options: SetSpeedOptions,
) -> ToolResult:
    """Internal implementation of set_speed. Raises ClipwrightError on failure.

    Validation order:
      1. output suffix == .otio
      2. output parent exists
      3. output != timeline (PATH_NOT_ALLOWED via check_output_not_source)
      4. speed in [0.25, 8.0]
      5. load_timeline (FILE_NOT_FOUND / OTIO_ERROR propagate)
      6. first TrackKind.Video track exists
      7. select first Video track (raises UNSUPPORTED_OPERATION if none)
      8. build clip-only index space (gaps/transitions excluded)
      9. apply: remove old clipwright warp, append new, set metadata; then
         sync each target clip's linked Audio mirror clips the same way
         (ADR-MS-1/2, find_mirror_clips_batch called once before the loop --
         no-op on a non-conform timeline)
     10. save_timeline atomically
     11. return ok_result (data["mirrored_audio_clips_updated"], ADR-MS-5)

    Output may reside in any directory (transform tool: no co-location
    constraint).  check_output_not_source raises PATH_NOT_ALLOWED when
    output and timeline resolve to the same file, preserving DC-AM-003
    (mixed relative/absolute media refs survive the round-trip unchanged).
    """
    out = Path(output)
    inp = Path(timeline)

    # --- Step 1: output suffix validation (SR L-1: no raw suffix in message) ---
    if out.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path must have a .otio extension.",
            hint="Change the output file extension to .otio (e.g., 'result.otio').",
        )

    # --- Step 2: output parent exists (SR M-1: no path in message or hint) ---
    if not out.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="Output directory does not exist.",
            hint="Create the output directory before calling clipwright_set_speed.",
        )

    # --- Step 3: output must not resolve to the same file as the timeline ---
    # PATH_NOT_ALLOWED (not INVALID_INPUT) for consistent transform tool contract.
    check_output_not_source(out, [timeline])

    # --- Step 4: speed range validation (OQ-1) ---
    speed = options.speed
    if speed < _SPEED_MIN or speed > _SPEED_MAX:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Speed must be between {_SPEED_MIN} and {_SPEED_MAX} inclusive.",
            hint=f"Set speed within {_SPEED_MIN}-{_SPEED_MAX}.",
        )

    # --- Step 6: load timeline ---
    # ClipwrightError(FILE_NOT_FOUND / OTIO_ERROR) propagates to set_speed boundary.
    validate_source_or_basename(
        timeline,
        message=f"Timeline file not found: {inp.name}",
        hint="Verify the timeline path and ensure the file exists.",
    )
    timeline_obj = load_timeline(timeline)

    # --- Step 7: select first Video track ---
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

    # --- Step 8: build clip-only index space (gaps/transitions excluded) ---
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

    # --- Step 8b: resolve target clip indices ---
    clip_index = options.clip_index
    if clip_index is None:
        target_indices = list(range(len(clips)))
    else:
        max_index = len(clips) - 1
        if clip_index > max_index:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="clip_index is out of range for the video track.",
                hint=(
                    f"Provide a clip_index within 0-{max_index}, or omit it to "
                    "apply the speed change to all clips."
                ),
            )
        target_indices = [clip_index]

    # --- Step 9: per-target clip: remove old warp, append new, set metadata ---
    # Mirrors are looked up once for every target clip via find_mirror_clips_batch
    # (a single Audio-track scan) rather than once per clip via find_mirror_clips,
    # so the total cost stays O(V1 targets + Audio clips) instead of amplifying to
    # O(V1 targets x Audio clips) (CWE-400 hardening).
    mirrored_updated = 0
    mirror_lists = find_mirror_clips_batch(
        timeline_obj, [clips[i] for i in target_indices]
    )
    for idx, mirrors in zip(target_indices, mirror_lists, strict=True):
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

        # NLE mirror-sync (ADR-MS-1/2/3): sync every linked Audio-track mirror
        # clip (already resolved above via find_mirror_clips_batch) so it
        # carries the same speed warp as this V1 clip. A non-conform timeline
        # yields [] here (no Resolve_OTIO Link Group ID), so this is a pure
        # no-op in that case (backward-compat pin). Each mirror gets a
        # freshly-constructed LinearTimeWarp instance (never shared with the V1
        # clip's or another mirror's) and no clip-level metadata (ADR-MS-3):
        # range fields are never touched.
        for mirror in mirrors:
            mirror.effects[:] = [
                e for e in mirror.effects if not _is_clipwright_speed_warp(e)
            ]
            mirror_warp = otio.schema.LinearTimeWarp(
                name="clipwright_speed",
                time_scalar=speed,
            )
            set_clipwright_metadata(
                mirror_warp,
                {
                    "tool": "clipwright-speed",
                    "version": __version__,
                    "kind": "speed",
                    "speed": speed,
                },
            )
            mirror.effects.append(mirror_warp)
            mirrored_updated += 1

    # --- Step 10: save atomically; input file is never written ---
    save_timeline(timeline_obj, output)

    # --- Step 11: build result ---
    applied_count = len(target_indices)
    out_resolved = out.resolve()
    summary = (
        f"Applied speed {speed}x to {applied_count} clip(s). "
        f"Output: {out.name}. "
        f"Estimated rendered duration scales by 1/{speed}."
    )
    if mirrored_updated > 0:
        summary += (
            f" Retimed {mirrored_updated} linked audio mirror clip(s) to match "
            "(NLE sync)."
        )
    return ok_result(
        summary=summary,
        data={
            "applied_count": applied_count,
            "speed": speed,
            "clip_indices": target_indices,
            "mirrored_audio_clips_updated": mirrored_updated,
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
    the clipwright warp on each clip (and on each of its Audio mirror clips).

    clip_index is the clip-only index space (gaps/transitions excluded from
    indexing), matching render ordering. Sub-range speed is expressed by
    splitting the region into its own clip before calling (ADR-SP-1).
    speed=1.0 is a valid no-op-at-render annotation.

    NLE mirror-sync (ADR-MS-1/2/5): if the input timeline was conformed for an
    NLE (see clipwright.nle_interop.conform_timeline_for_nle), each target
    clip's linked Audio-track mirror clips also receive a matching clipwright
    LinearTimeWarp so picture and its audio mirrors stay in sync after a
    round-trip through the NLE. ``data["mirrored_audio_clips_updated"]``
    (int) always reports how many mirror clips were updated (0 for a
    non-conform timeline); the summary gains a trailing sentence about the
    sync only when that count is greater than 0.

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
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording to
        # prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Setting the clip speed failed due to an internal error.",
            "Retry after verifying that the output directory is writable.",
        )
