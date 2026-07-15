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
  has_video (§V2.2 DC-AS-002). No co-location check (ADR-SEQ-6 relaxed).
- Sources may reside anywhere readable; no project-boundary restriction on sources.
  output == any source is still rejected (§V2.8 DC-AM-001).
- target_url in OTIO: relative POSIX path when source is under the otio_dir tree;
  absolute path for external sources (pathpolicy.media_ref_for_otio).
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
from clipwright.nle_interop import conform_timeline_for_nle
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.pathpolicy import check_output_not_source, media_ref_for_otio
from clipwright.schemas import (
    MediaInfo,
    MediaRef,
    RationalTimeModel,
    TimeRangeModel,
    ToolResult,
)

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

    Source files may reside in any readable location (no project-boundary
    restriction on sources; ADR-SEQ-6 relaxed).  Output must not equal any
    source path.

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
    except Exception:
        # SR-R-001 / CWE-209: catch unexpected exceptions with fixed wording to
        # prevent internal path exposure.
        return error_result(
            ErrorCode.INTERNAL,
            "Building the sequence failed due to an internal error.",
            "Retry after verifying that the input and output paths are accessible.",
        )


def _build_sequence_inner(clips: list[SequenceClip], output: str) -> ToolResult:
    """Internal implementation. Raises ClipwrightError directly on any failure.

    Flow:
      1. Fast-fail checks (before ffprobe): empty clips, clips>1000, .otio
         extension, output parent dir existence.
      2. Per unique source (first-occurrence order, resolved-path dedup):
         inspect_media -> duration None -> rate sentinel -> has_video.
      3. Output == any source check (pathpolicy.check_output_not_source).
      4. resolve_clip_specs (pure range arithmetic / defaulting / tolerance).
      5. OTIO build: new_timeline -> V1 clips with add_clip.
         target_url = media_ref_for_otio(source, output_dir): relative POSIX
         for internal sources, absolute for external sources.
      5b. conform_timeline_for_nle (ADR-NI-8): stamp Resolve wire format from
         the target_url -> MediaInfo map built during the add_clip loop.
      6. save_timeline (atomic write).
      7. Return ok_result envelope.
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
            message="Too many clips were provided.",
            hint=(
                f"Received {len(clips)} clips; reduce to at most {_MAX_CLIPS} clips, "
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
    # key for dedup; the canonical abs_path used as target_url comes from the
    # three-stage fallback (resolve -> absolute -> str) in the probe loop.
    #
    # TOCTOU note: this pre-resolve and the second resolution in the probe loop
    # create a two-resolve window per source path.  The window is closed by
    # inspect_media (step 1 in the probe loop) which runs between the two
    # resolutions: inspect_media -> _validate_existing_file rejects symlinks and
    # non-existent paths with FILE_NOT_FOUND, so any path manipulation between
    # the two resolutions is caught before the canonical abs_path is used
    # as target_url (SR L-1 / DC-AS-005).
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
        #     FILE_NOT_FOUND is caught here to replace the core message (which may
        #     contain a full path from _validate_existing_file) with a basename-only
        #     fixed message (CWE-209 / DC-AM-004/005). All other ClipwrightError codes
        #     (DEPENDENCY_MISSING / SUBPROCESS_*) propagate transparently (§V2.10).
        try:
            info = inspect_media(media)
        except ClipwrightError as exc:
            if exc.code == ErrorCode.FILE_NOT_FOUND:
                raise ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message=f"File not found: {Path(media).name}",
                    hint="Check that the path is correct and the file exists.",
                ) from None
            raise  # DEPENDENCY_MISSING / SUBPROCESS_* stay transparent

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

        duration_value = info.duration.value
        rate = info.duration.rate
        duration_sec = duration_value / rate

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

        # (5) Resolve source path to canonical abs_path (three-stage fallback).
        #     No co-location check: sources may reside outside the output directory
        #     (ADR-SEQ-6 relaxed).  The abs_path is reused as the probes dict key
        #     and the OTIO target_url input (DC-AS-001: single resolve).
        try:
            abs_path = str(Path(media).resolve())
        except OSError:
            try:
                abs_path = str(Path(media).absolute())
            except OSError:
                abs_path = str(Path(media))

        probes[abs_path] = SourceProbe(
            abs_path=abs_path,
            duration_sec=duration_sec,
            duration_value=duration_value,
            rate=rate,
            has_video=has_video,
            media_info=info,
        )

        # Record the canonical abs_path for every clip.media string that shares
        # the same pre-resolved key (handles "./a.mp4" vs "a.mp4" dedup).
        media_key = pre_resolved_map[media]
        canonical_map[media_key] = abs_path

    # ------------------------------------------------------------------
    # 3. Output == any source check (§V2.8 DC-AM-001)
    # ------------------------------------------------------------------

    # check_output_not_source compares the canonicalised output path against each
    # source abs_path.  Raises PATH_NOT_ALLOWED when any match is found.
    check_output_not_source(output_path, probes.keys())

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
    # 4. Resolve clip specs (pure arithmetic; raises INVALID_INPUT on range errors)
    # ------------------------------------------------------------------

    resolved_clips, warnings = resolve_clip_specs(probes, resolved_key_clips)

    # ------------------------------------------------------------------
    # 5. Build OTIO timeline (ADR-SEQ-5)
    # ------------------------------------------------------------------

    timeline = new_timeline(output_path.stem)
    v1 = timeline.tracks[0]  # V1 (Video) track; index 0 per new_timeline
    otio_dir = output_path.parent

    # target_url -> MediaInfo map for conform_timeline_for_nle (ADR-NI-8/9).
    # The key is the exact string returned by media_ref_for_otio below and
    # written onto each Clip's ExternalReference, so conform's literal
    # target_url lookup cannot silently miss (ADR-NI-9: no re-computation with
    # potentially divergent arguments). Sources whose media_info is absent
    # (e.g. plan-only paths) are simply omitted and skipped by conform.
    media_info_map: dict[str, MediaInfo] = {}

    for rc in resolved_clips:
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=rc.start_sec * rc.rate, rate=rc.rate),
            duration=RationalTimeModel(
                value=(rc.end_sec - rc.start_sec) * rc.rate, rate=rc.rate
            ),
        )
        # available_range: FULL duration of this clip's own source (ADR-3/ADR-4),
        # looked up per-clip via rc.source -> probes to avoid cross-source
        # mix-ups when multiple distinct sources are interleaved.
        # Use available_duration_value (the probe's original RationalTime value)
        # rather than `duration_sec * rate`, which would round-trip through a
        # division (duration_sec = duration_value / rate) and could reintroduce
        # floating-point error at the source_range <= available_range boundary
        # (CR-NEW low, precision).
        source_probe = probes[rc.source]
        available_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=source_probe.rate),
            duration=RationalTimeModel(
                value=source_probe.available_duration_value,
                rate=source_probe.rate,
            ),
        )
        # target_url: relative POSIX path for internal sources (under otio_dir),
        # absolute path for external sources (media_ref_for_otio, ADR-SEQ-6).
        target_url = media_ref_for_otio(rc.source, otio_dir)
        if source_probe.media_info is not None:
            media_info_map[target_url] = source_probe.media_info
        add_clip(
            v1,
            MediaRef(target_url=target_url, available_range=available_range),
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
    # 5b. Conform for NLE interop (ADR-NI-8): stamp Resolve wire format —
    #     start-timecode shift, global_start_time, and N audio-track mirror —
    #     right before save. Never raises; its warnings are relayed to the
    #     envelope so an AI can see any degenerate case (unresolved timecode,
    #     unsupported channel count, etc.).
    # ------------------------------------------------------------------

    warnings = warnings + conform_timeline_for_nle(timeline, media_info_map)

    # ------------------------------------------------------------------
    # 6. Save timeline (atomic write)
    # ------------------------------------------------------------------

    save_timeline(timeline, output)

    # ------------------------------------------------------------------
    # 7. Build and return ok_result envelope (ADR-SEQ-7)
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
