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
- ADR-SR-1 (per-seam subprocess-error redaction, SR-R-001 / CWE-209): every
  render seam that reaches a child process (ffmpeg / ffprobe) individually
  routes its ClipwrightError through _sanitize_subprocess_error(exc), which
  replaces the message with the shared safe_subprocess_message(exc) ONLY for
  SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT, leaving code and hint unchanged and
  passing every other error code through untouched. The five seams are S1 the
  main ffmpeg run() in _render_inner, S2 the run() in render_plan, S3 the
  inspect_media() (ffprobe) in _probe, S4 the PiP re-probe inspect_media(), and
  S5 the ffmpeg -encoders run() in encoders.py (redacted inline to avoid a
  circular import). A single top-level swap in the tool boundary is deliberately
  NOT used: it would miss S2 (called outside the render() try), clobber render's
  own path-safe curated SUBPROCESS_FAILED messages (_verify_image_magic
  basename; the "output file was not generated" wording), and not cover the S5
  encoder list probe (`ffmpeg -encoders`). render-owned messages that are raised
  directly (not through
  a run() / inspect_media() seam) are never redacted. The stable contract for
  callers is code / hint, not message, so redacting message is backward
  compatible.
"""

from __future__ import annotations

import contextlib
import math
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import get_clipwright_metadata, load_timeline
from clipwright.pathpolicy import check_media_ref, check_output_not_source
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import ToolResult
from pydantic import ValidationError as PydanticValidationError

from clipwright_render import retiming as _retiming
from clipwright_render.encoders import (
    ResolvedEncoder,
    _resolve_hw_encoder,
    hwaccel_value,
)
from clipwright_render.plan import (
    MAX_CUES,
    MAX_WORDS,
    BgmClip,
    ProbeInfo,
    _build_karaoke_ass,
    _parse_word_vtt,
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

# Image overlay file extension whitelist (V2-3 / ADR-OV-4).
# Keep in sync with plan._ALLOWED_IMAGE_EXTENSIONS (cross-layer defence-in-depth).
_ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp"}
)

# PiP video overlay file extension whitelist (ADR-PIP-5 / PiP video inputs).
# Keep in sync with plan._ALLOWED_PIP_VIDEO_EXTENSIONS (cross-layer defence-in-depth).
# NOTE: This set is defined independently in three places (this
# render._ALLOWED_PIP_VIDEO_EXTENSIONS, plan._ALLOWED_PIP_VIDEO_EXTENSIONS, and
# clipwright-overlay's _ALLOWED_VIDEO_EXTENSIONS). When changing it, all three must
# be updated manually. Cross-package dependency is avoided per ADR-PIP-4; automatic
# sync tests are not provided.
_ALLOWED_PIP_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mkv", ".mov", ".webm"}
)

# Maximum .srt file size accepted for re-timing (SR-L-2)
_MAX_SRT_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# G Layer 1: when this many SRT cues are fragmented by cuts (split or clipped),
# emit a prescriptive advisory recommending the correct edit order
# (cut -> render -> transcribe -> burn). One fragmented cue is a benign edge;
# two or more signal a systematic cut-grid vs cue-grid mismatch.
_CUE_FRAGMENTATION_ADVISORY_THRESHOLD = 2


def _sanitize_subprocess_error(exc: ClipwrightError) -> ClipwrightError:
    """Replace a SUBPROCESS_FAILED/TIMEOUT message with a generic string (ADR-SR-1).

    core run() / inspect_media() messages may embed raw child stderr and absolute
    input paths; this substitutes the shared safe_subprocess_message so nothing
    leaks into the MCP envelope (CWE-209). hint is preserved (AI reads the next
    step); every other error code is returned unchanged (its message/hint is
    user-facing and path-safe by construction).
    """
    if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
        return ClipwrightError(
            code=exc.code,
            message=safe_subprocess_message(exc),
            hint=exc.hint,
        )
    return exc


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
        raise _sanitize_subprocess_error(exc) from exc  # S3 (ADR-SR-1)
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
        # Resolve relative paths against the timeline directory so that OTIO files
        # with relative target_urls (e.g. produced by clipwright-sequence using
        # media_ref_for_otio) are validated correctly (SR L-2).
        _p = Path(path_to_check)
        target_resolved = (
            (timeline_path.parent / _p).resolve()
            if not _p.is_absolute()
            else _p.resolve()
        )
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
            _p2 = Path(path_to_check)
            target_abs = str(
                (timeline_path.parent / _p2).absolute()
                if not _p2.is_absolute()
                else _p2.absolute()
            )
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


def _verify_image_magic(image_path: str) -> None:
    """Check the magic bytes of an image file to detect corrupt or non-image files.

    Reads only the first 12 bytes; raises SUBPROCESS_FAILED when the header
    does not match any known image signature, so that callers receive an immediate
    error rather than waiting for -loop 1 to hang indefinitely on corrupt input
    (V2-7 / DC-AM-004).

    Supported magic signatures:
      PNG  : b'\\x89PNG'
      JPEG : b'\\xff\\xd8\\xff'
      WebP : bytes 0-3 == b'RIFF' AND bytes 8-12 == b'WEBP'

    Args:
        image_path: Absolute path to the image file (already validated to exist).

    Raises:
        ClipwrightError: SUBPROCESS_FAILED when the file header does not match
            any supported image signature (corrupt or wrong format).
    """
    try:
        with open(image_path, "rb") as fh:
            header = fh.read(12)
    except OSError:
        # If we can't even read the file, let ffmpeg produce the error normally.
        return

    basename = Path(image_path).name

    is_png = header[:4] == b"\x89PNG"
    is_jpeg = header[:3] == b"\xff\xd8\xff"
    is_webp = header[:4] == b"RIFF" and header[8:12] == b"WEBP"

    if not (is_png or is_jpeg or is_webp):
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=(f"Image overlay file appears corrupt or unsupported: {basename}"),
            hint=(
                "The image overlay file does not have a valid PNG / JPEG / WebP header."
                " Replace it with a valid image file."
            ),
        )


def _build_ffmpeg_inputs(
    plan: Any,
    hw_decode_value: str | None,
) -> list[str]:
    """Build the ffmpeg -i input list from a RenderPlan.

    Order: input_sources → bgm (with -stream_loop -1) → image_sources →
    pip_sources (ADR-PIP-7).
    When hw_decode_value is set, prepend -hwaccel <value> before each source -i.
    BGM, image, and PiP inputs do NOT receive -hwaccel (mirrors existing
    behaviour).

    Args:
        plan: RenderPlan with input_sources, bgm_source, image_sources,
            pip_sources.
        hw_decode_value: hwaccel flag value (e.g. 'cuda', 'auto') or None.

    Returns:
        List of ffmpeg input arguments (flat list of strings).
    """
    inputs: list[str] = []

    # Regular video/audio sources (with optional hwaccel)
    for src in plan.input_sources:
        if hw_decode_value is not None:
            inputs += ["-hwaccel", hw_decode_value]
        inputs += ["-i", src]

    # BGM: -stream_loop -1 -i <bgm_source> (ADR-B6-r2; NO -hwaccel on BGM)
    if plan.bgm_source is not None:
        inputs += ["-stream_loop", "-1", "-i", plan.bgm_source]

    # Image overlays: -t {total_duration} -loop 1 -i <image_path>
    # -loop 1 repeats the single-frame PNG so that its internal timestamps advance
    # with the output clock.  Without this, all PNG frames carry timestamp=0 and
    # fade=t=in:st=N:d=D:alpha=1 never fires because st>0 is never reached.
    # -t caps the loop at the output duration so ffmpeg does not run indefinitely
    # (ADR-OV-5 / V2-1 fade fix). -t and -loop are both input-level options that
    # apply to the following -i regardless of their relative order; -t is placed
    # first so that "-loop 1" sits immediately before "-i" (ADR-PIP-7 ordering
    # contract exercised by test_pip_video.py).
    img_dur: str | None = (
        f"{plan.total_duration_seconds:g}" if plan.image_sources else None
    )
    for img in plan.image_sources:
        if img_dur is not None:
            inputs += ["-loop", "1", "-t", img_dur]
        inputs += ["-i", img]

    # PiP video overlays: plain -i <pip_source>, NO -loop 1 (ADR-PIP-7 — these
    # are real video files with their own timestamps, unlike the still-image
    # overlays above; the filtergraph re-bases them via trim+setpts instead).
    for pip in plan.pip_sources:
        inputs += ["-i", pip]

    return inputs


def render_plan(
    plan: Any,
    output: str,
    overwrite: bool = False,
) -> None:
    """Execute a pre-built RenderPlan via ffmpeg.

    Minimal orchestration layer used by tests (Section 9 / V2-7): validates
    output path, builds the ffmpeg command from plan, and calls run().

    Does NOT perform boundary validation, probe, or OTIO analysis — those are
    handled upstream by render_timeline / _render_inner.

    Args:
        plan: RenderPlan (from build_plan).
        output: output video file path.
        overwrite: when True, pass -y to ffmpeg; otherwise -n.

    Raises:
        ClipwrightError: SUBPROCESS_FAILED when ffmpeg fails (propagated from run()).
    """
    import math as _math

    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    overwrite_flag = ["-y"] if overwrite else ["-n"]

    inputs = _build_ffmpeg_inputs(plan, hw_decode_value=None)

    # F-4: absolutise output when stabilize_cwd is set (mirroring _render_inner).
    output_arg = (
        str(Path(output).resolve()) if plan.stabilize_cwd is not None else str(output)
    )

    # vid.stab #144: decoder frame-threading corrupts B-frame refs; serialize.
    global_decode_args = ["-threads", "1"] if plan.stabilize_cwd is not None else []
    timeout = max(300, _math.ceil(plan.total_duration_seconds * 10))
    cmd = (
        [ffmpeg]
        + overwrite_flag
        + global_decode_args
        + inputs
        + plan.ffmpeg_args
        + [output_arg]
    )
    try:
        run(cmd, timeout=float(timeout), cwd=plan.stabilize_cwd)
    except ClipwrightError as exc:
        raise _sanitize_subprocess_error(exc) from None  # S2 (ADR-SR-1)


def _generate_retimed_srt(
    src_srt: str,
    tmap: _retiming.ProgramTimeMap,
    output_path: Path,
    overwrite: bool,
) -> tuple[str | None, list[str]]:
    """Generate a re-timed .srt file and return (retimed_path, warnings).

    Reads src_srt (UTF-8, non-destructive), remaps each cue via remap_window,
    then writes the re-timed cues to {output_path.stem}.retimed.srt in the
    same directory as output_path (Decision B / ADR-3).

    Naming: output_path.parent / f"{output_path.stem}.retimed.srt".
    This uses the output file stem (not the subtitle source stem) to avoid
    name collisions when the same subtitle is used across multiple renders
    with different output names.

    The retimed .srt is placed alongside the primary output file.  This keeps
    all render outputs co-located and applies the same directory policy as the
    main output (SR-L-1).

    Overwrite policy (Decision B):
      - overwrite=False + existing retimed .srt → INVALID_INPUT with hint.
      - overwrite=True → replace silently.

    Decision A (all cues dropped):
      - Returns (None, warnings) when every cue is remapped to dropped.
      - Caller must skip the subtitle filter entirely and emit a warning.
      - No empty .srt is written.

    Warning format (FR-6 / §5 / B3):
      - dropped: "caption cue [{start}-{end}] dropped (source range removed)"
      - split:   "caption cue [{start}-{end}] split across cut boundary into
                  {N} windows"
      - clipped: "caption cue [{start}-{end}] clipped at cut boundary"
      - shifted: "caption cue [{start}-{end}] shifted by {delta:.3f}s"

    Args:
        src_srt:     Absolute path to the source .srt file.
        tmap:        ProgramTimeMap built from kept ranges.
        output_path: Output video path (supplies stem and parent directory).
        overwrite:   Whether to allow overwriting an existing retimed .srt.

    Returns:
        (retimed_path, warnings) where retimed_path is the absolute path
        string of the written .srt, or None when all cues were dropped.

    Raises:
        ClipwrightError: INVALID_INPUT when retimed .srt exists and
            overwrite=False, or the subtitle file is too large or unparseable.
    """
    retimed_path = output_path.parent / f"{output_path.stem}.retimed.srt"

    # SR-M-3: guard single quote in output stem before model_copy replaces path.
    # model_copy(update={"path": ...}) skips Pydantic field_validator, so
    # _validate_path_no_single_quote would not run on the constructed path.
    if "'" in output_path.stem:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output file name must not contain a single quote.",
            hint="Rename the output file to avoid single quotes in its name.",
        )

    # Overwrite guard (Decision B)
    if retimed_path.exists() and not overwrite:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(f"Re-timed subtitle file already exists: {retimed_path.name}"),
            hint=(
                "Specify overwrite=True to allow replacing the existing"
                " re-timed subtitle file."
            ),
        )

    # SR-L-2: reject oversized SRT files before reading into memory.
    srt_stat = Path(src_srt).stat()
    if srt_stat.st_size > _MAX_SRT_SIZE_BYTES:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The subtitle file is too large to process.",
            hint="Use a .srt file smaller than 50 MB.",
        )

    # Parse source SRT (non-destructive — read only).
    # SR-M-1: catch parse / decode errors and re-raise as fixed-text ClipwrightError
    # to prevent internal detail (timecode line content) from leaking (CWE-209).
    try:
        src_text = Path(src_srt).read_text(encoding="utf-8")
        cues = _retiming.parse_srt(src_text)
    except (ValueError, OSError, UnicodeDecodeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The subtitle file could not be parsed.",
            hint=(
                "Verify that the .srt file is valid UTF-8 and uses correct SRT format."
            ),
        ) from None

    new_cues: list[_retiming.SrtCue] = []
    warnings: list[str] = []
    fragmented_cues = 0  # G Layer 1: distinct cues that were split OR clipped

    for cue in cues:
        rr = _retiming.remap_window(tmap, cue.start, cue.end)
        # CR-L-3: use the public format_srt_timecode instead of _format_srt_timecode.
        start_tc = _retiming.format_srt_timecode(cue.start)
        end_tc = _retiming.format_srt_timecode(cue.end)
        label = f"[{start_tc}-{end_tc}]"

        if rr.dropped:
            warnings.append(f"caption cue {label} dropped (source range removed)")
            continue

        if rr.split:
            n_wins = len(rr.windows)
            warnings.append(
                f"caption cue {label} split across cut boundary into {n_wins} windows"
            )
        elif rr.clipped:
            warnings.append(f"caption cue {label} clipped at cut boundary")
        elif rr.shifted:
            first_start_s = float(rr.windows[0].program_start.to_seconds())
            orig_start_s = float(cue.start.to_seconds())
            delta = first_start_s - orig_start_s
            warnings.append(f"caption cue {label} shifted by {delta:.3f}s")

        # G Layer 1: count this cue once if it was fragmented (split or clipped).
        # Counted per-cue (not split+clipped separately) because a single cue can
        # be both split and clipped; summing the two flags would double-count it.
        if rr.split or rr.clipped:
            fragmented_cues += 1

        for win in rr.windows:
            new_cues.append(
                _retiming.SrtCue(
                    start=win.program_start,
                    end=win.program_end,
                    text=cue.text,
                )
            )

    # G Layer 1: if two or more distinct cues were fragmented, emit a prescriptive
    # advisory recommending the correct cut -> render -> transcribe -> burn order.
    if fragmented_cues >= _CUE_FRAGMENTATION_ADVISORY_THRESHOLD:
        warnings.append(
            f"{fragmented_cues} subtitle cue(s) were fragmented by cuts"
            " (split/clipped). For clean captions, cut first and render the cut"
            " program, then transcribe the rendered cut, then burn captions onto it"
            " (order: cut -> render -> transcribe -> burn)."
        )

    # Decision A: all cues dropped — do not write empty SRT, skip subtitle filter
    if not new_cues:
        warnings.append(
            "All subtitle cues were dropped by cuts; subtitle filter skipped."
        )
        return (None, warnings)

    # Write re-timed SRT
    srt_content = _retiming.serialize_srt(new_cues)
    retimed_path.write_text(srt_content, encoding="utf-8")

    return (str(retimed_path.resolve()), warnings)


def _subtitle_skip_warnings(
    options: RenderOptions,
    tmap: _retiming.ProgramTimeMap,
    unique_sources: list[str],
) -> list[str]:
    """Return skip-warning strings for conditions that prevent subtitle re-timing.

    Emitted when options.subtitle is non-None and retime_markers=="auto" but
    re-timing cannot proceed (ADR-4: global skip warnings in render.py only).

    Conditions (evaluated in order; first match returns):
      1. Multi-source timeline → "retime_markers skipped: multi-source
         timeline is not supported" (AC-9 / §4.2)
      2. Non-.srt subtitle → "subtitle re-timing skipped: only .srt is
         supported in this version"

    If retime_markers=="off" or has_cut=False/has_warp=False (identity), no
    skip warning is emitted (those are silent no-ops, not degradations).

    Args:
        options:        Current RenderOptions (after subtitle absolutisation).
        tmap:           ProgramTimeMap for the current timeline.
        unique_sources: list of unique source strings from unique_sources_in_order.

    Returns:
        List of skip-warning strings (may be empty).
    """
    if options.subtitle is None or options.retime_markers != "auto":
        return []
    if not (tmap.has_cut or tmap.has_warp):
        return []

    # Multi-source check
    if len(unique_sources) >= 2:
        return ["retime_markers skipped: multi-source timeline is not supported"]

    # Non-.srt subtitle
    sub_ext = Path(options.subtitle.path).suffix.lower()
    if sub_ext != ".srt":
        return ["subtitle re-timing skipped: only .srt is supported in this version"]

    return []


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
        ClipwrightError: INVALID_INPUT when a pre-existing re-timed subtitle
            file would be overwritten but overwrite=False.  This case is raised
            directly (not converted to error_result) so that callers that use
            render as a building block can distinguish it from an ok=False
            envelope.  All other ClipwrightErrors are converted to error_result.
    """
    # Pre-check: subtitle re-timing overwrite collision.
    # Performed BEFORE the try/except so that ClipwrightError propagates to the
    # caller instead of being converted to error_result.  The retimed path is
    # deterministic (output_stem.retimed.srt in the output directory) and can be
    # checked here without loading the timeline (Decision B / ADR-3).
    #
    # Only check when retime_markers=="auto" + subtitle set + overwrite=False.
    # The full do_retime condition (single source / has_cut / .srt) is evaluated
    # later inside _render_inner; the pre-check errs on the conservative side and
    # checks only the minimal necessary conditions.  A false-positive (retimed file
    # exists but re-timing would be skipped) is intentionally avoided by also
    # requiring suffix==".srt" here.
    if (
        options.subtitle is not None
        and options.retime_markers == "auto"
        and not options.overwrite
        and Path(options.subtitle.path).suffix.lower() == ".srt"
    ):
        _retimed_candidate = Path(output).parent / f"{Path(output).stem}.retimed.srt"
        if _retimed_candidate.exists():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"Re-timed subtitle file already exists: {_retimed_candidate.name}"
                ),
                hint=(
                    "Specify overwrite=True to allow replacing the existing"
                    " re-timed subtitle file."
                ),
            )

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
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording to
        # prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Rendering the timeline failed due to an internal error.",
            "Retry after verifying that the output directory is writable and "
            "has free space.",
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
    # V2-3: propagate the timeline file path into KeptRangeList so that
    # build_plan -> _collect_image_overlays can reconstruct relative image_paths
    # stored in image_overlay markers (DC-AS-003 / round-trip stability).
    ranges._timeline_path = str(timeline_path.resolve())

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
        check_output_not_source(output_path, [bgm_clip.source])

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
    #        to all unique sources (ADR-C8 / ADR-PP-1) ---
    for src in unique_sources:
        # output == source check (PATH_NOT_ALLOWED; DC-AM-002)
        check_output_not_source(output_path, [src])

        # Existence check first (FILE_NOT_FOUND; DC-GP-005).
        # Must run before check_media_ref because check_media_ref raises
        # PATH_NOT_ALLOWED for non-file absolute refs (ADR-PP-1), masking the
        # more informative FILE_NOT_FOUND for simply-missing sources.
        # Relative paths are resolved against the timeline directory (not CWD).
        _src_p = Path(src.replace("\\", "/"))
        _src_exists_p = (
            (timeline_path.parent / _src_p) if not _src_p.is_absolute() else _src_p
        )
        if not _src_exists_p.exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"Source media file not found: {_src_p.name}",
                hint=(
                    "Place the source file recorded in the OTIO timeline in the"
                    " expected location."
                ),
            )

        # Unified boundary/symlink check (ADR-PP-1): absolute refs to existing
        # real files are accepted regardless of location; relative refs must
        # resolve within the timeline directory (CWE-22 guard).
        check_media_ref(src, timeline_path.parent, "media")

    # Resolve any relative target_urls to absolute paths against the timeline
    # directory.  This MUST run after check_media_ref so that the CWE-22
    # guard fires on relative paths (including "../" traversal) before they are
    # converted to absolute.  After security validation, downstream code
    # (probe, plan.py unique_sources_in_order, ffmpeg -i args) needs only
    # absolute paths and must not depend on the working directory.
    _tl_dir = timeline_path.parent
    unique_sources = [
        str((_tl_dir / s).resolve()) if not Path(s).is_absolute() else s
        for s in unique_sources
    ]
    for _kr in ranges:
        if not Path(_kr.source).is_absolute():
            try:
                _kr.source = str((_tl_dir / _kr.source).resolve())
            except OSError:
                _kr.source = str((_tl_dir / _kr.source).absolute())

    # --- 4b. Detailed boundary validation for BGM source (ADR-B8 / ADR-PP-1) ---
    # Apply the same all-source boundary validation to the BGM source.
    # Early path collision check (step 2b) already verified output == BGM.
    if isinstance(bgm_clip, BgmClip):
        bgm_src = bgm_clip.source
        # output == bgm_src (defence-in-depth; early check at step 2b already ran)
        check_output_not_source(output_path, [bgm_src])
        # Existence check before check_media_ref (same rationale as step 4)
        _bgm_p = Path(bgm_src.replace("\\", "/"))
        _bgm_exists_p = (
            (timeline_path.parent / _bgm_p) if not _bgm_p.is_absolute() else _bgm_p
        )
        if not _bgm_exists_p.exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"BGM source file not found: {_bgm_p.name}",
                hint=(
                    "Place the BGM source file recorded in the OTIO timeline"
                    " in the expected location."
                ),
            )
        # Unified boundary/symlink check (ADR-PP-1)
        check_media_ref(bgm_src, timeline_path.parent, "media")
        # Resolve a relative target_url to an absolute path against the timeline
        # directory and write it back onto the (frozen) BgmClip. This mirrors the
        # main-media resolution above (L823-833): downstream code (plan.py
        # bgm_source_out -> ffmpeg -stream_loop -1 -i <bgm_source>) runs with
        # cwd=None and must not depend on the working directory. Without this,
        # a co-located BGM (media_ref_for_otio stores a relative POSIX path when
        # bgm and timeline share a directory) reaches ffmpeg as a relative path
        # and fails to open (SUBPROCESS_FAILED).
        if not _bgm_p.is_absolute():
            try:
                _bgm_abs = str((_tl_dir / _bgm_p).resolve())
            except OSError:
                _bgm_abs = str((_tl_dir / _bgm_p).absolute())
            bgm_clip = replace(bgm_clip, source=_bgm_abs)

    # --- 4c. Boundary validation, existence check, extension WL, and fonts_dir
    #         validation for subtitle options ---
    # When options.subtitle is non-None, validates and resolves to absolute path
    # (ADR-S4-r2/S5-r2). subtitle=None is completely skipped (backward
    # compatible; ADR-S8).
    if options.subtitle is not None:
        sub_path_raw = options.subtitle.path
        sub_path_obj = Path(sub_path_raw)

        # Existence check before check_media_ref (FILE_NOT_FOUND > PATH_NOT_ALLOWED;
        # resolves relative paths against the timeline directory, not CWD).
        _sub_p = Path(sub_path_raw.replace("\\", "/"))
        _sub_exists_p = (
            (timeline_path.parent / _sub_p) if not _sub_p.is_absolute() else _sub_p
        )
        if not _sub_exists_p.exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"Subtitle file not found: {sub_path_obj.name}",
                hint=("Specify a valid subtitle file path (.srt / .vtt / .ass)."),
            )

        # Unified boundary/symlink check (ADR-PP-1): absolute external subtitle allowed
        check_media_ref(sub_path_raw, timeline_path.parent, "subtitle")

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

        # karaoke=True requires a word-level WebVTT (.vtt) input (F-R-04 / ADR-K7).
        # Checked after the extension WL guard so non-WL extensions receive the
        # generic WL error first (priority: FILE_EXT_INVALID > KARAOKE_EXT).
        if options.subtitle.karaoke and sub_ext != ".vtt":
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"karaoke=True requires a word-level WebVTT file (.vtt),"
                    f" but got extension {sub_ext!r}."
                ),
                hint=(
                    "Supply a word-level .vtt file produced by"
                    " clipwright_transcribe with word_timestamps=True."
                ),
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

    # --- 4e. Subtitle re-timing stage (§2.2 / ADR-3 / ADR-4) ---
    # Inserted after subtitle absolutisation (step 4c) and before build_plan (step 5).
    # At this point options.subtitle.path is an absolute .srt/.vtt/.ass path.
    # Build the source→program map once for this timeline; use it for both the
    # do_retime decision and the skip-warning evaluation.
    subtitle_warnings: list[str] = []
    if options.subtitle is not None:
        _tmap = _retiming.build_program_time_map(ranges)
        sub_suffix = Path(options.subtitle.path).suffix.lower()
        do_retime = (
            options.retime_markers == "auto"
            and (_tmap.has_cut or _tmap.has_warp)
            and len(unique_sources) == 1
            and sub_suffix == ".srt"
        )
        if do_retime:
            retimed_path, subtitle_warnings = _generate_retimed_srt(
                options.subtitle.path,
                _tmap,
                output_path,
                options.overwrite,
            )
            if retimed_path is not None:
                # Re-timed SRT was written — replace subtitle path in options
                retimed_abs = str(Path(retimed_path).resolve())
                options = options.model_copy(
                    update={
                        "subtitle": options.subtitle.model_copy(
                            update={"path": retimed_abs}
                        )
                    }
                )
            else:
                # Decision A: all cues dropped — skip subtitle filter
                options = options.model_copy(update={"subtitle": None})
        else:
            subtitle_warnings = _subtitle_skip_warnings(options, _tmap, unique_sources)

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
    raw_color = clipwright_meta.get("color")
    raw_stabilize = clipwright_meta.get("stabilize")  # §6-A
    raw_reframe = clipwright_meta.get("reframe")  # §7.2
    raw_transition = clipwright_meta.get("transition")  # ADR-RT-2

    # --- 4d. Boundary and existence checks for trf_path (CR-E-001 / SR-V-002) ---
    # When stabilize directive is present and trf_path is non-None, apply the same
    # timeline-directory boundary check as source files, then verify the file exists.
    # This prevents crafted OTIO (stabilize.trf_path="/etc/shadow") from directing
    # cwd to an arbitrary directory. Checked here so that ffmpeg is never invoked
    # with an out-of-boundary or missing trf file (early error; FILE_NOT_FOUND, not
    # SUBPROCESS_FAILED). Symmetric with source existence check (step 4, L429).
    if raw_stabilize is not None:
        # raw_stabilize may be an opentimelineio.AnyDictionary (not a plain dict),
        # so isinstance(..., dict) would return False. Use hasattr to detect mapping
        # behaviour instead (OTIO AnyDictionary supports .get()).
        raw_trf_path = (
            raw_stabilize.get("trf_path") if hasattr(raw_stabilize, "get") else None
        )
        if raw_trf_path is not None:
            # (a) Boundary check: trf_path must be under the timeline directory.
            # Intentional: trf file is always co-located with the timeline
            # (generated by detect_shake). ADR-PP-1 absolute escape hatch is
            # not applicable here.
            _check_within_timeline_dir(
                timeline_path,
                raw_trf_path,
                kind="stabilize trf file",
                hint_detail="stabilize trf file",
            )
            # (b) Existence check: file must already exist (generated by detect_shake).
            # Message exposes basename only (CWE-209).
            if not Path(raw_trf_path).exists():
                raise ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message=f"Stabilize .trf file not found: {Path(raw_trf_path).name}",
                    hint=(
                        "Run clipwright_detect_shake first to generate the .trf file"
                        " next to the timeline."
                    ),
                )

    # --- 5. Resolve hardware encoder (ADR-4) ---
    # Called before build_plan so that UNSUPPORTED_OPERATION is raised early and
    # ffmpeg is never invoked on encoder resolution failure (AC-4).
    # The call is inside _render_inner which is wrapped by render_timeline's
    # try/except ClipwrightError, so the error is converted to ok=False envelope.
    resolved: ResolvedEncoder | None = _resolve_hw_encoder(options)

    # --- 4f. Karaoke ASS generation (F-R-04 / ADR-K4) ---
    # When subtitle.karaoke=True: parse the word-level VTT, build a self-contained
    # ASS document in a TemporaryDirectory, and swap subtitle.path so that
    # _append_subtitle_filter detects is_ass=True (suppresses charenc and
    # force_style per DC-AS-002 / ADR-K4).
    #
    # SR-L-2: ExitStack provides RAII cleanup of the TemporaryDirectory.
    # The tmpdir remains alive through the entire with-block scope, covering
    # both build_plan (filter_complex generation) and any subsequent ffmpeg
    # execution (actual .ass file read).  The ExitStack __exit__ always runs,
    # whether _render_inner returns normally or raises an exception.
    #
    # When karaoke=False (default), ExitStack exits immediately with no
    # cleanup to perform; existing subtitle paths flow unmodified to
    # build_plan (F-R-07 / AC-7 regression guarantee).
    with contextlib.ExitStack() as _karaoke_cleanup:
        if options.subtitle is not None and options.subtitle.karaoke:
            # Frame dimensions for PlayResX/Y in the ASS [Script Info] section.
            # Use the explicit render resolution when specified; fall back to the
            # probe dimensions from the first source; last resort 1920×1080 (should
            # not occur in practice: no-video sources raise UNSUPPORTED upstream).
            _kara_w: int = (
                options.width
                if options.width is not None
                else (probe_info.width or 1920)
            )
            _kara_h: int = (
                options.height
                if options.height is not None
                else (probe_info.height or 1080)
            )
            _tmpdir_name = _karaoke_cleanup.enter_context(tempfile.TemporaryDirectory())
            _ass_cues = _parse_word_vtt(
                options.subtitle.path, max_words=MAX_WORDS, max_cues=MAX_CUES
            )
            _ass_content = _build_karaoke_ass(
                _ass_cues, options.subtitle, _kara_w, _kara_h
            )
            _ass_path = Path(_tmpdir_name) / "karaoke.ass"
            # SR-L-3: OS temp paths never contain single quotes in practice, but
            # guard before embedding the path in the filtergraph.
            # _escape_filtergraph handles backslash and colon; single-quote is
            # not escaped there, so a path containing "'" would break the
            # filename='...' ffmpeg filtergraph syntax (CR-E-001).
            if "'" in str(_ass_path):
                raise ClipwrightError(
                    code=ErrorCode.INTERNAL,
                    message="Internal error: temporary karaoke file path is unusable.",
                    hint="Report this error with reproduction steps.",
                )
            _ass_path.write_text(_ass_content, encoding="utf-8")
            # Replace subtitle.path with the generated .ass.  model_copy is used
            # (bypasses Pydantic validators; see SR-M-3 note in step 4c).
            # The karaoke field is preserved in the copied object but is not
            # re-evaluated downstream.
            options = options.model_copy(
                update={
                    "subtitle": options.subtitle.model_copy(
                        update={"path": str(_ass_path)}
                    )
                }
            )

        # --- 5b. build_plan ---
        # Pass source_probes to enable multi-source path (ADR-C2-r2 / ADR-C9-r2).
        # Pass bgm_clip for BGM audio chain integration (ADR-B5-r2; bgm=None for
        # backward compat).
        #
        # L-4 / S-I-2: text_overlays is not passed here; build_plan auto-collects
        # text_overlay markers from KeptRangeList._timeline when text_overlays=None
        # (the default).  ranges is a KeptRangeList with _timeline set by
        # resolve_kept_ranges, so marker lookup happens automatically inside build_plan.
        # Callers that pass a plain list[KeptRange] (e.g. in unit tests) will receive
        # no overlays (getattr returns None; _collect_text_overlays is not called).
        # IMPORTANT: if build_plan is ever called with a plain list[KeptRange] instead
        # of a KeptRangeList here, text_overlay markers will be silently ignored.
        # This is the designed fallback; however, any refactor that switches from
        # resolve_kept_ranges to manual KeptRange construction must pass text_overlays
        # explicitly to preserve overlay functionality.
        plan = build_plan(
            ranges,
            probe_info,
            options,
            denoise=raw_denoise,
            loudness=raw_loudness,
            color=raw_color,
            stabilize=raw_stabilize,  # §6-B
            source_probes=source_probes,
            bgm=bgm_clip,
            resolved_encoder=resolved,
            reframe=raw_reframe,  # §7.2
            transition=raw_transition,  # ADR-RT-2
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
            hw_warnings: list[str] = resolved.warnings if resolved is not None else []
            return ok_result(
                summary,
                data={
                    "ffmpeg_args": plan.ffmpeg_args,
                    "filter_complex": plan.filter_complex,
                    "segment_count": plan.segment_count,
                    "total_duration_seconds": plan.total_duration_seconds,
                    "estimated_size_bytes": plan.estimated_size_bytes,
                },
                warnings=plan.warnings + subtitle_warnings + hw_warnings,
            )

        # --- 6b. Execute ---
        ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
        timeout = max(300, math.ceil(plan.total_duration_seconds * 10))

        # Overwrite flag (-y / -n)
        overwrite_flag = ["-y"] if options.overwrite else ["-n"]

        # Determine -hwaccel value for decode acceleration (ADR-6 / AC-7).
        # Only emitted when hwaccel_decode=True; BGM input is excluded (ADR-6).
        # Vendor mapping:
        #   explicit vendor → hwaccel_value(vendor) (amf yields None → skip)
        #   auto            → resolved.hwaccel_value or "auto" (libx264 fallback)
        #   none            → "auto" (parent confirmed Q1)
        _hw_decode_value: str | None = None
        if options.hwaccel_decode:
            _vendor = options.hw_encoder
            if _vendor in ("nvenc", "amf", "qsv", "vaapi", "videotoolbox"):
                _hw_decode_value = hwaccel_value(_vendor)
            elif _vendor == "auto":
                _hw_decode_value = (
                    resolved.hwaccel_value if resolved is not None else None
                ) or "auto"
            else:
                # hw_encoder == "none"
                _hw_decode_value = "auto"

        # -i list taken directly from plan.input_sources (ADR-C9-r2).
        # Order is not recalculated in render.py (eliminates duplicate logic).
        # When _hw_decode_value is set, prepend -hwaccel <value> before each -i (ADR-6).
        inputs: list[str] = []
        for src in plan.input_sources:
            if _hw_decode_value is not None:
                inputs += ["-hwaccel", _hw_decode_value]
            inputs += ["-i", src]

        # When BGM is present, prepend -stream_loop -1 and append BGM as the last
        # -i (ADR-B6-r2/DC-AS-005). -stream_loop is an input option; it must
        # immediately precede -i. The invariant BGM index == len(plan.input_sources)
        # is maintained (DC-AS-005).
        if plan.bgm_source is not None:
            inputs += ["-stream_loop", "-1", "-i", plan.bgm_source]

        # Image overlay inputs: appended AFTER bgm (ADR-OV-5/G4).
        # -loop 1 -t {total_duration} is prepended so the single-frame PNG's internal
        # timestamps advance with the output clock; without this, fade=t=in:st=N fires
        # at timestamp 0 only and the alpha channel is 0 for the entire clip (V2-1 fix).
        # NO -hwaccel on image inputs.
        # Boundary, extension, and existence checks are applied here (defence-in-depth;
        # OTIO is untrusted; V2-7/DC-AM-004).
        _img_dur: str | None = (
            f"{plan.total_duration_seconds:g}" if plan.image_sources else None
        )
        for img in plan.image_sources:
            # Early path collision check (ADR-B8 parity / ADR-SR-1): output == image
            # is rejected before existence/extension checks (non-destructive; the
            # extensions are disjoint so this only trips the pathological
            # image_path == output input).
            check_output_not_source(output_path, [img])
            _img_p = Path(img.replace("\\", "/"))
            _img_exists_p = (
                (timeline_path.parent / _img_p) if not _img_p.is_absolute() else _img_p
            )
            # Existence check (FILE_NOT_FOUND; basename only; CWE-209)
            if not _img_exists_p.exists():
                raise ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message=f"Image overlay file not found: {_img_p.name}",
                    hint=(
                        "Place the image overlay file referenced in the OTIO timeline"
                        " in the expected location."
                    ),
                )
            # Extension allowlist (INVALID_INPUT; after existence check per ADR-OV-3)
            _img_ext = _img_p.suffix.lower()
            if _img_ext not in _ALLOWED_IMAGE_EXTENSIONS:
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        f"Image overlay file extension is not allowed: {_img_ext!r}."
                    ),
                    hint=(
                        "Use an image overlay file with extension .png, .jpg, .jpeg,"
                        " or .webp."
                    ),
                )
            # Unified boundary/symlink check (ADR-PP-1): external absolute paths allowed
            check_media_ref(img, timeline_path.parent, "image")
            _verify_image_magic(img)  # fast corrupt-check before -loop 1 can hang
            if _img_dur is not None:
                inputs += ["-loop", "1", "-t", _img_dur]
            inputs += ["-i", img]

        # PiP video overlays: appended AFTER image_sources (ADR-PIP-7).
        # Plain -i <pip_source>, NO -loop 1 (these are real video files with their own
        # timestamps, unlike the still-image overlays above; trim+setpts in the
        # filtergraph re-bases them instead).
        # Boundary, extension, and existence checks are applied here (defence-in-depth;
        # OTIO is untrusted; second-layer validation mirrors image_sources).
        for pip in plan.pip_sources:
            _pip_p = Path(pip.replace("\\", "/"))
            _pip_exists_p = (
                (timeline_path.parent / _pip_p) if not _pip_p.is_absolute() else _pip_p
            )
            # Existence check (FILE_NOT_FOUND; basename only; CWE-209)
            if not _pip_exists_p.exists():
                raise ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message=f"PiP video file not found: {_pip_p.name}",
                    hint=(
                        "Place the PiP video file referenced in the OTIO timeline"
                        " in the expected location."
                    ),
                )
            # Extension allowlist (INVALID_INPUT; after existence check per ADR-OV-3)
            _pip_ext = _pip_p.suffix.lower()
            if _pip_ext not in _ALLOWED_PIP_VIDEO_EXTENSIONS:
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(f"PiP video file extension is not allowed: {_pip_ext!r}."),
                    hint=(
                        "Use a PiP video file with extension .mp4, .mkv, .mov,"
                        " or .webm."
                    ),
                )
            # Unified boundary/symlink check (ADR-PP-1): external absolute paths allowed
            check_media_ref(pip, timeline_path.parent, "pip")
            # Re-probe video stream (TOCTOU: check_media_ref covers symlink/path
            # validation; re-probe ensures content is still valid at render time,
            # unlike _verify_image_magic which only checks magic bytes).
            # Redact subprocess errors (ADR-SR-1 / S4).
            try:
                inspect_media(pip)
            except ClipwrightError as exc:
                raise _sanitize_subprocess_error(exc) from None
            # Check that output is not equal to pip source
            check_output_not_source(output_path, [pip])
            inputs += ["-i", pip]

        # F-4: When stabilize is enabled, output must be absolutised so that
        # changing cwd to the trf parent directory does not redirect the output
        # file into the wrong directory. -i (input_sources) is already absolute;
        # subtitles/drawtext are absolutised by _escape_filtergraph; the only
        # remaining relative path risk is the output argument. §6-C.
        output_arg = (
            str(Path(output).resolve())
            if plan.stabilize_cwd is not None
            else str(output)
        )
        # vid.stab #144: decoder frame-threading corrupts B-frame refs; serialize.
        global_decode_args = ["-threads", "1"] if plan.stabilize_cwd is not None else []
        cmd = (
            [ffmpeg]
            + overwrite_flag
            + global_decode_args
            + inputs
            + plan.ffmpeg_args
            + [output_arg]
        )

        try:
            run(cmd, timeout=float(timeout), cwd=plan.stabilize_cwd)  # §6-C
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from None  # S1 (ADR-SR-1)

        # Verify output file exists
        if not output_path.exists():
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=(
                    "ffmpeg exited successfully but the output file was not generated."
                ),
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
            warnings=plan.warnings
            + subtitle_warnings
            + (resolved.warnings if resolved is not None else []),
        )
