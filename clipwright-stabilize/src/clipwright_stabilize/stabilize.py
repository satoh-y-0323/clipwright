"""stabilize.py — detect_shake orchestration layer (architecture-report §5).

Flow (7 steps):
  1. Output validation (extension, parent dir, output==media,
     output==timeline, same directory as media)
  2. inspect_media: require video stream (audio NOT required)
  3. Timeline resolution (None -> create new / path -> load + validate)
  4. run_vidstabdetect (generate .trf + estimate severity)
  5. Build StabilizeDirective and annotate timeline metadata
     (severity=None -> still write directive, differs from color measured=None skip)
  6. save_timeline (atomic)
  7. ok_result with summary / data / artifacts (both .otio and .trf paths verified)

Design decisions:
- Audio NOT required; shake detection requires video stream only.
- severity=None does NOT skip directive (trf was generated and can still be applied).
- output must be in the same directory as media (DC-AS-002).
- Mirrors color.py helper structure (_same_path / _add_full_clip /
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

import clipwright_stabilize
from clipwright_stabilize.analyze import run_vidstabdetect
from clipwright_stabilize.schemas import DetectShakeOptions, StabilizeDirective


def detect_shake(
    media: str,
    output: str,
    options: DetectShakeOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for shake detection. Converts ClipwrightError to ok=False envelope.

    Args:
        media: Input video file path (video stream required).
        output: Output OTIO timeline file path (.otio, same directory as media).
        options: DetectShakeOptions with shakiness / accuracy / smoothing.
        timeline: Existing timeline path (None = create new).

    Returns:
        ok_result or error_result ToolResult.
    """
    try:
        return _detect_shake_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _detect_shake_inner(
    media: str,
    output: str,
    options: DetectShakeOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of detect_shake. Raises ClipwrightError directly."""
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
    # NOTE: no has_audio check — stabilize does not require audio.

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint="Provide a media file that contains a video stream.",
        )

    # Retrieve duration for _add_full_clip
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

    # --- 4. run_vidstabdetect (generates .trf + estimates severity) ---

    analysis: dict[str, Any] = run_vidstabdetect(media_path, output_path, options)
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. Build StabilizeDirective and annotate timeline metadata ---
    # severity=None is allowed — directive is always written when trf is generated.

    directive = StabilizeDirective(
        tool="clipwright-stabilize",
        version=clipwright_stabilize.__version__,
        kind="stabilize",
        trf_path=str(Path(analysis["trf_path"]).resolve()),
        severity=analysis["severity"],
        shakiness=options.shakiness,
        accuracy=options.accuracy,
        smoothing=options.smoothing,
    )
    existing_meta = get_clipwright_metadata(tl)
    existing_meta["stabilize"] = directive.model_dump()
    set_clipwright_metadata(tl, existing_meta)

    # --- 6. save_timeline (atomic) ---

    save_timeline(tl, str(output_path))

    # --- 7. ok_result: verify both paths exist, build summary and artifacts ---

    trf_abs = Path(analysis["trf_path"]).resolve()
    otio_abs = output_path.resolve()

    # Post-save sanity check (frames parity — both artifacts must be on disk).
    if not otio_abs.exists():
        raise ClipwrightError(
            code=ErrorCode.INTERNAL,
            message="Timeline file was not created after save_timeline.",
            hint="Check that the output directory is writable.",
        )
    if not trf_abs.exists():
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message="The .trf analysis file is no longer present after vidstabdetect.",
            hint="Check that the output directory was not modified during analysis.",
        )

    trf_basename = trf_abs.name
    sev = analysis["severity"]
    sev_str = f"{sev:.3f}" if sev is not None else "unavailable"

    summary = (
        f"Shake analysis of {media_path.name} complete."
        f" severity={sev_str},"
        f" shakiness={options.shakiness},"
        f" smoothing={options.smoothing}."
        f" Stabilize directive and {trf_basename} written;"
        f" apply with clipwright-render."
    )

    return ok_result(
        summary,
        data={
            "severity": analysis["severity"],
            "shakiness": options.shakiness,
            "accuracy": options.accuracy,
            "smoothing": options.smoothing,
            "trf_basename": trf_basename,
        },
        artifacts=[
            {
                "role": "timeline",
                "path": str(otio_abs),
                "format": "otio",
            },
            {
                "role": "analysis",
                "path": str(trf_abs),
                "format": "trf",
            },
        ],
        warnings=warnings if warnings else None,
    )


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
        # Fallback to absolute() comparison when resolve() fails (e.g. broken symlink).
        try:
            allowed_base_abs = str(timeline_path.parent.absolute())
            source_abs = str(Path(source).absolute())
            if not (
                source_abs == allowed_base_abs
                or source_abs.startswith(allowed_base_abs + "/")
                or source_abs.startswith(allowed_base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message=(
                        "Source file points outside the timeline directory boundary."
                    ),
                    hint=(
                        "Use a source file located within the same directory"
                        " as the OTIO timeline."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            # absolute() also failed (truly unresolvable path) — best-effort skip
            pass
