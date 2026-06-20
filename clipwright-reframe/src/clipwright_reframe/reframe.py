"""reframe.py — clipwright-reframe orchestration layer (architecture-report §1.1).

Flow (3-layer: public API → _inner → error boundary):
  1. Output validation (extension, parent dir, output != media, output != timeline)
  2. inspect_media: require video stream
  3. Timeline resolution (None -> create new / path -> load + validate)
  4. Annotate ReframeDirective to timeline metadata["clipwright"]["reframe"]
  5. save_timeline (atomic)
  6. ok_result with summary / data / artifacts

Design decisions:
- Non-destructive: input media and OTIO are never modified.
- Directive dict shape is the only contract between this package and clipwright-render.
  render's reader-side _RenderReframe validates independently (defence-in-depth).
- W2a will implement the full body; this scaffold provides the 3-layer skeleton
  with error boundary so `import clipwright_reframe` and server startup both work.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import (
    get_clipwright_metadata,
    load_timeline,
    new_timeline,
    save_timeline,
    set_clipwright_metadata,
)
from clipwright.schemas import ToolResult

import clipwright_reframe
from clipwright_reframe.schemas import ReframeDirective, ReframeOptions


def reframe(
    media: str,
    output: str,
    options: ReframeOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for reframe annotation. Converts ClipwrightError to ok=False envelope.

    Args:
        media: Input video file path (video stream required).
        output: Output OTIO timeline file path (.otio, same directory as media).
        options: ReframeOptions (target resolution, mode, anchor, pad_color).
        timeline: Existing timeline path (None = create new).

    Returns:
        ok_result or error_result ToolResult.
    """
    try:
        return _reframe_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _reframe_inner(
    media: str,
    output: str,
    options: ReframeOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of reframe. Raises ClipwrightError directly.

    W2a will replace the TODO body with full logic.
    Scaffold: validates paths and returns a minimal ok_result for import smoke tests.
    """
    media_path = Path(media)
    output_path = Path(output)

    # --- 1. Output validation ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output file must use the .otio extension.",
            hint="Set the output file extension to .otio.",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    if _same_path(output_path, media_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input media path are the same.",
            hint="Change the output file path to differ from the input media.",
        )

    if timeline is not None and _same_path(output_path, Path(timeline)):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input timeline path are the same.",
            hint="Change the output file path to differ from the input timeline.",
        )

    # --- 2. inspect_media: video required ---

    if not media_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {media_path.name}",
            hint="Check that the input media file path is correct.",
        )

    media_info = inspect_media(media)
    has_video = any(s.codec_type == "video" for s in media_info.streams)

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint="Provide a media file that contains a video stream.",
        )

    duration_sec: float = 0.0
    if media_info.duration is not None:
        duration_sec = media_info.duration.value / media_info.duration.rate

    # --- 3. Timeline resolution ---

    if timeline is None:
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration)
    else:
        # D1: timeline existence check before load (B-5).
        # FileNotFoundError from load_timeline must not escape as a raw exception.
        if not Path(timeline).exists():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=f"Timeline file not found: {Path(timeline).name}",
                hint=(
                    "Specify an existing .otio timeline file or omit"
                    " the timeline argument."
                ),
            )
        # D1: invalid .otio content must be wrapped as ClipwrightError (B-6).
        # load_timeline wraps OTIOError, but OTIO may raise ValueError for JSON parse
        # errors on some adapters; catch it here and convert to OTIO_ERROR.
        try:
            tl = load_timeline(timeline)
        except ClipwrightError:
            raise
        except (ValueError, OSError) as exc:
            raise ClipwrightError(
                code=ErrorCode.OTIO_ERROR,
                message=f"Failed to load OTIO file: {Path(timeline).name}",
                hint="Specify a valid .otio timeline file.",
            ) from exc

    # --- 4. Annotate ReframeDirective ---

    # TODO (W2a): add full validation and idempotency checks here.
    directive = ReframeDirective(
        tool="clipwright-reframe",
        version=clipwright_reframe.__version__,
        kind="reframe",
        target_w=options.target_w,
        target_h=options.target_h,
        mode=options.mode,
        anchor=options.anchor,
        pad_color=options.pad_color,
    )

    existing_meta = get_clipwright_metadata(tl)
    existing_meta["reframe"] = directive.model_dump()
    set_clipwright_metadata(tl, existing_meta)

    # --- 5. save_timeline (atomic) ---

    save_timeline(tl, str(output_path))

    # --- 6. ok_result ---

    summary = (
        f"Reframe directive written for {media_path.name}."
        f" target={options.target_w}x{options.target_h}"
        f" mode={options.mode} anchor={options.anchor}."
        f" Directive saved to {output_path.name}."
    )

    return ok_result(
        summary,
        data={
            "target_w": options.target_w,
            "target_h": options.target_h,
            "mode": options.mode,
            "anchor": options.anchor,
            "pad_color": options.pad_color,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=[],
    )


def _same_path(a: Path, b: Path) -> bool:
    """Return True if both paths refer to the same entity.

    Falls back to string comparison on OSError.
    """
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _add_full_clip(
    tl: otio.schema.Timeline,
    media_path: Path,
    duration_sec: float,
    duration_rt: object | None,
) -> None:
    """Add one full-length keep clip to V1/A1 tracks of the timeline (new creation).

    target_url is set to the absolute path of media_path.resolve().
    """
    try:
        target_url = str(media_path.resolve())
    except OSError:
        target_url = str(media_path.absolute())

    # duration_rt is a RationalTimeModel from clipwright.schemas (has .rate attribute)
    rate = getattr(duration_rt, "rate", None) or 1000.0

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    ref = otio.schema.ExternalReference(target_url=target_url)

    for track in tl.tracks:
        clip = otio.schema.Clip(
            name=media_path.name,
            media_reference=ref,
            source_range=source_range,
        )
        track.append(clip)
