"""extract.py — clipwright-frames orchestration layer.

Handles the full flow: input validation -> inspect_media -> mode dispatch
-> ffmpeg execution -> OTIO/JSON output -> envelope return.

Design decisions:
- _extract_frames_inner() is the raising implementation; extract_frames() is
  the public boundary that catches ClipwrightError and converts to error_result.
- subprocess errors are sanitised with safe_subprocess_message() before reaching
  the MCP error envelope (mirrors detect.py pattern).
- Error messages expose basename only (CWE-209 path disclosure prevention).
- build_single_frame_command returns list[str]; run() receives the list directly
  without any str() conversion in this module.
- artifact containment is enforced via clipwright.pathpolicy.check_within_boundary;
  scene_timeline is a read-only input and is NOT required to be inside output_dir.
"""

from __future__ import annotations

import json
import math
import os
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
from clipwright.pathpolicy import check_within_boundary
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import RationalTimeModel, TimeRangeModel, ToolResult

from clipwright_frames.plan import (
    build_single_frame_command,
    compute_interval_timestamps,
    compute_scene_segment_timestamps,
    compute_timestamps_mode,
    frame_filename,
    scene_marker_seconds,
)
from clipwright_frames.schemas import ExtractFramesOptions

# Clamp applied before timeout multiplication to prevent OverflowError on
# extreme ffprobe-derived duration values (CWE-400 / SR-V-001).
# Mirrors clipwright-reframe _MAX_TIMEOUT_DURATION_S (10 years).
_MAX_TIMEOUT_DURATION_S: float = 315_360_000.0


def _write_frames_otio(
    out_dir: Path,
    media_path: Path,
    extracted_frames: list[tuple[int, float]],
    rate: float,
) -> Path:
    """Write frames.otio with zero-duration markers for each extracted frame.

    Each marker carries metadata['clipwright']['kind']='extracted_frame' and
    metadata['clipwright']['timestamp_sec']. Uses save_timeline (atomic).

    Args:
        out_dir: Directory to write frames.otio.
        media_path: Source media path (stem used for timeline name).
        extracted_frames: List of (index, timestamp_sec) pairs.
        rate: Frame rate used for RationalTime construction.

    Returns:
        Resolved Path to the written frames.otio file.
    """
    frames_otio_path = out_dir / "frames.otio"
    frames_timeline = new_timeline(name=f"{media_path.stem} - frames")
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
    return frames_otio_path


