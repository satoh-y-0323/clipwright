"""noise.py — clipwright-noise orchestration layer (design §1.1).

Flow:
  1. Output validation (extension, parent dir, output==media, output==timeline)
  2. inspect_media: video + audio required check (ADR-N8)
  3. Timeline resolution (None → new / path → load + validate)
  4. measure_noise: measure noise floor via astats → calculate params
  5. Partial update of denoise directive into timeline-level metadata
  6. save_timeline → return ok_result

Design decisions:
- FILE_NOT_FOUND / SUBPROCESS_FAILED messages use basename only (DC-GP-005).
- output may be placed in any directory (parent must exist; output != source).
- target_url in generated OTIO clips follows the media_ref_for_otio policy:
  relative POSIX when media is under the OTIO directory (enabling project-level
  portability), absolute otherwise (ADR-PP-1 escape hatch for external media).
- source==media comparison delegates to check_timeline_source_matches
  (B-4: CWD-independent).
- Timeline validation: exactly one Video-kind track (B-5).
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
from clipwright.pathpolicy import (
    check_media_ref,
    check_output_not_source,
    check_timeline_source_matches,
    media_ref_for_otio,
)
from clipwright.schemas import RationalTimeModel, ToolResult

import clipwright_noise
from clipwright_noise.analyze import measure_noise
from clipwright_noise.schemas import DenoiseDirective, DetectNoiseOptions


def detect_noise(
    media: str,
    output: str,
    options: DetectNoiseOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for noise detection. Converts ClipwrightError to ok=False.

    Args:
        media: Input media file path (video + audio required).
        output: Output OTIO timeline file path (.otio; parent dir must exist;
            may be placed in any directory).
        options: DetectNoiseOptions.
        timeline: Existing timeline path (None = create new).

    Returns:
        ToolResult envelope (ok=True on success, ok=False on error).
    """
    try:
        return _detect_noise_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording to
        # prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Noise detection failed due to an internal error.",
            "Retry after verifying that the input and output paths are accessible.",
        )


