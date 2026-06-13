"""render.py — orchestration layer for clipwright-render.

Integrates ffprobe-based probing and ffmpeg-based re-encoding, handling the
full flow: input validation → OTIO analysis → probe → plan construction →
execution.

Design decisions:
- _probe() calls core inspect_media and converts MediaInfo → ProbeInfo (AD-3).
  Removes the previous custom ffprobe call to eliminate duplication
  (DC-AS-001/ADR-6 interim workaround resolved).
- ffmpeg timeout = max(300, ceil(output duration seconds × 10)) seconds
  (ADR-4/DC-AM-006). Safety margin based on worst-case re-encode time
  (~10× real time).
- PROBE_FAILED and similar errors propagated as-is from inspect_media.
- ffmpeg stderr raw strings and internal paths are not exposed in summary/data/
  error. core's process.run includes only a 200-character summary in the
  message.
- Boundary validation, existence checks, and probing are applied to all unique
  sources (ADR-C8).
- ffmpeg command -i list is taken directly from RenderPlan.input_sources
  (ADR-C9-r2).
- _probe sets fps only when "first video StreamInfo present AND duration not
  None", so it does not mis-adopt the rate=1000.0 sentinel from audio-only
  sources (ADR-C2-r2).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import get_clipwright_metadata, load_timeline
from clipwright.process import resolve_tool, run
from clipwright.schemas import ToolResult
from pydantic import ValidationError as PydanticValidationError

from clipwright_render.plan import (
    BgmClip,
    ProbeInfo,
    build_plan,
    resolve_bgm,
    resolve_kept_ranges,
    unique_sources_in_order,
)
from clipwright_render.schemas import RenderOptions

# Output extension whitelist (DC-AM-003)
_ALLOWED_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm"})

# Subtitle file extension whitelist (ADR-S3)
_ALLOWED_SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".ass"})


def _probe(source: str) -> ProbeInfo:
    """Call inspect_media and return ProbeInfo (AD-3 / ADR-C2-r2).

    Pure adapter that delegates ffprobe execution to core's inspect_media
    and converts the returned MediaInfo to the ProbeInfo format required by
    plan.py. On FILE_NOT_FOUND, replaces the message with basename only and
    re-raises to avoid exposing the absolute path from the OTIO target_url
    (Sec M-1). Other errors (PROBE_FAILED, etc.) are propagated as-is.

    fps is adopted from MediaInfo.duration.rate only when "first video StreamInfo
    is present AND MediaInfo.duration is not None". For audio-only sources,
    duration.rate=1000.0 is a sentinel and must not be used as fps, so fps=None
    is returned when there is no video stream (ADR-C2-r2).

    Args:
        source: path to the media file to probe.

    Returns:
        ProbeInfo(has_video, audio_count, bit_rate, width, height, fps).

    Raises:
        ClipwrightError: PROBE_FAILED / DEPENDENCY_MISSING / SUBPROCESS_FAILED
            / SUBPROCESS_TIMEOUT / FILE_NOT_FOUND (raised by inspect_media).
    """
    try:
        info = inspect_media(source)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=(f"Source media file not found: {Path(source).name}"),
                hint=exc.hint,
            ) from exc
        raise
    has_video = any(s.codec_type == "video" for s in info.streams)
    audio_count = sum(1 for s in info.streams if s.codec_type == "audio")

    # Resolution and fps are taken from the first video StreamInfo (ADR-C2-r2)
    width: int | None = None
    height: int | None = None
    fps: float | None = None

    if has_video:
        # Retrieve the first video StreamInfo
        first_video = next((s for s in info.streams if s.codec_type == "video"), None)
        if first_video is not None:
            width = first_video.width
            height = first_video.height

        # fps: adopted only when video stream present AND duration not None
        # (ADR-C2-r2). Audio-only sources use rate=1000.0 as a sentinel; never
        # adopt as fps when there is no video stream.
        if info.duration is not None:
            fps = float(info.duration.rate)

    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=info.bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _check_within_timeline_dir(
    timeline_path: Path,
    path_to_check: str,
    kind: str,
    hint_detail: str,
) -> None:
    """Common helper to verify that the given path is within the timeline's
    parent directory.

    Consolidates the shared logic of _check_source_within_timeline_dir and
    _check_subtitle_within_timeline_dir (DRY; CR-M-001).
    Allows recursive subdirectories; raises PATH_NOT_ALLOWED only when the path
    points outside the timeline directory tree.
    Falls back to absolute()-based best-effort comparison when resolve() fails
    (SR L-1).

    Args:
        timeline_path: path to the OTIO timeline file.
        path_to_check: path string to validate against the boundary.
        kind: type label for the error message
            (e.g. "source file" / "subtitle file").
        hint_detail: specific file type description for the error hint
            (e.g. "source file").

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when the path points outside the
            project boundary.
    """
    try:
        allowed_base = timeline_path.parent.resolve()
        target_resolved = Path(path_to_check).resolve()
        # Compare with path separator to avoid false prefix matches on directory names
        target_str = str(target_resolved)
        base_str = str(allowed_base)
        if not (
            target_str == base_str
            or target_str.startswith(base_str + "/")
            or target_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=f"{kind} points outside the project boundary.",
                hint=(
                    f"Use a {hint_detail} located under the same directory"
                    " as the OTIO timeline."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve() failure (network paths, extremely long paths, symlink loops,
        # etc.): fall back to absolute()-based best-effort comparison (SR L-1).
        # This reduces the risk of completely skipping boundary validation
        # and guards against out-of-boundary probing even in extreme cases.
        # Only skips the check when absolute() also fails, and defers to
        # subsequent existence checks (follows the existing
        # _check_path_not_allowed fallback pattern).
        try:
            allowed_base_abs = str(timeline_path.parent.absolute())
            target_abs = str(Path(path_to_check).absolute())
            if not (
                target_abs == allowed_base_abs
                or target_abs.startswith(allowed_base_abs + "/")
                or target_abs.startswith(allowed_base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message=f"{kind} points outside the project boundary.",
                    hint=(
                        f"Use a {hint_detail} located under the same directory"
                        " as the OTIO timeline."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            # Skip only when absolute() also fails (truly unresolvable path)
            pass


def _check_source_within_timeline_dir(timeline_path: Path, source: str) -> None:
    """Verify that the source path is within the timeline's parent directory
    (Sec M-2).

    Guards against malicious OTIO files with arbitrary paths embedded as
    target_url. Assumes a single source is co-located under the same directory
    as the OTIO file.

    Args:
        timeline_path: path to the OTIO timeline file.
        source: media source path obtained from the OTIO target_url.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when source points outside the
            project boundary.
    """
    _check_within_timeline_dir(
        timeline_path,
        source,
        kind="source file",
        hint_detail="source file",
    )


def _check_subtitle_within_timeline_dir(timeline_path: Path, subtitle: str) -> None:
    """Verify that the subtitle path is within the timeline's parent directory
    (ADR-S7).

    Raises PATH_NOT_ALLOWED only when the path points outside the timeline
    directory tree; subtitle files in subdirectories are allowed.

    Args:
        timeline_path: path to the OTIO timeline file.
        subtitle: subtitle file path.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when the subtitle points outside the
            project boundary.
    """
    _check_within_timeline_dir(
        timeline_path,
        subtitle,
        kind="subtitle file",
        hint_detail="subtitle file",
    )


def _check_path_not_allowed(output_path: Path, source: str) -> None:
    """Verify that output and source do not point to the same path
    (DC-AM-002).

    Uses resolve() for comparison that accounts for symbolic links.
    Falls back to absolute() when resolve() fails (path does not exist etc.),
    and to string comparison only when absolute() also fails (Sec L-1).
    """
    try:
        if output_path.resolve() == Path(source).resolve():
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Output path and input source path are the same.",
                hint=(
                    "Change the output file path to be different from the"
                    " input source file."
                ),
            )
    except OSError as exc:
        # resolve() failure (network paths, extremely long paths, etc.):
        # try absolute() first, then fall back to string comparison.
        try:
            if Path(output_path).absolute() == Path(source).absolute():
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Output path and input source path are the same.",
                    hint=(
                        "Change the output file path to be different from the"
                        " input source file."
                    ),
                ) from exc
        except OSError as exc2:
            if str(output_path) == source:
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Output path and input source path are the same.",
                    hint=(
                        "Change the output file path to be different from the"
                        " input source file."
                    ),
                ) from exc2


def render_timeline(
    timeline: str,
    output: str,
    options: RenderOptions,
    dry_run: bool = False,
) -> ToolResult:
    """Materialise an OTIO timeline with FFmpeg (§3 data flow).

    Non-destructive: the input timeline file and source media are never
    modified. The output is a newly generated video file whose path is returned
    in artifacts.

    Flow:
      1. Input validation (timeline/output existence, extension, overwrite, path
         collision)
      2. load_timeline → resolve_kept_ranges → validate/probe all unique sources
      3. build_plan(ranges, probe_info, options, source_probes=source_probes)
      4a. dry_run=True  → return plan summary as ok_result (ffmpeg not called)
      4b. dry_run=False → run ffmpeg once → verify output exists → ok_result

    Args:
        timeline: input OTIO timeline file path.
        output: output video file path.
        options: RenderOptions (codec/resolution/fps/crf/overwrite).
        dry_run: when True, returns the plan only without calling ffmpeg.

    Returns:
        ok_result or error_result envelope dict.

    Raises:
        None (all ClipwrightErrors are converted to error_result and returned).
    """
    try:
        return _render_inner(timeline, output, options, dry_run)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except PydanticValidationError:
        return error_result(
            ErrorCode.INTERNAL,
            "An internal schema error occurred during render.",
            "Please report with reproduction steps.",
        )


def _render_inner(
    timeline: str,
    output: str,
    options: RenderOptions,
    dry_run: bool,
) -> ToolResult:
    """Internal implementation of render. Raises ClipwrightError directly.

    BGM orchestration extension (§7 ADR-B4-r2/B5-r2/B6-r2/B8):
    - Detects BGM clips from the A2 Audio track using resolve_bgm(tl).
    - When a BGM clip is present, applies all-source boundary validation to the
      BGM source as well (ADR-B8).
    - Passes bgm=BgmClip to build_plan (None also works identically; backward
      compatible; ADR-B7).
    - When plan.bgm_source is set, prepends -stream_loop -1 and appends BGM as
      the last -i (ADR-B6-r2/DC-AS-005).
    """
    timeline_path = Path(timeline)
    output_path = Path(output)

    # --- 1. Input validation (timeline existence check) ---

    # Verify timeline file exists
    if not timeline_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Timeline file not found: {timeline_path.name}",
            hint="Specify a valid .otio file path.",
        )

    # --- 2. OTIO analysis (upfront) ---
    # OTIO analysis is done early so that the BGM source / output comparison can
    # be performed before the output extension check (ADR-B8: PATH_NOT_ALLOWED
    # for output==BGM takes precedence).
    tl = load_timeline(timeline)
    ranges = resolve_kept_ranges(tl)

    # Obtain all unique sources in order of appearance (ADR-C9-r2).
    # unique_sources_in_order is the single source of truth in plan.py
    # (ADR-C9-r2)
    unique_sources = unique_sources_in_order(ranges)

    # --- 2b. Detect BGM clip (ADR-B4-r2) ---
    # Detects kind=="bgm" clips from A2 Audio tracks. Multiple BGM clips raise
    # UNSUPPORTED_OPERATION (resolve_bgm).
    bgm_clip: BgmClip | None = resolve_bgm(tl)

    # Early path collision check for BGM source vs output (ADR-B8;
    # PATH_NOT_ALLOWED priority). output == BGM source is detected before the
    # extension check (non-destructive guarantee).
    if isinstance(bgm_clip, BgmClip):
        _check_path_not_allowed(output_path, bgm_clip.source)

    # --- 3. Output input validation ---

    # Verify output extension whitelist (DC-AM-003)
    output_ext = output_path.suffix.lower()
    if output_ext not in _ALLOWED_EXTENSIONS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Output file extension is invalid: {output_ext!r}."
                f" Allowed extensions: {sorted(_ALLOWED_EXTENSIONS)}"
            ),
            hint=(
                "Set the output file path extension to one of"
                " .mp4 / .mkv / .mov / .webm."
            ),
        )

    # Verify output parent directory exists (not auto-created; DC-GP-005)
    # Full path is not included in error.message (Sec M-1)
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=(
                "Output directory does not exist."
                " Check the parent directory of the specified output."
            ),
            hint="Create the output directory first, then re-run.",
        )

    # --- 4. Apply boundary validation, existence check, and path collision check
    #        to all unique sources (ADR-C8) ---
    for src in unique_sources:
        # Verify source is within the same directory as the timeline (Sec M-2)
        _check_source_within_timeline_dir(timeline_path, src)

        # output == source check (PATH_NOT_ALLOWED; DC-AM-002)
        _check_path_not_allowed(output_path, src)

        # Verify source file exists (DC-GP-005)
        if not Path(src).exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"Source media file not found: {Path(src).name}",
                hint=(
                    "Place the source file recorded in the OTIO timeline in the"
                    " expected location."
                ),
            )

    # --- 4b. Detailed boundary validation for BGM source (ADR-B8) ---
    # Apply the same all-source boundary validation to the BGM source.
    # Early path collision check (step 2b) already verified output == BGM.
    if isinstance(bgm_clip, BgmClip):
        bgm_src = bgm_clip.source
        _check_source_within_timeline_dir(timeline_path, bgm_src)
        # output == bgm_src was already checked early, but re-applied here for
        # defence-in-depth
        _check_path_not_allowed(output_path, bgm_src)
        if not Path(bgm_src).exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"BGM source file not found: {Path(bgm_src).name}",
                hint=(
                    "Place the BGM source file recorded in the OTIO timeline"
                    " in the expected location."
                ),
            )

    # --- 4c. Boundary validation, existence check, extension WL, and fonts_dir
    #         validation for subtitle options ---
    # When options.subtitle is non-None, validates and resolves to absolute path
    # (ADR-S4-r2/S5-r2). subtitle=None is completely skipped (backward
    # compatible; ADR-S8).
    if options.subtitle is not None:
        sub_path_raw = options.subtitle.path

        # Verify subtitle is in the same directory as the timeline file (ADR-S7)
        _check_subtitle_within_timeline_dir(timeline_path, sub_path_raw)

        # Verify subtitle file exists (FILE_NOT_FOUND; basename only; CWE-209)
        sub_path_obj = Path(sub_path_raw)
        if not sub_path_obj.exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"Subtitle file not found: {sub_path_obj.name}",
                hint=("Specify a valid subtitle file path (.srt / .vtt / .ass)."),
            )

        # Extension whitelist validation (INVALID_INPUT; ADR-S3)
        sub_ext = sub_path_obj.suffix.lower()
        if sub_ext not in _ALLOWED_SUBTITLE_EXTENSIONS:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"Subtitle file extension is invalid: {sub_ext!r}."
                    f" Allowed: {sorted(_ALLOWED_SUBTITLE_EXTENSIONS)}"
                ),
                hint="Set the subtitle file extension to one of .srt / .vtt / .ass.",
            )

        # When fonts_dir is specified: validate that it is an existing directory
        # (ADR-S7). Boundary is not enforced (it is natural to point to a
        # system/bundled font location)
        if options.subtitle.fonts_dir is not None:
            fonts_dir_path = Path(options.subtitle.fonts_dir)
            if not fonts_dir_path.is_dir():
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "The specified fonts_dir does not exist or is not a directory."
                    ),
                    hint="Specify a valid font directory path in fonts_dir.",
                )

        # Resolve subtitle path and fonts_dir to absolute paths and update
        # options (ADR-S5-r2). resolve() is applied so filename=/fontsdir= can be
        # opened without depending on cwd. A new instance is created via
        # model_copy (Pydantic model is immutable).
        subtitle_abs = str(sub_path_obj.resolve())
        update_dict: dict[str, Any] = {"path": subtitle_abs}
        if options.subtitle.fonts_dir is not None:
            # Also resolve fonts_dir to absolute (SR-INJ-002; ADR-S5-r2 scope extension)
            update_dict["fonts_dir"] = str(Path(options.subtitle.fonts_dir).resolve())
        updated_subtitle = options.subtitle.model_copy(update=update_dict)
        options = options.model_copy(update={"subtitle": updated_subtitle})

    # output exists + overwrite=False → INVALID_INPUT (DC-AM-002)
    if output_path.exists() and not options.overwrite:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Output file already exists: {output_path.name}",
            hint="Specify overwrite=True to overwrite the existing file.",
        )

    # --- 3. Probe all unique sources and build source_probes (ADR-C8 /
    #         ADR-C2-r2) ---
    source_probes: dict[str, ProbeInfo] = {}
    for src in unique_sources:
        source_probes[src] = _probe(src)

    # Pass the first source's ProbeInfo as probe_info (backward compatible for
    # single-source path)
    first_source = unique_sources[0]
    probe_info = source_probes[first_source]

    # --- 4. Read denoise / loudness metadata ---
    # Read denoise / loudness from timeline-level metadata["clipwright"].
    # When None, each is absent (backward compatible; ADR-L6). When present,
    # each Directive is validated inside build_plan; invalid values raise
    # INVALID_INPUT.
    clipwright_meta = get_clipwright_metadata(tl)
    raw_denoise = clipwright_meta.get("denoise")
    raw_loudness = clipwright_meta.get("loudness")

    # --- 5. build_plan ---
    # Pass source_probes to enable multi-source path (ADR-C2-r2 / ADR-C9-r2).
    # Pass bgm_clip for BGM audio chain integration (ADR-B5-r2; bgm=None for
    # backward compat)
    plan = build_plan(
        ranges,
        probe_info,
        options,
        denoise=raw_denoise,
        loudness=raw_loudness,
        source_probes=source_probes,
        bgm=bgm_clip,
    )

    # --- 6a. dry_run ---
    if dry_run:
        size_info = (
            f", estimated size {plan.estimated_size_bytes / 1024 / 1024:.1f} MB"
            if plan.estimated_size_bytes is not None
            else ", estimated size unavailable"
        )
        summary = (
            f"[dry_run] {plan.segment_count} segment(s),"
            f" total duration {plan.total_duration_seconds:.2f}s{size_info}."
            f" Running ffmpeg would generate {output_path.name}."
        )
        return ok_result(
            summary,
            data={
                "ffmpeg_args": plan.ffmpeg_args,
                "filter_complex": plan.filter_complex,
                "segment_count": plan.segment_count,
                "total_duration_seconds": plan.total_duration_seconds,
                "estimated_size_bytes": plan.estimated_size_bytes,
            },
            warnings=plan.warnings,
        )

    # --- 6b. Execute ---
    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    timeout = max(300, math.ceil(plan.total_duration_seconds * 10))

    # Overwrite flag (-y / -n)
    overwrite_flag = ["-y"] if options.overwrite else ["-n"]

    # -i list taken directly from plan.input_sources (ADR-C9-r2).
    # Order is not recalculated in render.py (eliminates duplicate logic)
    inputs: list[str] = []
    for src in plan.input_sources:
        inputs += ["-i", src]

    # When BGM is present, prepend -stream_loop -1 and append BGM as the last
    # -i (ADR-B6-r2/DC-AS-005). -stream_loop is an input option; it must
    # immediately precede -i. The invariant BGM index == len(plan.input_sources)
    # is maintained (DC-AS-005).
    if plan.bgm_source is not None:
        inputs += ["-stream_loop", "-1", "-i", plan.bgm_source]

    cmd = [ffmpeg] + overwrite_flag + inputs + plan.ffmpeg_args + [str(output)]

    run(cmd, timeout=float(timeout))

    # Verify output file exists
    if not output_path.exists():
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message="ffmpeg exited successfully but the output file was not generated.",
            hint="Check the ffmpeg command arguments and output path.",
        )

    output_size = output_path.stat().st_size
    summary = (
        f"Concatenated {plan.segment_count} clip(s) and generated a video of"
        f" {plan.total_duration_seconds:.2f}s"
        f" ({output_size / 1024 / 1024:.1f} MB)."
    )
    return ok_result(
        summary,
        data={
            "segment_count": plan.segment_count,
            "total_duration_seconds": plan.total_duration_seconds,
            "output_size_bytes": output_size,
        },
        artifacts=[
            {
                "role": "output",
                "path": str(output_path),
                "format": Path(output_path).suffix.lstrip("."),
            }
        ],
        warnings=plan.warnings,
    )
