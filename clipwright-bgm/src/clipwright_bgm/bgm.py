"""bgm.py — clipwright-bgm orchestration layer (design ADR-B1/B2/B3/B10).

Flow:
  1. Input validation (timeline exists, bgm exists, extension whitelist,
     output-vs-source collision, output parent directory existence,
     output overwrite guard)
  2. Load timeline
  3. Re-invocation detection
     (kind=='bgm' clip exists → INVALID_INPUT, ADR-B2-r3)
  4. Fetch BGM duration via core inspect_media
     — direct ffprobe subprocess call is forbidden (ADR-B2-r2)
  5. Add A2 Audio track and place BGM clip
     (BgmDirective co-locate, ADR-B3/B9-r2)
  6. save_timeline (new output file, input timeline unchanged, M5)
  7. Return ok_result

Design decisions:
- bgm.py does not call ffmpeg/ffprobe via subprocess (OTIO operations only).
- Error messages must not expose absolute paths — basename only (CWE-209, ADR-B10).
- Re-invocation detection is based on kind=='bgm' clip presence,
  not track name "A2" (ADR-B2-r3).
- BGM extension whitelist rejects disallowed extensions (DC-AM-007, ADR-B2-r3).
- BGM may reside in any directory (external files accepted; ADR-B8 co-location
  constraint removed in favour of pathpolicy.check_output_not_source).
- OTIO target_url is produced via pathpolicy.media_ref_for_otio:
    relative POSIX path when bgm is under output's parent dir,
    absolute path otherwise (no ../ traversal).
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import load_timeline, save_timeline
from clipwright.pathpolicy import (
    check_output_not_source,
    media_ref_for_otio,
    validate_source_file,
)
from clipwright.schemas import ToolResult

import clipwright_bgm
from clipwright_bgm.schemas import BgmDirective, BgmOptions, DuckingDirective

# Allowed BGM input extension whitelist (DC-AM-007, ADR-B2-r3).
# Primarily audio files; video containers are included because they
# may carry audio tracks.
_ALLOWED_BGM_EXTENSIONS: frozenset[str] = frozenset(
    {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "mp4", "mkv", "mov", "webm"}
)


def add_bgm(
    timeline: str,
    bgm: str,
    output: str,
    options: BgmOptions | None = None,
) -> ToolResult:
    """Public API to add a BGM clip to an OTIO timeline.

    Converts ClipwrightError to an ok=False envelope.
    BGM duration is fetched via core inspect_media;
    direct ffprobe calls are forbidden (ADR-B2-r2).

    Args:
        timeline: Input OTIO timeline file path.
        bgm: BGM file path (audio or video; see allowed extension whitelist).
            May reside in any directory (external files accepted).
        output: Output OTIO timeline file path.  Must differ from timeline and
            bgm (non-destructive, M5).  May reside in any directory whose
            parent already exists (accumulate contract).
        options: BGM options. When None, BgmOptions(volume_db=-6.0) is used.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _add_bgm_inner(timeline, bgm, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording to
        # prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Adding background music failed due to an internal error.",
            "Retry after verifying that the input and output paths are accessible.",
        )


