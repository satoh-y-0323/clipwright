"""extract.py — clipwright-frames orchestration layer.

Handles the full flow: input validation -> inspect_media -> mode dispatch
-> ffmpeg execution -> OTIO/JSON output -> envelope return.

Design decisions:
- _extract_frames_inner() is the raising implementation; extract_frames() is
  the public boundary that catches ClipwrightError and converts to error_result.
- subprocess errors are sanitised with safe_subprocess_message() before reaching
  the MCP error envelope (mirrors detect.py pattern).
- Error messages expose basename only (CWE-209 path disclosure prevention).
- build_single_frame_command returns list[str|float]; all elements are str()-ified
  before passing to run() so that subprocess receives only strings.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import (
    add_marker,
    get_markers,
    load_timeline,
    new_timeline,
    save_timeline,
)
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import RationalTimeModel, TimeRangeModel, ToolResult

from clipwright_frames.plan import (
    build_fps_command,
    build_single_frame_command,
    compute_timestamps_mode,
    frame_filename,
    scene_marker_seconds,
)
from clipwright_frames.schemas import ExtractFramesOptions


def _extract_frames_inner(
    media: str,
    output_dir: str,
    options: ExtractFramesOptions,
) -> ToolResult:
    """Internal implementation of extract_frames. Raises ClipwrightError directly."""
    media_path = Path(media)
    out_dir = Path(output_dir)

    # --- 1. output_dir validation (must be an existing directory; no auto-create) ---

    if not out_dir.exists() or not out_dir.is_dir():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The output directory does not exist or is not a directory.",
            hint="Create the output directory first, then re-run.",
        )

    # --- 2. inspect_media -> MediaInfo ---

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

    # --- 3. has_video validation ---

    has_video = any(s.codec_type == "video" for s in media_info.streams)
    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint=(
                "Frame extraction requires a video stream. "
                "Specify a media file that contains video."
            ),
        )

    # --- 4. duration validation ---

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

    # --- 5. Mode dispatch ---

    warnings: list[str] = []

    # List of (index, timestamp_sec) pairs describing successfully extracted frames.
    extracted_frames: list[tuple[int, float]] = []

    ffmpeg = resolve_tool("ffmpeg", env_var="CLIPWRIGHT_FFMPEG")
    abs_media = str(media_path.resolve())
    ext = "jpg" if options.format == "jpeg" else "png"

    if options.mode == "interval":
        interval_sec = options.interval_sec

        if interval_sec > duration_sec:
            warnings.append(
                f"interval_sec ({interval_sec}s) exceeds media duration "
                f"({duration_sec:.2f}s). No frames extracted."
            )
        else:
            # Build fps-filter command; output pattern uses frame_%05d.<ext>
            out_pattern = str(out_dir / f"frame_%05d.{ext}")
            cmd = build_fps_command(ffmpeg, abs_media, out_pattern, options)
            timeout = float(max(60, math.ceil(duration_sec * 2)))
            _run_with_safe_error(cmd, timeout)

            # Discover produced files and map index -> timestamp
            produced = sorted(out_dir.glob(f"frame_?????.{ext}"))
            for idx, _fpath in enumerate(produced):
                ts = float(idx) * interval_sec
                extracted_frames.append((idx, ts))

    elif options.mode == "scene":
        # --- scene_timeline validation ---
        scene_timeline = options.scene_timeline
        if scene_timeline is None:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="scene_timeline is required when mode='scene'.",
                hint="Provide the path to an .otio timeline file.",
            )
        scene_path = Path(scene_timeline)
        if scene_path.suffix.lower() != ".otio":
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"Invalid scene_timeline extension: {scene_path.suffix!r}. "
                    "Only .otio is allowed."
                ),
                hint="Change the scene_timeline file path extension to .otio.",
            )
        if not scene_path.exists():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=f"scene_timeline file not found: {scene_path.name}",
                hint="Specify a valid .otio timeline file path.",
            )

        # Load OTIO (OTIO_ERROR propagated as-is)
        tl = load_timeline(scene_timeline)

        # Extract scene_boundary marker timestamps
        markers = get_markers(tl, kind="scene_boundary")
        scene_ts_list = scene_marker_seconds(markers)

        if not scene_ts_list:
            warnings.append(
                "No scene_boundary markers found in the timeline. No frames extracted."
            )
        else:
            timeout = float(max(60, math.ceil(duration_sec * 2)))
            for idx, ts in enumerate(scene_ts_list):
                out_path = str(out_dir / frame_filename(idx, options.format))
                raw_cmd = build_single_frame_command(
                    ffmpeg, abs_media, ts, out_path, options
                )
                str_cmd = [str(x) for x in raw_cmd]
                _run_with_safe_error(str_cmd, timeout)
                extracted_frames.append((idx, ts))

    else:
        # mode == "timestamps"
        kept, skipped = compute_timestamps_mode(options.timestamps, duration_sec)

        if skipped:
            skipped_str = ", ".join(f"{s}s" for s in skipped)
            warnings.append(
                f"Skipped {len(skipped)} out-of-range timestamp(s): {skipped_str}."
            )

        if kept:
            timeout = float(max(60, math.ceil(duration_sec * 2)))
            for idx, ts in enumerate(kept):
                out_path = str(out_dir / frame_filename(idx, options.format))
                raw_cmd = build_single_frame_command(
                    ffmpeg, abs_media, ts, out_path, options
                )
                str_cmd = [str(x) for x in raw_cmd]
                _run_with_safe_error(str_cmd, timeout)
                extracted_frames.append((idx, ts))

    # --- 7. Write frames.otio ---

    frames_otio_path = out_dir / "frames.otio"
    frames_timeline = new_timeline(name=f"{media_path.stem} — frames")
    v1 = frames_timeline.tracks[0]

    for idx, ts in extracted_frames:
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=ts * rate, rate=rate),
            duration=RationalTimeModel(value=0.0, rate=rate),
        )
        add_marker(
            v1,
            marked_range=marked_range,
            name=f"frame_{idx:05d}",
            color="CYAN",
            metadata={
                "kind": "extracted_frame",
                "timestamp_sec": float(ts),
            },
        )

    save_timeline(frames_timeline, str(frames_otio_path))

    # --- 8. Write frames.json ---

    frames_json_path = out_dir / "frames.json"
    frame_entries: list[dict[str, object]] = []
    for idx, ts in extracted_frames:
        frame_path = (out_dir / frame_filename(idx, options.format)).resolve()
        frame_entries.append(
            {
                "index": idx,
                "timestamp_sec": float(ts),
                "path": str(frame_path),
            }
        )

    manifest = {
        "count": len(extracted_frames),
        "mode": options.mode,
        "format": options.format,
        "frames": frame_entries,
    }
    frames_json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # --- 9. Build return envelope ---

    frame_count = len(extracted_frames)
    summary = (
        f"Extracted {frame_count} frame(s) from {media_path.name} "
        f"in {options.mode} mode (format={options.format}). "
        f"Output directory: {out_dir.name}."
    )

    return ok_result(
        summary,
        data={
            "frame_count": frame_count,
            "mode": options.mode,
            "format": options.format,
        },
        artifacts=[
            {
                "role": "timeline",
                "path": str(frames_otio_path.resolve()),
                "format": "otio",
            },
            {
                "role": "manifest",
                "path": str(frames_json_path.resolve()),
                "format": "json",
            },
        ],
        warnings=warnings if warnings else None,
    )


def _run_with_safe_error(cmd: list[str], timeout: float) -> None:
    """Run a subprocess command; re-raise SUBPROCESS_FAILED/TIMEOUT with safe message.

    Mirrors the pattern in detect.py _detect_with_ffmpeg().
    """
    try:
        run(cmd, timeout=timeout)
    except ClipwrightError as exc:
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            raise ClipwrightError(
                code=exc.code,
                message=safe_subprocess_message(exc),
                hint=exc.hint,
            ) from exc
        raise


def extract_frames(
    media: str,
    output_dir: str,
    options: ExtractFramesOptions,
) -> ToolResult:
    """Extract still frames from a video file; write an OTIO timeline + JSON manifest.

    Non-destructive: does not modify the input media file.
    Each extracted frame is recorded as a zero-duration OTIO Marker on the V1 track.

    Args:
        media: Input video file path.
        output_dir: Existing directory where frames and artifacts are written.
            The directory must already exist (no auto-creation).
        options: ExtractFramesOptions controlling mode, format, quality, etc.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _extract_frames_inner(media, output_dir, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
