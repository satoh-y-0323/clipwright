"""sequence.py — orchestration layer for clipwright-sequence.

Assembles an ordered list of SequenceClip specs into a single multi-source
OTIO timeline (V1 video track only).

Design decisions:
- build_sequence is the sole ClipwrightError -> error_result boundary (ADR-SEQ-4).
  No error conversion in server.py; no I/O in plan.py.
- Fast-fail order before spawning ffprobe: empty clips, clips>1000, .otio
  extension, parent dir existence (§V2.12 / trim.py L66-99 pattern).
- Unique sources are deduplicated by resolved absolute path in first-occurrence
  order (§V2.6 DC-AM-002). probe is called exactly once per unique source.
- Per-source validation order: probe -> duration None -> rate sentinel ->
  has_video -> co-location -> output==source (§V2.2 DC-AS-002).
- _resolve_and_check_colocation resolves the source path exactly once and
  returns the resolved absolute path string, which is reused as SourceProbe.abs_path
  and OTIO target_url (§V2.1 DC-AS-001; no double resolve).
- All ClipwrightError from inspect_media (including DEPENDENCY_MISSING /
  SUBPROCESS_*) propagate transparently through the 2-layer boundary
  (§V2.10 DC-AM-004/005).
- Error messages do not expose full paths (CWE-209).
"""

from __future__ import annotations

from pathlib import Path

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel, ToolResult

import clipwright_sequence
from clipwright_sequence.plan import SourceProbe, resolve_clip_specs
from clipwright_sequence.schemas import SequenceClip

# Sentinel frame rate produced by media.py when avg_frame_rate is 0/0 or N/A.
# Keep in sync with clipwright.media (1000.0).
_SENTINEL_RATE = 1000.0

# Maximum number of clips accepted per call (§V2.12 DC-GP-003).
_MAX_CLIPS = 1000


