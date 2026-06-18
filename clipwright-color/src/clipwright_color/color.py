"""color.py — clipwright-color orchestration layer (architecture-report §5).

Flow (7 steps):
  1. Output validation (extension, parent dir, output==media,
     output==timeline, same directory as media)
  2. inspect_media: require video stream (audio NOT required; FR-2 Constraint)
  3. Timeline resolution (None -> create new / path -> load + validate)
  4. measure_brightness
  5. Derive brightness offset and annotate ColorDirective
     (measured=None -> skip directive + warning, U-1)
  6. save_timeline (atomic)
  7. ok_result with summary / data / artifacts

Design decisions:
- Audio NOT required (Constraint, FR-2); color requires video stream only.
- output must be in the same directory as media (DC-AS-002).
- measured=None: skip directive, still save timeline + return warning (U-1 parity).
- brightness = clamp((target_luma - yavg) / 255.0, -1.0, 1.0).
- Mirrors loudness.py helper structure (_same_path / _add_full_clip /
  _load_and_validate_timeline / _check_source_within_timeline_dir).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
from clipwright.schemas import RationalTimeModel, ToolResult
from pydantic import ValidationError

import clipwright_color
from clipwright_color.analyze import measure_brightness
from clipwright_color.schemas import (
    BrightnessMeasured,
    ColorDirective,
    DetectColorOptions,
    EqParams,
)


def detect_color(
    media: str,
    output: str,
    options: DetectColorOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for color detection. Converts ClipwrightError to ok=False envelope.

    Args:
        media: Input video file path (video stream required).
        output: Output OTIO timeline file path (.otio, same directory as media).
        options: DetectColorOptions.
        timeline: Existing timeline path (None = create new).

    Returns:
        ok_result or error_result ToolResult.
    """
    try:
        return _detect_color_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _detect_color_inner(
    media: str,
    output: str,
    options: DetectColorOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of detect_color. Raises ClipwrightError directly."""
    media_path = Path(media)
    output_path = Path(output)

    # --- 1. Output validation ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Unsupported output extension: {output_path.suffix!r}",
            hint="Set the output file extension to .otio.",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    # Prohibit output == media (non-destructive)
    if _same_path(output_path, media_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input media path are the same.",
            hint="Change the output file path to differ from the input media.",
        )

    # Prohibit output == timeline (non-destructive)
    if timeline is not None and _same_path(output_path, Path(timeline)):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input timeline path are the same.",
            hint="Change the output file path to differ from the input timeline.",
        )

    # output must be in the same directory as media (DC-AS-002)
    try:
        media_resolved_dir = media_path.resolve().parent
        output_resolved_dir = output_path.resolve().parent
    except OSError:
        media_resolved_dir = media_path.absolute().parent
        output_resolved_dir = output_path.absolute().parent

    if media_resolved_dir != output_resolved_dir:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Output file must be placed in the same directory as the media file."
            ),
            hint="Change the output path to the same directory as the media file.",
        )

    # --- 2. inspect_media: video required, audio NOT required ---

    if not media_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {media_path.name}",
            hint="Check that the input media file path is correct.",
        )

    media_info = inspect_media(media)

    has_video = any(s.codec_type == "video" for s in media_info.streams)
    # NOTE: no has_audio check — color does not require audio (Constraint, FR-2).

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint="Provide a media file that contains a video stream.",
        )

    # Retrieve duration (total seconds for the full-length clip in _add_full_clip)
    duration_sec: float = 0.0
    if media_info.duration is not None:
        duration_sec = media_info.duration.value / media_info.duration.rate

    # --- 3. Timeline resolution ---

    if timeline is None:
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration)
    else:
        tl = _load_and_validate_timeline(
            timeline, media_path, duration_sec, media_info.duration
        )

    # --- 4. measure_brightness ---

    analysis = measure_brightness(media_path, options)
    measured_raw: dict[str, Any] | None = analysis["measured"]
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. Derive brightness offset and annotate ColorDirective ---
    # (U-1: skip when measured is None)

    brightness: float = 0.0

    if measured_raw is None:
        # U-1: measurement not possible — skip directive, still save timeline
        warnings.append(
            "Could not retrieve brightness measurement."
            " color directive will not be written (U-1)."
        )
    else:
        try:
            measured_obj = BrightnessMeasured(**measured_raw)
        except ValidationError:
            # CWE-209: do not expose ValidationError details externally
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Validation of brightness measured values failed.",
                hint="Check the return value of measure_brightness.",
            ) from None

        brightness = _clamp(
            (options.target_luma - measured_obj.yavg) / 255.0, -1.0, 1.0
        )

        directive = ColorDirective(
            tool="clipwright-color",
            version=clipwright_color.__version__,
            kind="color",
            target_luma=options.target_luma,
            measured=measured_obj,
            eq=EqParams(brightness=brightness),  # contrast/saturation/gamma neutral
        )

        existing_meta = get_clipwright_metadata(tl)
        existing_meta["color"] = directive.model_dump()
        set_clipwright_metadata(tl, existing_meta)

    # --- 6. save_timeline (atomic) ---

    save_timeline(tl, str(output_path))

    # --- 7. ok_result ---

    # Resolve final measured values for summary/data.
    # measured_obj is defined iff measured_raw is not None (set in step 5 else branch).
    final_luma: float | None = None
    final_brightness: float | None = None
    final_frames: int = 0

    if measured_raw is not None:
        final_luma = measured_obj.yavg
        final_brightness = brightness
        final_frames = measured_obj.sampled_frames
        summary = (
            f"Color analysis of {media_path.name} complete."
            f" measured_luma={final_luma:.1f}"
            f" (over {final_frames} frame(s)),"
            f" target_luma={options.target_luma:.1f},"
            f" computed brightness offset={brightness:+.3f}."
            f" color directive written to {output_path.name}."
        )
    else:
        summary = (
            f"Color analysis of {media_path.name} attempted but no YAVG could be"
            f" measured. color directive was not written (U-1)."
        )

    return ok_result(
        summary,
        data={
            "measured_luma": final_luma,
            "brightness": final_brightness,
            "contrast": 1.0,
            "saturation": 1.0,
            "gamma": 1.0,
            "target_luma": options.target_luma,
            "sampled_frames": final_frames,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=warnings,
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi] range."""
    return max(lo, min(hi, value))


def _same_path(a: Path, b: Path) -> bool:
    """Return True if both paths refer to the same entity (DC-GP-005 / B-4).

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
    duration_rt: RationalTimeModel | None,
) -> None:
    """Add one full-length keep clip to V1/A1 tracks of the timeline (new creation).

    target_url is set to the absolute path of media_path.resolve() (DC-AS-002).

    Args:
        duration_rt: Pydantic model RationalTimeModel (not OTIO RationalTime).
            Used to obtain the rate. Falls back to rate=1000.0 when None.
    """
    try:
        target_url = str(media_path.resolve())
    except OSError:
        target_url = str(media_path.absolute())

    rate = duration_rt.rate if duration_rt is not None else 1000.0

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


def _load_and_validate_timeline(
    timeline_path: str,
    media_path: Path,
    duration_sec: float,
    duration_rt: RationalTimeModel | None,
) -> otio.schema.Timeline:
    """Load an existing timeline and validate its consistency (B-4 / B-5).

    Validates:
    - The target_url of V1 clips matches media_path
    - Single source (all clips share the same target_url)
    - Exactly one Video-kind track (B-5)

    If V1 is empty, adds a full-length keep clip and continues.

    Raises:
        ClipwrightError: INVALID_INPUT / OTIO_ERROR.
    """
    tl = load_timeline(timeline_path)

    # Exactly one Video-kind track (B-5)
    video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    if len(video_tracks) != 1:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Invalid number of Video tracks in timeline: {len(video_tracks)}"
                " (only 1 is supported)"
            ),
            hint="Specify a timeline with exactly one Video track.",
        )

    v1 = video_tracks[0]

    clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]

    if not clips:
        _add_full_clip(tl, media_path, duration_sec, duration_rt)
        return tl

    urls: set[str] = set()
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            urls.add(ref.target_url)

    # Boundary check: target_url must be within timeline parent dir (SR L-2)
    tl_path = Path(timeline_path)
    for url in urls:
        _check_source_within_timeline_dir(tl_path, url)

    if len(urls) > 1:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="Timeline contains clips from multiple sources.",
            hint="Specify a timeline with a single source (same media file).",
        )

    # Validate target_url == media_path (B-4: resolve() normalization)
    if urls:
        target_url = next(iter(urls))
        try:
            tl_source = Path(target_url).resolve()
            media_resolved = media_path.resolve()
        except OSError:
            tl_source = Path(target_url).absolute()
            media_resolved = media_path.absolute()

        if tl_source != media_resolved:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"Timeline source file does not match input media."
                    f" timeline source: {Path(target_url).name}"
                    f" / media: {media_path.name}"
                ),
                hint=(
                    "Specify the same media file used when the timeline was created."
                ),
            )

    return tl


def _check_source_within_timeline_dir(timeline_path: Path, source: str) -> None:
    """Validate that the source path is within the timeline parent directory (SR L-2).

    Guards against malicious OTIO embedding arbitrary paths in target_url.

    Args:
        timeline_path: Path to the OTIO timeline file.
        source: Media source path obtained from OTIO target_url.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED (source outside timeline parent dir).
    """
    try:
        allowed_base = timeline_path.parent.resolve()
        source_resolved = Path(source).resolve()
        source_str = str(source_resolved)
        base_str = str(allowed_base)
        if not (
            source_str == base_str
            or source_str.startswith(base_str + "/")
            or source_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Source file points outside the timeline directory boundary.",
                hint=(
                    "Use a source file located within the same directory"
                    " as the OTIO timeline."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # Skip on resolve() failure as a best-effort fallback.
        pass
