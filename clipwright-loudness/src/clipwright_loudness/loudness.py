"""loudness.py — clipwright-loudness orchestration layer (design §3.2, ADR-L4/L7).

Flow:
  1. Output validation (extension, parent dir, output≠media/timeline)
  2. inspect_media: require both video and audio streams
  3. Timeline resolution (None -> create new / path -> load + validate)
  4. measure_loudness: measure loudness
  5. If measured is None, skip loudness directive and emit warning
     (U-1, DC-AM-003).
     If measured is present, partial-update timeline-level metadata
     with loudness directive.
  6. save_timeline -> return ok_result

Design decisions:
- FILE_NOT_FOUND / message uses basename only (DC-GP-005).
- output may be placed anywhere; parent dir must exist, output≠media/timeline
  (DC-AS-004). Replaces old DC-AS-002 same-dir constraint.
- target_url in written OTIO follows media_ref_for_otio(): relative POSIX when
  media is inside the output directory, absolute otherwise (DC-AM-004).
- Timeline source validation uses check_media_ref(): accepts absolute existing
  files regardless of directory; relative traversal rejected (CWE-22).
- output==media comparison uses check_output_not_source() (B-4).
- timeline validation: exactly one Video-kind track (B-5).
- measured=None: skip loudness directive and return warning (U-1, DC-AM-003).
- Mirrors _add_full_clip / _load_and_validate_timeline structure from noise.py.
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
from pydantic import ValidationError

import clipwright_loudness
from clipwright_loudness.analyze import measure_loudness
from clipwright_loudness.schemas import (
    DetectLoudnessOptions,
    LoudnessDirective,
    LoudnormMeasured,
    LoudnormTarget,
    PeakMeasured,
    PeakTarget,
)


def detect_loudness(
    media: str,
    output: str,
    options: DetectLoudnessOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for loudness detection. Converts ClipwrightError to ok=False envelope.

    Args:
        media: Input media file path (video + audio required).
        output: Output OTIO timeline file path (.otio). Parent directory must
            exist; output may be placed anywhere (create type, DC-AS-004).
            Must not equal media or timeline.
        options: DetectLoudnessOptions.
        timeline: Existing timeline path (None = create new).

    Returns:
        ok_result or error_result ToolResult.
    """
    try:
        return _detect_loudness_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _detect_loudness_inner(
    media: str,
    output: str,
    options: DetectLoudnessOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of detect_loudness. Raises ClipwrightError directly."""
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

    # Prohibit output == media or output == timeline (non-destructive invariant)
    _sources: list[str] = [media]
    if timeline is not None:
        _sources.append(timeline)
    check_output_not_source(output_path, _sources)

    # --- 2. inspect_media: require both video and audio ---

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

    # Retrieve duration (total seconds for the full-length clip in _add_full_clip)
    duration_sec: float = 0.0
    if media_info.duration is not None:
        duration_sec = media_info.duration.value / media_info.duration.rate

    # --- 3. Timeline resolution ---

    otio_dir = output_path.parent

    if timeline is None:
        # Create new: add one full-length keep clip to V1
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration, otio_dir)
    else:
        tl = _load_and_validate_timeline(
            timeline, media_path, duration_sec, media_info.duration, otio_dir
        )

    # --- 4. Loudness measurement ---

    kwargs: dict[str, Any] = {}
    if options.mode == "loudnorm":
        kwargs = {
            "target_i": options.target_i,
            "target_tp": options.target_tp,
            "target_lra": options.target_lra,
        }
    else:
        kwargs = {"target_peak_db": options.target_peak_db}

    analysis = measure_loudness(media_path, mode=options.mode, **kwargs)

    measured_raw: dict[str, Any] | None = analysis["measured"]
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. Partial-update timeline-level metadata with loudness directive ---
    # (U-1: skip when measured is None)

    if measured_raw is None:
        # U-1: measurement not possible — skip directive and emit warning (DC-AM-003)
        # analyze already added a warning, but loudness adds one as well
        warnings.append(
            "Could not retrieve loudness measured values."
            " loudness directive will not be written (U-1)."
        )
    else:
        # measured present — write loudness directive
        if options.mode == "loudnorm":
            target: LoudnormTarget | PeakTarget = LoudnormTarget(
                i=options.target_i,
                tp=options.target_tp,
                lra=options.target_lra,
            )
            try:
                measured_obj: LoudnormMeasured | PeakMeasured | None = LoudnormMeasured(
                    **measured_raw
                )
            except ValidationError:
                # CWE-209: do not expose ValidationError details externally
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "Validation of loudnorm measured values failed."
                        " Check field types."
                    ),
                    hint="Check the return value of measure_loudness.",
                ) from None
        else:
            target = PeakTarget(peak_db=options.target_peak_db)
            try:
                measured_obj = PeakMeasured(**measured_raw)
            except ValidationError:
                # CWE-209: do not expose ValidationError details externally
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "Validation of peak measured values failed. Check field types."
                    ),
                    hint="Check the return value of measure_loudness.",
                ) from None

        directive = LoudnessDirective(
            tool="clipwright-loudness",
            version=clipwright_loudness.__version__,
            kind="loudness",
            mode=options.mode,
            scope="track",
            target=target,
            measured=measured_obj,
        )

        existing_meta = get_clipwright_metadata(tl)
        existing_meta["loudness"] = directive.model_dump()
        set_clipwright_metadata(tl, existing_meta)

    # --- 6. save_timeline -> ok_result ---

    save_timeline(tl, str(output_path))

    if measured_raw is not None:
        summary = (
            f"Loudness analysis of {media_path.name} complete."
            f" mode={options.mode}, scope={options.scope}."
            f" loudness directive written to {output_path.name}."
        )
    else:
        summary = (
            f"Loudness analysis of {media_path.name} attempted"
            " but measured values could not be retrieved."
            f" mode={options.mode}, scope={options.scope}."
            f" loudness directive was not written (U-1)."
        )

    return ok_result(
        summary,
        data={
            "mode": options.mode,
            "scope": options.scope,
            "measured": measured_raw,
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
    """Add one full-length keep clip to V1/A1 tracks of the timeline (new creation).

    target_url follows media_ref_for_otio(): relative POSIX when media is inside
    otio_dir, absolute path when media is outside (DC-AM-004).

    Args:
        duration_rt: Pydantic model RationalTimeModel (not OTIO RationalTime).
            Used to obtain the rate. Falls back to rate=1000.0 when None.
        otio_dir: Directory where the output OTIO file will be saved.
    """
    target_url = media_ref_for_otio(media_path, otio_dir)

    # Determine rate: use duration if available, otherwise 1000.0.
    # RationalTimeModel.rate is guaranteed to be float by the Pydantic schema,
    # but gt=0 is not constrained, so zero-division is theoretically possible.
    # However, OTIO RationalTime(duration_sec * rate, rate) initialization does
    # not divide internally, so no crash occurs in practice.
    rate = duration_rt.rate if duration_rt is not None else 1000.0

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    ref = otio.schema.ExternalReference(target_url=target_url)

    # Add the same clip to V1 (index 0) and A1 (index 1)
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
      (B-4: CWD-independent via check_timeline_source_matches)
    - Single source (all clips share the same target_url)
    - Exactly one Video-kind track (B-5)

    If V1 is empty, adds a full-length keep clip and continues
    (equivalent to new creation).

    Args:
        otio_dir: Output OTIO directory used for media_ref_for_otio() when
            the clip list is empty.

    Raises:
        ClipwrightError: INVALID_INPUT / OTIO_ERROR / PATH_NOT_ALLOWED.
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

    # --- Collect all clip target_urls and validate single source ---
    clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]

    if not clips:
        # V1 is empty — add full-length keep clip and continue
        _add_full_clip(tl, media_path, duration_sec, duration_rt, otio_dir)
        return tl

    urls: set[str] = set()
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            urls.add(ref.target_url)

    # --- Boundary check: validate each source reference (DC-AM-004 / CWE-22) ---
    # check_media_ref accepts absolute existing files and rejects relative traversal.
    tl_dir = Path(timeline_path).parent
    for url in urls:
        check_media_ref(url, tl_dir, "media")

    if len(urls) > 1:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="Timeline contains clips from multiple sources.",
            hint="Specify a timeline with a single source (same media file).",
        )

    # --- Validate target_url == media_path (B-4: CWD-independent via helper) ---
    if urls:
        check_timeline_source_matches(next(iter(urls)), media_path, tl_dir)

    return tl