def _write_frames_json(
    out_dir: Path,
    options: ExtractFramesOptions,
    extracted_frames: list[tuple[int, float]],
) -> Path:
    """Write frames.json manifest atomically (temp file + os.replace).

    Partial/interrupted writes do not leave a corrupt JSON file because the
    content is first written to a sibling temp file and then renamed.

    Args:
        out_dir: Directory to write frames.json (and temp file).
        options: Extraction options (mode, format).
        extracted_frames: List of (index, timestamp_sec) pairs.

    Returns:
        Resolved Path to the written frames.json file.
    """
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

    manifest: dict[str, object] = {
        "count": len(extracted_frames),
        "mode": options.mode,
        "format": options.format,
        "frames": frame_entries,
    }
    if options.mode == "scene":
        # CR-NEW: persist scene_sample so AI can later determine which strategy
        # produced this manifest (midpoint/start/boundary) without MCP round-trip.
        manifest["scene_sample"] = options.scene_sample

    tmp_path = out_dir / "frames.json.tmp"
    tmp_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp_path, frames_json_path)

    return frames_json_path


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
            ) from None  # SR-R-001: drop __cause__ to prevent path exposure (CWE-209)
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            # frames-3-L-1: ffprobe stderr (which may embed absolute input paths)
            # must not reach the MCP error envelope. Replace message with a safe
            # generic token; retain code and hint for diagnostic context (CWE-209).
            raise ClipwrightError(
                code=exc.code,
                message=safe_subprocess_message(exc),
                hint=exc.hint,
            ) from None
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

    # Compute timeout once (used by all modes that call ffmpeg).
    # Clamp duration_sec to prevent OverflowError on extreme ffprobe values
    # (CWE-400 / SR-V-001). Mirrors clipwright-reframe _MAX_TIMEOUT_DURATION_S.
    _safe_dur = min(duration_sec, _MAX_TIMEOUT_DURATION_S)
    _prod = _safe_dur * 2
    timeout = float(max(60, math.ceil(_prod) if math.isfinite(_prod) else 630_720_000))

    # --- 5. Mode dispatch ---

    warnings: list[str] = []

    # List of (index, timestamp_sec) pairs describing successfully extracted frames.
    extracted_frames: list[tuple[int, float]] = []

    ffmpeg = resolve_tool("ffmpeg", env_var="CLIPWRIGHT_FFMPEG")
    abs_media = str(media_path.resolve())

    if options.mode == "interval":
        interval_sec = options.interval_sec

        if interval_sec > duration_sec:
            warnings.append(
                "interval_sec exceeds media duration. No frames extracted. "
                "Use a smaller interval_sec value."
            )
        else:
            timestamps = compute_interval_timestamps(duration_sec, interval_sec)
            for idx, ts in enumerate(timestamps):
                out_path = str(out_dir / frame_filename(idx, options.format))
                cmd = build_single_frame_command(
                    ffmpeg, abs_media, ts, out_path, options
                )
                _run_with_safe_error(cmd, timeout)
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
                message="scene_timeline must have a .otio extension.",
                hint="Change the scene_timeline file path extension to .otio.",
            )
        if not scene_path.exists():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=f"scene_timeline file not found: {scene_path.name}",
                hint="Specify a valid .otio timeline file path.",
            )

        # Load OTIO (OTIO_ERROR propagated as-is).
        # scene_timeline is a read-only input; it is NOT required to be inside
        # output_dir. Boundary check applies to output artifacts only.
        tl = load_timeline(scene_timeline)

        # Extract scene_boundary marker timestamps
        markers = get_markers(tl, kind="scene_boundary")
        boundaries = scene_marker_seconds(markers)

        # Determine timestamps to extract based on scene_sample.
        # "boundary": preserve pre-0.2.0 behaviour (one frame per marker position).
        # "midpoint"/"start": compute segment representatives via plan function
        #   (no warning even when 0 boundaries — entire media treated as one segment).
        if options.scene_sample == "boundary":
            if not boundaries:
                warnings.append(
                    "No scene_boundary markers found in the timeline."
                    " No frames extracted."
                )
                ts_list: list[float] = []
            else:
                ts_list = boundaries
        else:
            # options.scene_sample is "midpoint" or "start" here.
            ts_list = compute_scene_segment_timestamps(
                boundaries, duration_sec, options.scene_sample
            )

        for idx, ts in enumerate(ts_list):
            out_path = str(out_dir / frame_filename(idx, options.format))
            cmd = build_single_frame_command(ffmpeg, abs_media, ts, out_path, options)
            _run_with_safe_error(cmd, timeout)
            extracted_frames.append((idx, ts))

    else:
        # mode == "timestamps"
        kept, skipped = compute_timestamps_mode(options.timestamps, duration_sec)

        if skipped:
            warnings.append(
                f"Skipped {len(skipped)} out-of-range timestamp(s). "
                "Values must be in [0, duration_sec)."
            )

        if kept:
            for idx, ts in enumerate(kept):
                out_path = str(out_dir / frame_filename(idx, options.format))
                cmd = build_single_frame_command(
                    ffmpeg, abs_media, ts, out_path, options
                )
                _run_with_safe_error(cmd, timeout)
                extracted_frames.append((idx, ts))

    # --- 6. Boundary check on output artifacts (CWE-22) ---

    out_dir_resolved = out_dir.resolve()
    frames_otio_path = out_dir / "frames.otio"
    frames_json_path = out_dir / "frames.json"
    check_within_boundary(out_dir_resolved, frames_otio_path, "frame output")
    check_within_boundary(out_dir_resolved, frames_json_path, "frame output")

    # --- 7. Write frames.otio ---

    frames_otio_path = _write_frames_otio(out_dir, media_path, extracted_frames, rate)

    # --- 8. Write frames.json (atomic) ---

    frames_json_path = _write_frames_json(out_dir, options, extracted_frames)

    # --- 9. Build return envelope ---

    frame_count = len(extracted_frames)
    if options.mode == "scene":
        # CR-NEW / L-5: include scene_sample in data and summary so AI can
        # distinguish midpoint (N shots) from boundary (N boundaries) without
        # reading the manifest.
        summary = (
            f"Extracted {frame_count} frame(s) from {media_path.name} "
            f"in scene mode (scene_sample={options.scene_sample}, "
            f"format={options.format}). "
            f"Output directory: {out_dir.name}."
        )
        data: dict[str, object] = {
            "frame_count": frame_count,
            "mode": options.mode,
            "scene_sample": options.scene_sample,
            "format": options.format,
        }
    else:
        summary = (
            f"Extracted {frame_count} frame(s) from {media_path.name} "
            f"in {options.mode} mode (format={options.format}). "
            f"Output directory: {out_dir.name}."
        )
        data = {
            "frame_count": frame_count,
            "mode": options.mode,
            "format": options.format,
        }

    return ok_result(
        summary,
        data=data,
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
            ) from None
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
    except Exception:
        # SR-R-001: catch unexpected exceptions (e.g. OTIOError from save_timeline,
        # OSError from _write_frames_json) with fixed wording to prevent internal
        # path exposure (CWE-209).
        return error_result(
            ErrorCode.INTERNAL,
            "Frame extraction failed due to an internal error.",
            "Retry after verifying that the output directory is writable.",
        )