def _add_bgm_inner(
    timeline: str,
    bgm: str,
    output: str,
    options: BgmOptions | None,
) -> ToolResult:
    """Internal implementation of add_bgm. Propagates ClipwrightError as-is."""
    resolved_options = options if options is not None else BgmOptions(volume_db=-6.0)

    timeline_path = Path(timeline)
    bgm_path = Path(bgm)
    output_path = Path(output)

    # --- 1. Input validation ---

    # Check timeline exists. Delegates to the shared core guard
    # (validate_source_file) so symlinked inputs are rejected with
    # PATH_NOT_ALLOWED (ADR-PP-2 / CWE-59), instead of re-implementing the
    # symlink check locally.
    try:
        validate_source_file(timeline)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            # Re-wrap to keep the basename-only message contract: core's
            # FILE_NOT_FOUND message embeds the full caller-supplied path,
            # which would leak directory structure (CWE-209). __cause__ is
            # dropped via `from None` so the core message never surfaces.
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"Timeline file not found: {timeline_path.name}",
                hint="Check that the input timeline file path is correct.",
            ) from None
        # PATH_NOT_ALLOWED (symlink) and any other core error propagate as-is;
        # core's message/hint are not overridden here.
        raise

    # Check bgm exists (existence check must come before extension check).
    # Same symlink-guard delegation as the timeline check above.
    try:
        validate_source_file(bgm)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            # Re-wrap to keep the basename-only message contract (CWE-209);
            # __cause__ is dropped via `from None`.
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"BGM file not found: {bgm_path.name}",
                hint="Check that the BGM file path is correct.",
            ) from None
        # PATH_NOT_ALLOWED (symlink) and any other core error propagate as-is.
        raise

    # BGM extension whitelist validation (DC-AM-007, ADR-B2-r3)
    bgm_ext = bgm_path.suffix.lstrip(".").lower()
    if bgm_ext not in _ALLOWED_BGM_EXTENSIONS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Disallowed BGM file format: .{bgm_ext}",
            hint=(
                f"BGM file must have one of the following extensions: "
                f"{', '.join(sorted(_ALLOWED_BGM_EXTENSIONS))}"
            ),
        )

    # Output-vs-source collision: output must not overwrite timeline or bgm
    # (non-destructive, M5).  Raises PATH_NOT_ALLOWED when they coincide.
    check_output_not_source(output_path, [str(timeline_path), str(bgm_path)])

    # Output parent directory must already exist (accumulate contract)
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Output parent directory does not exist: {output_path.parent.name}"
            ),
            hint="Create the output directory before calling add_bgm.",
        )

    # Output overwrite guard: refuse to clobber an existing file (non-destructive)
    if output_path.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Output file already exists: {output_path.name}",
            hint=(
                "Specify a different output file path that does not"
                " conflict with an existing file."
            ),
        )

    # --- 2. Load timeline ---

    tl = load_timeline(str(timeline_path))

    # --- 3. Re-invocation detection (DC-AS-002/AM-005, ADR-B2-r3) ---
    # Raise INVALID_INPUT if a kind=='bgm' clip already exists.
    # Detection is kind-based, not track-name-based ("A2").
    existing_bgm_clips = _collect_bgm_clips(tl)
    if existing_bgm_clips:
        # Do not expand existing clip names in hint
        # (prevents control-character injection from OTIO data, SR L-2, CWE-20)
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="A BGM clip already exists in the timeline.",
            hint=(
                "An existing BGM clip was found. "
                "Specify a timeline that does not already contain a BGM clip."
            ),
        )

    # --- 4. Fetch BGM duration via core inspect_media (ADR-B2-r2) ---
    # On inspect_media failure, catch ClipwrightError and reformat
    # to hide the absolute path.

    try:
        media_info = inspect_media(str(bgm_path))
    except ClipwrightError as exc:
        # Replace message with basename-only to avoid exposing absolute paths
        # (CWE-209, ADR-B10)
        safe_message = f"Failed to retrieve BGM file info: {bgm_path.name}"
        raise ClipwrightError(
            code=exc.code,
            message=safe_message,
            hint=exc.hint,
        ) from None

    # Convert duration to seconds
    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Could not retrieve duration of BGM file: {bgm_path.name}",
            hint="Specify a BGM file that has a valid audio stream.",
        )

    bgm_duration_sec = media_info.duration.value / media_info.duration.rate
    bgm_rate = media_info.duration.rate

    # --- 5. Add A2 Audio track and place BGM clip ---

    # source_range is fixed to full BGM media length (0–bgm_duration)
    # (DC-AS-003, ADR-B2-r2)
    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, bgm_rate),
        duration=otio.opentime.RationalTime(bgm_duration_sec * bgm_rate, bgm_rate),
    )

    # Build BgmDirective and co-locate in BGM clip metadata (ADR-B3/B9-r2)
    directive = BgmDirective(
        tool="clipwright-bgm",
        version=clipwright_bgm.__version__,
        kind="bgm",
        volume_db=resolved_options.volume_db,
        fade_in_sec=resolved_options.fade_in_sec,
        fade_out_sec=resolved_options.fade_out_sec,
        ducking=DuckingDirective(
            enabled=resolved_options.ducking.enabled,
            threshold=resolved_options.ducking.threshold,
            ratio=resolved_options.ducking.ratio,
        ),
    )

    ref = otio.schema.ExternalReference(
        target_url=media_ref_for_otio(bgm_path, output_path.parent)
    )
    bgm_clip = otio.schema.Clip(
        name=bgm_path.name,
        media_reference=ref,
        source_range=source_range,
        metadata={"clipwright": directive.model_dump()},
    )

    # Add A2 Audio track and append BGM clip
    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    a2.append(bgm_clip)
    tl.tracks.append(a2)

    # --- 6. save_timeline (new output file, input timeline unchanged, M5) ---

    save_timeline(tl, str(output_path))

    # --- 7. Return ok_result ---

    summary = (
        f"BGM added."
        f" bgm={bgm_path.name}"
        f", volume_db={resolved_options.volume_db}"
        f", fade_in={resolved_options.fade_in_sec}s"
        f", fade_out={resolved_options.fade_out_sec}s"
        f", ducking={'ON' if resolved_options.ducking.enabled else 'OFF'}"
        f", bgm_duration={bgm_duration_sec:.2f}s."
        f" Output timeline: {output_path.name}"
    )

    return ok_result(
        summary,
        data={
            "bgm": bgm_path.name,
            "volume_db": resolved_options.volume_db,
            "fade_in_sec": resolved_options.fade_in_sec,
            "fade_out_sec": resolved_options.fade_out_sec,
            "ducking_enabled": resolved_options.ducking.enabled,
            "bgm_duration_sec": bgm_duration_sec,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
        ],
        warnings=[],
    )


def _collect_bgm_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """Collect all Clips with kind=='bgm' from every Audio track in the timeline.

    Uses kind-based detection to avoid dependency on track names,
    supporting re-invocation detection (ADR-B2-r3).
    """
    bgm_clips: list[otio.schema.Clip] = []
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Audio:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    meta = item.metadata.get("clipwright", {})
                    if meta.get("kind") == "bgm":
                        bgm_clips.append(item)
    return bgm_clips