def build_sequence(clips: list[SequenceClip], output: str) -> ToolResult:
    """Public entry point. Sole ClipwrightError -> error_result boundary.

    Assembles the ordered clips list into a single OTIO timeline written to
    the output path.  Non-destructive: input media files are never modified.

    Args:
        clips: Ordered list of SequenceClip specs to assemble.
        output: Output OTIO file path (.otio extension required).

    Returns:
        ok_result on success; error_result on any failure.
        total_duration_sec in data is the sum of input clip ranges (an estimate);
        the rendered output duration may differ by a few frames after normalization.
    """
    try:
        return _build_sequence_inner(clips, output)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _build_sequence_inner(clips: list[SequenceClip], output: str) -> ToolResult:
    """Internal implementation. Raises ClipwrightError directly on any failure.

    Flow:
      1. Fast-fail checks (before ffprobe): empty clips, clips>1000, .otio
         extension, output parent dir existence.
      2. Per unique source (first-occurrence order, resolved-path dedup):
         inspect_media -> duration None -> rate sentinel -> has_video ->
         co-location -> output==source.
      3. resolve_clip_specs (pure range arithmetic / defaulting / tolerance).
      4. OTIO build: new_timeline -> V1 clips with add_clip.
      5. save_timeline (atomic write).
      6. Return ok_result envelope.
    """
    output_path = Path(output)

    # ------------------------------------------------------------------
    # 1. Fast-fail checks (before spawning ffprobe)
    # ------------------------------------------------------------------

    # Empty clips list
    if not clips:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="No clips were provided.",
            hint="Provide at least one SequenceClip in the clips list.",
        )

    # Clips length upper bound (§V2.12 DC-GP-003)
    if len(clips) > _MAX_CLIPS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Too many clips: {len(clips)} (maximum is {_MAX_CLIPS}).",
            hint=(
                f"Reduce the number of clips to at most {_MAX_CLIPS}, "
                "or split into multiple sequences."
            ),
        )

    # Output extension must be .otio
    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Invalid output file extension. Only .otio is allowed.",
            hint="Change the output file path extension to .otio.",
        )

    # Output parent directory must exist
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    # ------------------------------------------------------------------
    # 2. Per-unique-source validation and probe (first-occurrence order)
    # ------------------------------------------------------------------

    # Determine unique sources by resolved absolute path in first-occurrence order
    # (§V2.6 DC-AM-002). All clip.media strings are pre-resolved to a temporary
    # key for dedup; the canonical abs_path used as target_url comes from
    # _resolve_and_check_colocation in the probe loop (DC-AS-001: single resolve).
    #
    # pre_resolved_map: maps every clip.media string -> its temporary resolved key.
    # This is used both for dedup and for mapping back after probing.
    pre_resolved_map: dict[str, str] = {}  # clip.media -> resolved/absolute key
    seen_keys: set[str] = set()
    ordered_unique_media: list[
        str
    ] = []  # first-occurrence clip.media for each unique source

    for clip in clips:
        if clip.media in pre_resolved_map:
            continue  # already processed this exact string
        try:
            key = str(Path(clip.media).resolve())
        except OSError:
            key = str(Path(clip.media).absolute())
        pre_resolved_map[clip.media] = key
        if key not in seen_keys:
            seen_keys.add(key)
            ordered_unique_media.append(clip.media)

    # canonical_map: pre-resolved key -> canonical abs_path (set during probe loop).
    # All clip.media spellings sharing the same pre-resolved key share one entry.
    canonical_map: dict[str, str] = {}  # pre-resolved key -> canonical abs_path

    probes: dict[str, SourceProbe] = {}  # keyed by canonical abs_path

    for media in ordered_unique_media:
        # (1) inspect_media (existence check, FILE_NOT_FOUND, DEPENDENCY_MISSING, etc.)
        #     All ClipwrightError propagate transparently (§V2.10).
        info = inspect_media(media)

        # (2) duration None -> PROBE_FAILED
        if info.duration is None:
            raise ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message=f"Could not retrieve media duration: {Path(media).name}",
                hint=(
                    "Check that the media file is not corrupted. "
                    "You can also verify manually with ffprobe."
                ),
            )

        duration_sec = info.duration.value / info.duration.rate
        rate = info.duration.rate

        # (3) Rate sentinel: video stream reported avg_frame_rate=0/0 or N/A
        #     (§V2.4 DC-AS-004). Sentinel rate means fps is undetermined.
        if rate >= _SENTINEL_RATE:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"Could not determine a valid frame rate for source: "
                    f"{Path(media).name}"
                ),
                hint=(
                    "This source reports no usable frame rate (e.g. a still image "
                    "stream or an unusual capture). Provide a video file with a "
                    "normal frame rate."
                ),
            )

        # (4) has_video check
        has_video = any(s.codec_type == "video" for s in info.streams)
        if not has_video:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=f"Source has no video stream: {Path(media).name}",
                hint=(
                    "Provide a source file that contains a video stream. "
                    "Audio-only files are not supported."
                ),
            )

        # (5) co-location check: resolve once, reuse as abs_path and target_url
        #     (§V2.1 DC-AS-001 / §V2.2 step 5 / ADR-SEQ-6).
        abs_path = _resolve_and_check_colocation(media, output_path)

        # (6) output == source -> PATH_NOT_ALLOWED (§V2.8 DC-AM-001)
        _check_output_not_source(output_path, abs_path)

        probes[abs_path] = SourceProbe(
            abs_path=abs_path,
            duration_sec=duration_sec,
            rate=rate,
            has_video=has_video,
        )

        # Record the canonical abs_path for every clip.media string that shares
        # the same pre-resolved key (handles "./a.mp4" vs "a.mp4" dedup).
        media_key = pre_resolved_map[media]
        canonical_map[media_key] = abs_path

    # Build resolved-key clips for plan.resolve_clip_specs (§V2.6):
    # replace each clip.media with its canonical abs_path so that plan.py
    # can look up probes[clip.media] correctly.
    resolved_key_clips = [
        SequenceClip(
            media=canonical_map[pre_resolved_map[clip.media]],
            start_sec=clip.start_sec,
            end_sec=clip.end_sec,
        )
        for clip in clips
    ]

    # ------------------------------------------------------------------
    # 3. Resolve clip specs (pure arithmetic; raises INVALID_INPUT on range errors)
    # ------------------------------------------------------------------

    resolved_clips, warnings = resolve_clip_specs(probes, resolved_key_clips)

    # ------------------------------------------------------------------
    # 4. Build OTIO timeline (ADR-SEQ-5)
    # ------------------------------------------------------------------

    timeline = new_timeline(output_path.stem)
    v1 = timeline.tracks[0]  # V1 (Video) track; index 0 per new_timeline

    for rc in resolved_clips:
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=rc.start_sec * rc.rate, rate=rc.rate),
            duration=RationalTimeModel(
                value=(rc.end_sec - rc.start_sec) * rc.rate, rate=rc.rate
            ),
        )
        add_clip(
            v1,
            MediaRef(target_url=rc.source),
            source_range,
            name="sequence_clip",
            metadata={
                "tool": "clipwright_build_sequence",
                "version": clipwright_sequence.__version__,
                "kind": "sequence_clip",
                "index": rc.index,
            },
        )

    # ------------------------------------------------------------------
    # 5. Save timeline (atomic write)
    # ------------------------------------------------------------------

    save_timeline(timeline, output)

    # ------------------------------------------------------------------
    # 6. Build and return ok_result envelope (ADR-SEQ-7)
    # ------------------------------------------------------------------

    clip_count = len(resolved_clips)
    total_duration_sec = sum(rc.end_sec - rc.start_sec for rc in resolved_clips)
    unique_source_count = len(probes)

    summary = (
        f"Assembled a {clip_count}-clip sequence "
        f"(approx total {total_duration_sec:.1f}s) "
        f"from {unique_source_count} source(s). "
        f"Generated {output_path.name}. "
        f"Pass it to clipwright-render to concatenate into a single video."
    )

    return ok_result(
        summary,
        data={
            "clip_count": clip_count,
            "total_duration_sec": total_duration_sec,
            "unique_source_count": unique_source_count,
        },
        artifacts=[{"role": "timeline", "path": str(output), "format": "otio"}],
        warnings=warnings,
    )