def _detect_noise_inner(
    media: str,
    output: str,
    options: DetectNoiseOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of detect_noise. Raises ClipwrightError directly."""
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
            message="Output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    # Prohibit output == media or output == timeline (non-destructive; M5 / SR-L-4)
    _sources: list[str] = [media]
    if timeline is not None:
        _sources.append(timeline)
    check_output_not_source(output_path, _sources)

    # --- 2. inspect_media: video + audio required (ADR-N8 / DC-AS-003) ---

    if not media_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {media_path.name}",
            hint="Check that the input media file path is correct.",
        )

    media_info = inspect_media(media)

    has_video = any(s.codec_type == "video" for s in media_info.streams)
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint="Provide a media file that contains both video and audio.",
        )

    if not has_audio:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No audio stream found: {media_path.name}",
            hint="Provide a media file that contains both video and audio.",
        )

    # Obtain duration (total duration in seconds passed to _add_full_clip)
    duration_sec: float = 0.0
    if media_info.duration is not None:
        duration_sec = media_info.duration.value / media_info.duration.rate

    # --- 3. Timeline resolution ---

    if timeline is None:
        # New timeline: append one full-length keep clip to V1
        tl = new_timeline(media_path.name)
        _add_full_clip(
            tl, media_path, duration_sec, media_info.duration, output_path.parent
        )
    else:
        tl = _load_and_validate_timeline(
            timeline, media_path, duration_sec, media_info.duration, output_path.parent
        )

    # --- 4. Noise analysis ---

    analysis = measure_noise(
        media_path=media_path,
        strength=options.strength,
        backend=options.backend,
    )

    params: dict[str, Any] = analysis["params"]
    measured: float | None = analysis["measured_noise_floor_db"]
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. Partial update of denoise directive into timeline-level metadata ---

    directive = DenoiseDirective(
        tool="clipwright-noise",
        version=clipwright_noise.__version__,
        kind="denoise",
        backend=options.backend,
        params=params,
        measured_noise_floor_db=measured,
    )

    existing_meta = get_clipwright_metadata(tl)
    existing_meta["denoise"] = directive.model_dump()
    set_clipwright_metadata(tl, existing_meta)

    # Add render-not-supported warning when deepfilternet is selected (DC-GP-003)
    if options.backend == "deepfilternet":
        warnings.append(
            "backend=deepfilternet was selected."
            " Render application is not supported (first release: afftdn only)."
            " Re-detect with afftdn or wait for a future release."
        )

    # --- 6. save_timeline → ok_result ---

    save_timeline(tl, str(output_path))

    summary = (
        f"Noise analysis of {media_path.name} completed."
        f" backend={options.backend}, strength={options.strength}."
        f" Denoise directive written to {output_path.name}."
        + (
            f" Measured noise floor: {measured:.1f} dB."
            if measured is not None
            else " Noise floor measurement failed; default value used."
        )
    )

    return ok_result(
        summary,
        data={
            "backend": options.backend,
            "strength": options.strength,
            "measured_noise_floor_db": measured,
            "params": params,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
        ],
        warnings=warnings,
    )


def _add_full_clip(
    tl: otio.schema.Timeline,
    media_path: Path,
    duration_sec: float,
    duration_rt: RationalTimeModel | None,
    otio_dir: Path,
) -> None:
    """Append one full-length keep clip to the V1/A1 tracks of the timeline.

    Used for new timelines.
    target_url follows the media_ref_for_otio policy: relative POSIX when media
    is under otio_dir, absolute path otherwise (ADR-PP-1 escape hatch).
    """
    target_url = media_ref_for_otio(media_path, otio_dir)

    # Determine rate: use the measured duration rate if available, otherwise 1000.0
    rate = duration_rt.rate if duration_rt is not None else 1000.0

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    # ADR-4 pattern B: _add_full_clip always creates a full-length keep clip,
    # so the exact media duration is known here; wire it as available_range
    # (identical to source_range) instead of leaving it unset (None).
    ref = otio.schema.ExternalReference(
        target_url=target_url, available_range=source_range
    )

    # Append the same clip to V1 (index 0) and A1 (index 1)
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
    """Load an existing timeline and validate its consistency.

    Validation references: DC-AM-003 / DC-AM-004 / B-4 / B-5.

    Validation:
    - The target_url of V1 clips matches media_path
      (B-4: resolved against the OTIO directory via check_timeline_source_matches)
    - Single source (all clips share the same target_url)
    - Exactly one Video-kind track (B-5)

    If V1 is empty, a full-length keep clip is appended and processing continues
    (to make the timeline renderable, equivalent to creating a new one;
    prevents render from rejecting with INVALID_INPUT due to zero clips).

    Raises:
        ClipwrightError: INVALID_INPUT / OTIO_ERROR.
    """
    tl = load_timeline(timeline_path)

    # --- Exactly one Video-kind track (B-5) ---
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

    # --- Collect all clip target_urls and validate single source (DC-AM-004) ---
    clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]

    if not clips:
        # V1 is empty: append a full-length keep clip and continue
        # (equivalent to creating a new timeline).
        # Prevents render's resolve_kept_ranges from rejecting due to zero clips.
        _add_full_clip(tl, media_path, duration_sec, duration_rt, otio_dir)
        return tl

    urls: set[str] = set()
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            urls.add(ref.target_url)

    # --- Single-source check (DC-AM-004): must precede check_media_ref so that
    #     multi-source timelines are rejected with UNSUPPORTED_OPERATION before
    #     individual URL validation (which may raise PATH_NOT_ALLOWED for missing
    #     files).  Security is not weakened: multi-source is always rejected. ---
    if len(urls) > 1:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="The timeline contains clips from multiple sources.",
            hint="Specify a timeline with a single source (same media file).",
        )

    # --- Boundary check: validate each source reference (DC-AM-004 / CWE-22) ---
    # check_media_ref accepts absolute existing files and rejects relative traversal.
    tl_dir = Path(timeline_path).parent
    for url in urls:
        check_media_ref(url, tl_dir, "media")

    # --- Validate target_url == media_path (B-4: CWD-independent via core helper) ---
    if urls:
        check_timeline_source_matches(next(iter(urls)), media_path, tl_dir)

    return tl
