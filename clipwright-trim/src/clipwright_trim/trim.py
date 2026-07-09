"""trim.py — orchestration layer for clipwright-trim.

Handles the full flow:
  output path validation -> inspect_media -> derive_keep_ranges -> OTIO build -> save.

This module is the sole owner of the ClipwrightError -> error_result boundary.
No business logic belongs in server.py; no error conversion in plan.py.

Design decisions:
- Output path validation runs BEFORE inspect_media (fast fail without spawning ffprobe).
  Order: (a) output==media (PATH_NOT_ALLOWED), (b) extension, (c) parent dir existence.
- The same-directory co-location constraint is removed; output may live in any directory
  whose parent already exists.
- OTIO media reference uses media_ref_for_otio: relative when media is under the OTIO
  dir, absolute otherwise (external reference).
- Error messages use Path.name (basename) only — no full path exposure (CWE-209).
"""

from __future__ import annotations

from pathlib import Path

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.pathpolicy import check_output_not_source, media_ref_for_otio
from clipwright.schemas import (
    MediaRef,
    RationalTimeModel,
    TimeRangeModel,
    ToolResult,
    full_media_range,
)

import clipwright_trim
from clipwright_trim.plan import derive_keep_ranges
from clipwright_trim.schemas import TrimOptions


def trim_media(media: str, output: str, options: TrimOptions) -> ToolResult:
    """Public entry point for the trim operation.

    Wraps _trim_inner; converts ClipwrightError to error_result at the boundary.
    This is the only place where ClipwrightError is caught and translated.

    Args:
        media: Input media file path.
        output: Output OTIO timeline file path (.otio extension required).
        options: TrimOptions controlling keep/drop ranges and padding.

    Returns:
        ToolResult with ok=True on success, ok=False with error on failure.
    """
    try:
        return _trim_inner(media, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording to
        # prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Trimming the media failed due to an internal error.",
            "Retry after verifying that the output directory is writable.",
        )


def _trim_inner(media: str, output: str, options: TrimOptions) -> ToolResult:
    """Internal implementation. Raises ClipwrightError directly on any failure.

    Flow:
      1. Output path validation (output==media, extension, parent dir) — before ffprobe.
      2. inspect_media (ffprobe) — raises on missing file or probe failure.
      3. derive_keep_ranges — pure interval arithmetic.
      4. Build OTIO timeline with keep Clips on V1.
      5. save_timeline — atomic write.
      6. Return ok_result envelope.
    """
    output_path = Path(output)
    media_path = Path(media)

    # ------------------------------------------------------------------
    # 1. Output path validation (runs before inspect_media — fast fail)
    # ------------------------------------------------------------------

    # output must not resolve to the same file as media (PATH_NOT_ALLOWED).
    # Checked first so the error code is consistent regardless of extension.
    check_output_not_source(output_path, [media])

    # Extension must be .otio
    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Invalid output file extension. Only .otio is allowed.",
            hint="Change the output file path extension to .otio.",
        )

    # Parent directory must exist
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    # ------------------------------------------------------------------
    # 2. inspect_media (ffprobe)
    # ------------------------------------------------------------------

    try:
        media_info = inspect_media(media)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {media_path.name}",
                hint="Check that the path is correct and the file exists.",
            ) from exc
        raise

    # ------------------------------------------------------------------
    # 3. Extract duration and rate; derive keep ranges
    # ------------------------------------------------------------------

    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message=f"Could not retrieve media duration: {media_path.name}",
            hint=(
                "Check that the media file is not corrupted. "
                "You can also verify manually with ffprobe."
            ),
        )

    duration_sec = media_info.duration.value / media_info.duration.rate
    rate = media_info.duration.rate

    keep_ranges, warnings, mode = derive_keep_ranges(duration_sec, options)

    # ------------------------------------------------------------------
    # 4. Build OTIO timeline (mirrors silence detect.py §4.1)
    # ------------------------------------------------------------------

    # Use media_ref_for_otio: relative when media is under the OTIO dir,
    # absolute otherwise (external reference, e.g. media in a different tree).
    otio_dir = output_path.parent
    abs_media = media_ref_for_otio(media, otio_dir)
    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]  # V1 (Video) track

    # ADR-3: available_range describes the whole media asset (0..duration),
    # never a keep clip's own source_range. Built once and reused for every
    # clip so downstream tools (render/speed) see the correct headroom.
    available_range = full_media_range(media_info)
    media_ref = MediaRef(target_url=abs_media, available_range=available_range)

    for start_sec, end_sec in keep_ranges:
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_sec * rate, rate=rate),
            duration=RationalTimeModel(value=(end_sec - start_sec) * rate, rate=rate),
        )
        add_clip(
            v1,
            media_ref,
            source_range,
            name="keep",
            metadata={
                "tool": "clipwright-trim",
                "version": clipwright_trim.__version__,
                "kind": "keep",
                "mode": mode,
            },
        )

    # ------------------------------------------------------------------
    # 5. Save timeline (atomic write via save_timeline)
    # ------------------------------------------------------------------

    save_timeline(timeline, output)

    # ------------------------------------------------------------------
    # 6. Build and return envelope (FR-8)
    # ------------------------------------------------------------------

    clip_count = len(keep_ranges)
    kept_duration_sec = sum(e - s for s, e in keep_ranges)
    summary = (
        f"Kept {clip_count} range(s) (total {kept_duration_sec:.1f}s) "
        f"from source duration {duration_sec:.1f}s ({mode} mode). "
        f"Generated {output_path.name}."
    )

    return ok_result(
        summary,
        data={
            "clip_count": clip_count,
            "kept_duration_sec": kept_duration_sec,
            "source_duration_sec": duration_sec,
            "mode": mode,
        },
        artifacts=[{"role": "timeline", "path": str(output), "format": "otio"}],
        warnings=warnings,
    )