def _resolve_and_check_colocation(media: str, output_path: Path) -> str:
    """Resolve the source path once and verify co-location against output directory.

    Resolves the source path exactly once and returns the resolved absolute path
    string.  The return value is reused as SourceProbe.abs_path and the OTIO
    target_url (DC-AS-001: no double resolve).

    Mirrors render._check_within_timeline_dir (keep in sync).
    Allows recursive subdirectories; raises PATH_NOT_ALLOWED only when the
    source points outside the output parent directory tree (ADR-SEQ-6).
    Falls back to absolute()-based comparison when resolve() raises OSError
    (§V2.11 DC-GP-002 / SR L-1).

    Error message uses fixed wording without exposing the full path (CWE-209).

    Args:
        media: Original source media path string.
        output_path: Output OTIO file path (its parent is the project boundary).

    Returns:
        Resolved (or absolute, as fallback) absolute path string.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when source is outside the project tree.
    """
    try:
        base = output_path.parent.resolve()
        target = Path(media).resolve()
        base_str = str(base)
        target_str = str(target)
        if not (
            target_str == base_str
            or target_str.startswith(base_str + "/")
            or target_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Source file points outside the project boundary.",
                hint=(
                    "Use a source file located under the same directory"
                    " as the OTIO timeline."
                ),
            )
        return target_str
    except ClipwrightError:
        raise
    except OSError:
        # resolve() failure (network paths, extremely long paths, symlink loops):
        # fall back to absolute()-based best-effort comparison (§V2.11 DC-GP-002).
        try:
            base_abs = str(output_path.parent.absolute())
            target_abs = str(Path(media).absolute())
            if not (
                target_abs == base_abs
                or target_abs.startswith(base_abs + "/")
                or target_abs.startswith(base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Source file points outside the project boundary.",
                    hint=(
                        "Use a source file located under the same directory"
                        " as the OTIO timeline."
                    ),
                )
            return target_abs
        except ClipwrightError:
            raise
        except OSError:
            # Truly unresolvable: accept and defer to subsequent existence checks.
            return str(Path(media).absolute())


def _check_output_not_source(output_path: Path, abs_source: str) -> None:
    """Verify that output and source do not resolve to the same path (§V2.8 DC-AM-001).

    abs_source is already a resolved (or absolute-fallback) path from
    _resolve_and_check_colocation, so only the output needs resolving.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when paths are equal.
    """
    try:
        out_resolved = str(output_path.resolve())
    except OSError:
        try:
            out_resolved = str(output_path.absolute())
        except OSError:
            out_resolved = str(output_path)

    if out_resolved == abs_source:
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message="Output path and input source path are the same.",
            hint=(
                "Change the output file path to be different from the"
                " input source file."
            ),
        )
