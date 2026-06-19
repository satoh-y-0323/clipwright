"""trim.py — orchestration layer for clipwright-trim.

Handles the full flow:
  output path validation -> inspect_media -> derive_keep_ranges -> OTIO build -> save.

This module is the sole owner of the ClipwrightError -> error_result boundary.
No business logic belongs in server.py; no error conversion in plan.py.

Design decisions:
- Output path validation (extension, parent dir, output==media) runs BEFORE
  inspect_media so a bad output path fails fast without spawning ffprobe.
- Same-directory check runs AFTER inspect_media (needs confirmed resolved paths).
- Error messages use Path.name (basename) only — no full path exposure (CWE-209).
"""

from __future__ import annotations

from pathlib import Path

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel, ToolResult

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


def _trim_inner(media: str, output: str, options: TrimOptions) -> ToolResult:
    """Internal implementation. Raises ClipwrightError directly on any failure.

    Flow:
      1. Output path validation (extension, parent dir, output==media) — before ffprobe.
      2. inspect_media (ffprobe) — raises on missing file or probe failure.
      3. Same-directory check (after inspect_media; needs resolved paths).
      4. derive_keep_ranges — pure interval arithmetic.
      5. Build OTIO timeline with keep Clips on V1.
      6. save_timeline — atomic write.
      7. Return ok_result envelope.
    """
    output_path = Path(output)
    media_path = Path(media)

    # ------------------------------------------------------------------
    # 1. Output path validation (runs before inspect_media — fast fail)
    # ------------------------------------------------------------------

    # Extension must be .otio
    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Invalid output file extension: {output_path.suffix!r}. "
                "Only .otio is allowed."
            ),
            hint="Change the output file path extension to .otio.",
        )

    # Parent directory must exist
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    # output must not equal media
    try:
        if output_path.resolve() == media_path.resolve():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Output path equals input media path.",
                hint="Choose an output path different from the media.",
            )
    except ClipwrightError:
        raise
    except OSError as os_err:
        # Fall back to string comparison when resolve() fails (network paths, etc.)
        if str(output_path) == str(media_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Output path equals input media path.",
                hint="Choose an output path different from the media.",
            ) from os_err

    # ------------------------------------------------------------------
    # 2. inspect_media (ffprobe)
    # ------------------------------------------------------------------

    # SR L-2: replace FILE_NOT_FOUND message with basename only (no full path)
    try:
        media_info = inspect_media(media)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {media_path.name}",
                hint=exc.hint,
            ) from exc
        raise

    # ------------------------------------------------------------------
    # 3. Same-directory check (after inspect_media; post-probe)
    # ------------------------------------------------------------------

    try:
        media_dir = media_path.resolve().parent
        output_dir = output_path.parent.resolve()
        if media_dir != output_dir:
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=(
                    "The output timeline must be placed in the same directory "
                    f"as the input media ({media_path.name})."
                ),
                hint=(
                    "Change the output path to be in the same directory as the media."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # Best-effort: skip on network paths where resolve() fails
        pass

    # ------------------------------------------------------------------
    # 4. Extract duration and rate; derive keep ranges
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

    keep_ranges, warnings = derive_keep_ranges(duration_sec, options)

    # Determine mode from options (used for metadata and data.mode)
    if options.keep:
        mode = "keep"
    elif options.drop:
        mode = "drop"
    else:
        # Both empty -> passthrough, report as keep mode
        mode = "keep"

    # ------------------------------------------------------------------
    # 5. Build OTIO timeline (mirrors silence detect.py §4.1)
    # ------------------------------------------------------------------

    abs_media = str(media_path.resolve())
    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]  # V1 (Video) track

    for start_sec, end_sec in keep_ranges:
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_sec * rate, rate=rate),
            duration=RationalTimeModel(value=(end_sec - start_sec) * rate, rate=rate),
        )
        add_clip(
            v1,
            MediaRef(target_url=abs_media),
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
    # 6. Save timeline (atomic write via save_timeline)
    # ------------------------------------------------------------------

    save_timeline(timeline, output)

    # ------------------------------------------------------------------
    # 7. Build and return envelope (FR-8)
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
