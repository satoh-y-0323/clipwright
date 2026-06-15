"""detect.py — clipwright-scene orchestration layer.

Handles the full flow: input validation -> inspect_media -> backend dispatch
-> boundary merge -> OTIO construction/save -> envelope return.

Design decisions:
- _detect_scenes_inner() is the raising implementation; detect_scenes() is the
  public boundary that catches ClipwrightError and converts to error_result.
- _detect_with_ffmpeg() uses the scdet filter (-vf scdet=threshold=T); stderr
  is parsed by parse.parse_scdet_stderr().
- _detect_with_pyscenedetect() spawns the scenedetect CLI and parses CSV stdout
  via parse.parse_pyscenedetect_csv().
- error messages do not expose full paths: FILE_NOT_FOUND uses basename only.
- subprocess seam errors are sanitised with safe_subprocess_message() before
  reaching the MCP error envelope.
"""

from __future__ import annotations

import math
from pathlib import Path

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_marker, load_timeline, new_timeline, save_timeline
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import RationalTimeModel, TimeRangeModel, ToolResult

import clipwright_scene
from clipwright_scene.parse import (
    SceneBoundary,
    merge_close_boundaries,
    parse_pyscenedetect_csv,
    parse_scdet_stderr,
)
from clipwright_scene.schemas import DetectScenesOptions


def _detect_with_ffmpeg(
    media: str,
    options: DetectScenesOptions,
    total_duration_sec: float,
) -> list[SceneBoundary]:
    """Run ffmpeg scdet filter and return parsed SceneBoundary list.

    Args:
        media: Absolute path to the input media file.
        options: DetectScenesOptions (references threshold and backend).
        total_duration_sec: Total duration of the media in seconds.

    Returns:
        Sorted list of SceneBoundary objects.

    Raises:
        ClipwrightError: DEPENDENCY_MISSING if ffmpeg not found;
            SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT from run().
    """
    ffmpeg = resolve_tool("ffmpeg", env_var="CLIPWRIGHT_FFMPEG")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        media,
        "-vf",
        f"scdet=threshold={options.threshold * 100:.1f}",
        "-f",
        "null",
        "-",
    ]
    timeout = float(max(60, math.ceil(total_duration_sec * 2)))
    try:
        result = run(cmd, timeout=timeout)
    except ClipwrightError as exc:
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            raise ClipwrightError(
                code=exc.code,
                message=safe_subprocess_message(exc),
                hint=exc.hint,
            ) from exc
        raise
    return parse_scdet_stderr(result.stderr or "", total_duration_sec)


def _detect_with_pyscenedetect(
    media: str,
    options: DetectScenesOptions,
    total_duration_sec: float,
) -> list[SceneBoundary]:
    """Run PySceneDetect CLI and return parsed SceneBoundary list.

    Attempts to resolve the scenedetect executable; falls back to the bare
    name so that run() will raise SUBPROCESS_FAILED if it is truly absent,
    giving a consistent error surface at the subprocess boundary.

    Args:
        media: Absolute path to the input media file.
        options: DetectScenesOptions (references threshold and backend).
        total_duration_sec: Total duration of the media in seconds.

    Returns:
        Sorted list of SceneBoundary objects (confidence=1.0 for all).

    Raises:
        ClipwrightError: SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT from run()
            when scenedetect is not installed or the command fails.
    """
    import shutil

    scenedetect_path = shutil.which("scenedetect") or "scenedetect"
    cmd = [
        scenedetect_path,
        "-i",
        media,
        "detect-content",
        "--threshold",
        f"{options.threshold * 27:.1f}",
        "list-scenes",
        "-c",
    ]
    timeout = float(max(120, math.ceil(total_duration_sec * 5)))
    try:
        result = run(cmd, timeout=timeout)
    except ClipwrightError as exc:
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            raise ClipwrightError(
                code=exc.code,
                message=safe_subprocess_message(exc),
                hint=exc.hint,
            ) from exc
        raise
    return parse_pyscenedetect_csv(result.stdout or "")


