"""color.py — clipwright-color orchestration layer (architecture-report §5).

Flow (7 steps):
  1. Output validation (extension, parent dir, output≠media/timeline)
  2. inspect_media: require video stream (audio NOT required; FR-2 Constraint)
  3. Timeline resolution (None -> create new / path -> load + validate)
  4. measure_brightness
  5. Derive brightness offset, WB, eq, LUT and annotate ColorDirective
     (measured=None -> skip directive + warning, U-1)
  6. save_timeline (atomic)
  7. ok_result with summary / data / artifacts

Design decisions:
- Audio NOT required (Constraint, FR-2); color requires video stream only.
- output may be placed anywhere; parent dir must exist, output≠media/timeline
  (DC-AS-004). Replaces old DC-AS-002 same-dir constraint.
- target_url in written OTIO follows media_ref_for_otio(): relative POSIX when
  media is inside the output directory, absolute otherwise (DC-AM-004).
- Timeline source validation uses check_media_ref(): accepts absolute existing
  files regardless of directory; relative traversal rejected (CWE-22).
- measured=None: skip directive, still save timeline + return warning (U-1 parity).
- brightness = clamp((target_luma - yavg) / 255.0, -1.0, 1.0).
- Auto WB (§4.2): BT.601 gray-world inverse-cast from uavg/vavg; WB_STRENGTH
  module constant scales correction strength (default 1.0 = full correction).
- Caller WB override (§4.3): temperature/tint normalised [-1,1] axes take
  precedence over auto derivation when either is supplied.
- FR-4 degradation: measured present but uavg/vavg absent → directive written
  WITH brightness+eq, white_balance omitted (None), WB warning appended.
- eq population (FR-1): saturation/contrast/gamma from options; neutral defaults.
- .cube validation (§5.1): validate_source_file (CWE-59) + media_ref_for_otio
  storage. Errors wrapped from None (no path in message, CWE-209 / ADR-CO-10).
- Mirrors loudness.py helper structure (_add_full_clip / _load_and_validate_timeline).
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
    validate_source_file,
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
    WhiteBalanceParams,
)

# Module constant: WB correction strength multiplier applied to gray-world shifts.
# 1.0 = full correction; lower values bias toward retaining the original look.
WB_STRENGTH = 1.0


def detect_color(
    media: str,
    output: str,
    options: DetectColorOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for color detection. Converts ClipwrightError to ok=False envelope.

    Args:
        media: Input video file path (video stream required).
        output: Output OTIO timeline file path (.otio). Parent directory must
            exist; output may be placed anywhere (create type, DC-AS-004).
            Must not equal media or timeline.
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

    otio_dir = output_path.parent

    if timeline is None:
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration, otio_dir)
    else:
        tl = _load_and_validate_timeline(
            timeline, media_path, duration_sec, media_info.duration, otio_dir
        )

    # --- 4. measure_brightness ---

    analysis = measure_brightness(media_path, options)
    measured_raw: dict[str, Any] | None = analysis["measured"]
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. Derive brightness offset, WB, eq, LUT and annotate ColorDirective ---
    # (U-1: skip when measured is None)

    final_luma: float | None = None
    final_brightness: float | None = None
    final_contrast: float = 1.0
    final_saturation: float = 1.0
    final_gamma: float = 1.0
    final_frames: int = 0
    summary: str

    if measured_raw is None:
        # U-1: measurement not possible — skip directive, still save timeline
        warnings.append(
            "Could not retrieve brightness measurement."
            " color directive will not be written (U-1)."
        )
        summary = (
            f"Color analysis of {media_path.name} attempted but no YAVG could be"
            f" measured. color directive was not written (U-1)."
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

        brightness: float = _clamp(
            (options.target_luma - measured_obj.yavg) / 255.0, -1.0, 1.0
        )

        # WB derivation: caller override takes precedence over auto gray-world
        # (§4.3 / §4.2)
        has_caller_wb = options.temperature is not None or options.tint is not None
        white_balance: WhiteBalanceParams | None
        if has_caller_wb:
            # Caller override: discard auto result regardless of chroma (§4.3)
            white_balance = _derive_caller_wb(options.temperature, options.tint)
        elif measured_obj.uavg is not None and measured_obj.vavg is not None:
            # Auto gray-world derivation from available chroma (§4.2)
            white_balance = _derive_auto_wb(measured_obj.uavg, measured_obj.vavg)
        else:
            # FR-4: chroma absent — omit white_balance and emit diagnostic warning
            white_balance = None
            warnings.append(
                "White balance could not be derived: chroma values (uavg/vavg)"
                " were not available in the measurement (FR-4)."
                " white_balance will not be written to the directive."
            )

        # EqParams population (FR-1): use caller-supplied values or neutral defaults
        final_saturation = options.saturation if options.saturation is not None else 1.0
        final_contrast = options.contrast if options.contrast is not None else 1.0
        final_gamma = options.gamma if options.gamma is not None else 1.0
        eq = EqParams(
            brightness=brightness,
            saturation=final_saturation,
            contrast=final_contrast,
            gamma=final_gamma,
        )

        # LUT validation (§5.1 / ADR-CO-10 / CWE-59 / CWE-209)
        lut_stored: str | None = None
        if options.lut is not None:
            try:
                validate_source_file(options.lut)
            except ClipwrightError as exc:
                # Wrap to strip the path from the error message (CWE-209 / ADR-CO-10).
                # validate_source_file leaks the full path in FILE_NOT_FOUND messages.
                raise ClipwrightError(
                    code=exc.code,
                    message="LUT (.cube) file is not accessible.",
                    hint=(
                        "Check that the .cube file exists, is a regular file,"
                        " and is not a symbolic link."
                    ),
                ) from None
            lut_stored = media_ref_for_otio(options.lut, otio_dir)

        directive = ColorDirective(
            tool="clipwright-color",
            version=clipwright_color.__version__,
            kind="color",
            target_luma=options.target_luma,
            measured=measured_obj,
            eq=eq,
            white_balance=white_balance,
            lut=lut_stored,
        )

        existing_meta = get_clipwright_metadata(tl)
        existing_meta["color"] = directive.model_dump()
        set_clipwright_metadata(tl, existing_meta)

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

    # --- 6. save_timeline (atomic) ---

    save_timeline(tl, str(output_path))

    return ok_result(
        summary,
        data={
            "measured_luma": final_luma,
            "brightness": final_brightness,
            "contrast": final_contrast,
            "saturation": final_saturation,
            "gamma": final_gamma,
            "target_luma": options.target_luma,
            "sampled_frames": final_frames,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=warnings,
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi] range."""
    return max(lo, min(hi, value))


