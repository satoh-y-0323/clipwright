"""stabilize.py — detect_shake orchestration layer (architecture-report §5).

Flow (7 steps):
  1. Output validation (extension, parent dir, output≠media/timeline)
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
- output may be placed anywhere; parent dir must exist, output≠media/timeline
  (DC-AS-004). Replaces old DC-AS-002 same-dir constraint.
- target_url in written OTIO follows media_ref_for_otio(): relative POSIX when
  media is inside the output directory, absolute otherwise (DC-AM-004).
- Timeline source validation uses check_media_ref(): accepts absolute existing
  files regardless of directory; relative traversal rejected (CWE-22).
- Mirrors color.py helper structure (_add_full_clip / _load_and_validate_timeline).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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
from clipwright.pathpolicy import (
    check_media_ref,
    check_output_not_source,
    media_ref_for_otio,
)
from clipwright.schemas import RationalTimeModel, ToolResult

import clipwright_stabilize
from clipwright_stabilize.analyze import recommend, run_vidstabdetect
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
        output: Output OTIO timeline file path (.otio). Parent directory must
            exist; output may be placed anywhere (create type, DC-AS-004).
            Must not equal media or timeline.
        options: DetectShakeOptions with shakiness / accuracy / smoothing.
        timeline: Existing timeline path (None = create new).

    Returns:
        ok_result or error_result ToolResult.
    """
    try:
        return _detect_shake_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # Catch unexpected exceptions (e.g. OTIOError from load_timeline/save_timeline)
        # that are not ClipwrightError, to prevent raw tracebacks with absolute paths
        # from reaching the MCP caller (CWE-209).  Sanitised message only (SR-R-001).
        return error_result(
            ErrorCode.INTERNAL,
            "Shake detection failed due to an internal error.",
            hint=(
                "Please report this with reproduction steps: "
                "media path, output path, and the exact error."
            ),
        )


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

    # Prohibit output == media or output == timeline (non-destructive invariant)
    _sources: list[str] = [media]
    if timeline is not None:
        _sources.append(timeline)
    check_output_not_source(output_path, _sources)

    # --- 2. inspect_media: video required, audio NOT required ---
    # inspect_media raises FILE_NOT_FOUND internally (color.py parity — no pre-check).

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

    otio_dir = output_path.parent

    if timeline is None:
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration, otio_dir)
    else:
        tl = _load_and_validate_timeline(
            timeline, media_path, duration_sec, media_info.duration, otio_dir
        )

    # --- 4. run_vidstabdetect (generates .trf + estimates severity) ---

    analysis: dict[str, Any] = run_vidstabdetect(media_path, output_path, options)
    warnings: list[str] = list(analysis["warnings"])

    severity: float | None = analysis["severity"]

    # Compute recommendation: use pre-computed value from analysis when available
    # (real run_vidstabdetect path); fall back to recommend() when mocked/absent.
    # run_vidstabdetect already adds a consolidated warning when severity is None
    # (CR-M-001), so no additional warning is appended here.
    precomputed_recommendation: str | None = analysis.get("recommendation")
    recommendation: Literal["skip", "apply"] = (
        precomputed_recommendation  # type: ignore[assignment]
        if precomputed_recommendation in ("skip", "apply")
        else recommend(severity)
    )

    # --- 5. Build StabilizeDirective and annotate timeline metadata ---
    # severity=None is allowed — directive is always written when trf is generated.

    directive = StabilizeDirective(
        tool="clipwright-stabilize",
        version=clipwright_stabilize.__version__,
        kind="stabilize",
        trf_path=str(Path(analysis["trf_path"]).resolve()),
        severity=severity,
        recommendation=recommendation,
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
    sev_str = f"{severity:.3f}" if severity is not None else "unavailable"

    summary = (
        f"Shake analysis of {media_path.name} complete."
        f" severity={sev_str},"
        f" recommendation={recommendation},"
        f" shakiness={options.shakiness},"
        f" smoothing={options.smoothing}."
        f" Stabilize directive and {trf_basename} written."
        f" The calling agent makes the final decision on whether to apply"
        f" stabilization; this recommendation is advisory only."
    )

    return ok_result(
        summary,
        data={
            "severity": severity,
            "recommendation": recommendation,
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


def _add_full_clip(
    tl: otio.schema.Timeline,
    media_path: Path,
    duration_sec: float,
    duration_rt: RationalTimeModel | None,
    otio_dir: Path,
) -> None:
    """Add one full-length keep clip to V1 track of the timeline (new creation).

    target_url follows media_ref_for_otio(): relative POSIX when media is inside
    otio_dir, absolute path when media is outside (DC-AM-004).

    Args:
        duration_rt: Pydantic model RationalTimeModel (not OTIO RationalTime).
            Used to obtain the rate. Falls back to rate=1000.0 when None.
        otio_dir: Directory where the output OTIO file will be saved.
    """
    target_url = media_ref_for_otio(media_path, otio_dir)

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
    otio_dir: Path,
) -> otio.schema.Timeline:
    """Load an existing timeline and validate its consistency (B-4 / B-5).

    Validates:
    - OTIO source references via check_media_ref (absolute existing files
      allowed; relative traversal rejected, CWE-22).
    - The target_url of V1 clips matches media_path
    - Single source (all clips share the same target_url)
    - Exactly one Video-kind track (B-5)

    If V1 is empty, adds a full-length keep clip and continues.

    Args:
        otio_dir: Output OTIO directory used for media_ref_for_otio() when
            the clip list is empty.

    Raises:
        ClipwrightError: INVALID_INPUT / OTIO_ERROR / PATH_NOT_ALLOWED.
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
        _add_full_clip(tl, media_path, duration_sec, duration_rt, otio_dir)
        return tl

    urls: set[str] = set()
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            urls.add(ref.target_url)

    # Reject multi-source timelines first (UNSUPPORTED_OPERATION — color.py order).
    if len(urls) > 1:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="Timeline contains clips from multiple sources.",
            hint="Specify a timeline with a single source (same media file).",
        )

    # Boundary check: validate each source reference (DC-AM-004 / CWE-22).
    # check_media_ref accepts absolute existing files and rejects relative traversal.
    tl_dir = Path(timeline_path).parent
    for url in urls:
        check_media_ref(url, tl_dir, "media")

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