def _detect_scenes_inner(
    media: str,
    output: str,
    options: DetectScenesOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of detect_scenes. Raises ClipwrightError directly."""
    output_path = Path(output)
    media_path = Path(media)

    # --- 1. Output path validation ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Invalid output file extension: {output_path.suffix!r}. "
                "Only .otio is allowed."
            ),
            hint="Change the output file path extension to .otio.",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The output directory does not exist.",
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

    # Verify video stream: scene detection requires a video stream.
    has_video = any(s.codec_type == "video" for s in media_info.streams)
    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint=(
                "Scene detection requires a video stream. "
                "Specify a media file that contains video."
            ),
        )

    # Verify duration is available for timeout calculation.
    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message=f"Could not retrieve media duration: {media_path.name}",
            hint=(
                "Check that the media file is not corrupted. "
                "You can also verify manually with ffprobe."
            ),
        )

    total_duration_sec = media_info.duration.value / media_info.duration.rate
    rate = media_info.duration.rate

    # --- 3. Backend dispatch ---

    abs_media = str(media_path.resolve())

    if options.backend == "pyscenedetect":
        boundaries = _detect_with_pyscenedetect(abs_media, options, total_duration_sec)
    else:
        # Default: ffmpeg backend
        boundaries = _detect_with_ffmpeg(abs_media, options, total_duration_sec)

    # --- 4. Merge close boundaries ---

    boundaries = merge_close_boundaries(boundaries, options.min_scene_duration)

    # --- 5. Build OTIO timeline ---

    if timeline is not None:
        # Augment mode: load existing OTIO and append markers to V1 track.
        timeline_obj = load_timeline(timeline)
        v1 = timeline_obj.tracks[0]
    else:
        # New timeline mode.
        timeline_obj = new_timeline(name=f"{media_path.stem} — scenes")
        v1 = timeline_obj.tracks[0]

    # Attach a zero-duration marker for each detected boundary.
    for b in boundaries:
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=b.timestamp_sec * rate, rate=rate),
            duration=RationalTimeModel(value=0.0, rate=rate),
        )
        add_marker(
            v1,
            marked_range=marked_range,
            name=f"scene_{b.scene_index + 1}",
            color="GREEN",
            metadata={
                "tool": "clipwright-scene",
                "version": clipwright_scene.__version__,
                "kind": "scene_boundary",
                "scene_index": b.scene_index,
                "confidence": b.confidence,
                "backend": options.backend,
            },
        )

    save_timeline(timeline_obj, output)

    # --- 6. Build return envelope ---

    scene_count = len(boundaries)
    warnings: list[str] = []
    if scene_count == 0:
        warnings.append(
            "No scene boundaries detected. Consider lowering the threshold."
        )

    summary = (
        f"Detected {scene_count} scene boundary(ies) using {options.backend} "
        f"in {media_path.name} "
        f"(duration: {total_duration_sec:.1f}s). "
        f"Saved OTIO timeline to {output_path.name}."
    )

    return ok_result(
        summary,
        data={
            "scene_count": scene_count,
            "backend": options.backend,
            "total_duration_sec": total_duration_sec,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=warnings,
    )


def detect_scenes(
    media: str,
    output: str,
    options: DetectScenesOptions,
    timeline: str | None = None,
) -> ToolResult:
    """Detect shot boundaries and write an OTIO timeline with markers.

    Non-destructive: does not modify the input media file in any way.
    Each detected boundary is recorded as a zero-duration OTIO Marker on the V1 track.

    Args:
        media: Input video file path.
        output: Output OTIO timeline file path (must end in .otio).
        options: DetectScenesOptions controlling threshold, merge, and backend.
        timeline: Optional existing OTIO file path. When provided, markers are
            appended to the V1 track of the loaded timeline instead of creating
            a new timeline.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _detect_scenes_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