def _derive_auto_wb(uavg: float, vavg: float) -> WhiteBalanceParams:
    """Derive colorbalance midtone shifts from BT.601 gray-world inverse-cast (§4.2).

    Neutral chroma in 8-bit YUV is 128. Offsets dU (Cb / blue-difference) and
    dV (Cr / red-difference) are mapped to RGB midtone correction shifts and
    normalised to [-1,1] by dividing by 255, then scaled by WB_STRENGTH.

    Args:
        uavg: Median Cb (blue-difference) value across sampled frames [0, 255].
        vavg: Median Cr (red-difference) value across sampled frames [0, 255].

    Returns:
        WhiteBalanceParams with r/g/b midtone shifts clamped to [-1, 1].
    """
    dU = uavg - 128.0  # Cb offset (blue-difference channel)
    dV = vavg - 128.0  # Cr offset (red-difference channel)
    r = _clamp(-1.402 * dV / 255.0 * WB_STRENGTH, -1.0, 1.0)
    g = _clamp((0.344 * dU + 0.714 * dV) / 255.0 * WB_STRENGTH, -1.0, 1.0)
    b = _clamp(-1.772 * dU / 255.0 * WB_STRENGTH, -1.0, 1.0)
    return WhiteBalanceParams(r=r, g=g, b=b)


def _derive_caller_wb(
    temperature: float | None,
    tint: float | None,
) -> WhiteBalanceParams:
    """Compute colorbalance shifts from caller temperature/tint normalised axes (§4.3).

    temperature maps to the red/blue axis: +warm adds red and removes blue, -cool
    does the inverse.  tint maps to the green axis (inverted): +magenta reduces
    green, -green adds green.  A missing axis stays at 0 (neutral for that channel).

    Args:
        temperature: Warm/cool shift in [-1, 1]; None treated as 0.0.
        tint: Magenta/green shift in [-1, 1]; None treated as 0.0.

    Returns:
        WhiteBalanceParams with r/g/b midtone shifts clamped to [-1, 1].
    """
    t = temperature or 0.0
    n = tint or 0.0
    r = _clamp(+t, -1.0, 1.0)
    b = _clamp(-t, -1.0, 1.0)
    g = _clamp(-n, -1.0, 1.0)
    return WhiteBalanceParams(r=r, g=g, b=b)


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
      (B-4: resolved against the OTIO directory via check_timeline_source_matches)
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

    # Boundary check: validate each source reference (DC-AM-004 / CWE-22).
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

    # --- Validate target_url == media_path (B-4: CWD-independent via core helper) ---
    if urls:
        check_timeline_source_matches(next(iter(urls)), media_path, tl_dir)

    return tl
