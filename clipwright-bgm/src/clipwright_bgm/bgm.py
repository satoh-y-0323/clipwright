"""bgm.py — clipwright-bgm orchestration layer (design ADR-B1/B2/B3/B8/B10).

Flow:
  1. Input validation (timeline exists, bgm exists, extension whitelist,
     boundary check, output collision)
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
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import load_timeline, save_timeline
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
        output: Output OTIO timeline file path (must differ from timeline, M5).
        options: BGM options. When None, BgmOptions(volume_db=-6.0) is used.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _add_bgm_inner(timeline, bgm, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


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

    # Check timeline exists
    if not timeline_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Timeline file not found: {timeline_path.name}",
            hint="Check that the input timeline file path is correct.",
        )

    # Check bgm exists (existence check must come before extension check)
    if not bgm_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"BGM file not found: {bgm_path.name}",
            hint="Check that the BGM file path is correct.",
        )

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

    # BGM path boundary check: bgm must be under the same directory as timeline (ADR-B8)
    _check_bgm_within_timeline_dir(bgm_path, timeline_path)

    # Output collision check: output == input timeline is forbidden
    # (non-destructive, M5)
    if _same_path(output_path, timeline_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input timeline path are identical.",
            hint=(
                "Specify a different path for the output file from the input timeline."
            ),
        )

    # Output boundary check: output must be under the same directory
    # as timeline (SR L-3)
    _check_output_within_timeline_dir(output_path, timeline_path)

    # Output collision check: overwriting an existing file is forbidden
    # (non-destructive)
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

    ref = otio.schema.ExternalReference(target_url=str(bgm_path))
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


def _check_bgm_within_timeline_dir(bgm_path: Path, timeline_path: Path) -> None:
    """Verify that the BGM file is under the same directory as the timeline (ADR-B8).

    Boundary check: raises PATH_NOT_ALLOWED if the BGM path is outside
    the timeline directory.
    Falls back to absolute() when resolve() fails (Windows compatibility).

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED (out of boundary).
    """
    try:
        bgm_resolved = bgm_path.resolve()
        timeline_dir = timeline_path.resolve().parent
    except OSError:
        bgm_resolved = bgm_path.absolute()
        timeline_dir = timeline_path.absolute().parent

    # Check whether bgm is under timeline_dir
    try:
        bgm_resolved.relative_to(timeline_dir)
    except ValueError:
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message=(f"BGM file is outside the timeline directory: {bgm_path.name}"),
            hint="Place the BGM file in the same directory as the timeline.",
        ) from None


def _check_output_within_timeline_dir(output_path: Path, timeline_path: Path) -> None:
    """Verify that the output file is under the same directory as the timeline (SR L-3).

    Boundary check: raises PATH_NOT_ALLOWED if output is outside
    the timeline directory.
    Falls back to absolute() when resolve() fails (Windows compatibility).

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED (out of boundary).
    """
    try:
        output_resolved = output_path.resolve()
        timeline_dir = timeline_path.resolve().parent
    except OSError:
        output_resolved = output_path.absolute()
        timeline_dir = timeline_path.absolute().parent

    # Check whether output's parent directory is under (or equal to) timeline_dir.
    # The parent directory of output (not output itself) must be within timeline_dir.
    try:
        output_resolved.parent.relative_to(timeline_dir)
    except ValueError:
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message=(
                f"Output path is outside the timeline directory: {output_path.name}"
            ),
            hint="Place the output file in the same directory as the timeline.",
        ) from None


def _same_path(a: Path, b: Path) -> bool:
    """Return True if both paths point to the same entity.

    Falls back to string comparison when resolve fails.
    """
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
