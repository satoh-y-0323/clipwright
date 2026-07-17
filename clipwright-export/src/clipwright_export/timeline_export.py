"""timeline_export.py — clipwright-export timeline-to-NLE orchestration layer.

Exports an OTIO timeline to an NLE-interchange format (CMX3600 EDL or Final
Cut Pro XML) via the OpenTimelineIO adapters, absolutizing media references
for hand-off and reporting clipwright-specific edit data that the exchange
formats cannot carry.

Design decisions (architecture-report §2/§4/§5/§6/§11/§13):
- Two-layer pattern (clipwright-speed §1 reuse): _export_timeline_inner() is
  the raising implementation; export_timeline() is the boundary that converts
  ClipwrightError -> error_result and any other exception -> INTERNAL
  (CWE-209: no input/output path leaks in the generic message).
- ADR-EX-10 (§13.1): non-integer (NTSC 23.976/29.97) frame rates are rejected
  BEFORE any write with INVALID_INPUT; no file is ever produced. The installed
  adapters silently emit files no NLE can import for these rates, so fail-fast
  is safer than best-effort.
- ADR-EX-11 (§13.2): _write_adapter performs a write-then-verify — it re-reads
  the just-written file with the same adapter and, on any re-read exception,
  deletes the artifact and returns OTIO_ERROR. The judgement is exception
  presence only (no value comparison), so normal integer-rate frame
  quantisation never false-fails.
- ADR-EX-12 (§quantization): in the EDL path only, after audio-track removal
  (ADR-NI-7) and immediately before _write_adapter, the write-time deep copy is
  quantized to whole-frame boundaries at the representative integer rate by
  _quantize_to_frame_boundaries. Cumulative record boundaries are snapped
  half-up (floor(x+0.5), never banker's round) and each item's record duration
  is the rounded boundary difference; source start is rounded independently and
  source duration is set equal to the record duration, so cmx_3600's
  src_duration == rec_duration check holds by construction. Adjustments are
  reported in one aggregated warning. FCPXML is passed through without a
  quantization pass: the fcpx_xml adapter performs its own frame conversion and
  never fails the write-then-verify round-trip the way cmx_3600 does. FCPXML
  export can silently floor fractional-frame durations per clip with unbounded
  cumulative drift and no warning (pre-existing adapter limitation, tracked
  separately).
- ADR-EX-4 (§4.2): media references that do not exist are skipped (kept
  relative) with a warning; only a relative reference that escapes the OTIO
  directory (CWE-22) fails the whole export.
- ADR-EX-6 (§6): transition directives are NOT converted to otio.schema
  Transition; the loss is surfaced via _loss_report warnings.
- The input OTIO file is never modified (AC-3): all mutation happens on a deep
  copy of the loaded timeline.

Validation order (adapted from clipwright-speed):
  1. output != timeline (check_output_not_source -> PATH_NOT_ALLOWED).
     NB: this precedes the suffix check on purpose. When output == timeline the
     paths share the .otio extension which never matches an export suffix, so
     running the suffix check first would return INVALID_INPUT instead of the
     PATH_NOT_ALLOWED the shared collision guard is meant to produce.
  2. output suffix matches the requested format (INVALID_INPUT; the offending
     suffix is never echoed back, SR L-1 / CWE-209).
  3. output parent directory exists (FILE_NOT_FOUND).
  4. source exists + load timeline (FILE_NOT_FOUND / OTIO_ERROR).
  5. deep copy + reject non-integer frame rate (ADR-EX-10).
  6. loss report + media absolutization (may raise PATH_NOT_ALLOWED).
  7. write adapter + write-then-verify (ADR-EX-11).
"""

from __future__ import annotations

import collections.abc
import contextlib
import math
from collections.abc import Iterator
from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import (
    get_clipwright_metadata,
    get_markers,
    load_timeline,
)
from clipwright.pathpolicy import (
    check_media_ref,
    check_output_not_source,
    validate_source_or_basename,
)
from clipwright.schemas import ToolResult

from clipwright_export.schemas import ExportTimelineOptions

# Adapter names as registered by otio.adapters (spike-report §(1)).
_ADAPTER_NAMES: dict[str, str] = {"edl": "cmx_3600", "fcpxml": "fcpx_xml"}

# Output extension allow-list per format (architecture §3.3).
_EXPECTED_SUFFIX: dict[str, str] = {"edl": ".edl", "fcpxml": ".fcpxml"}

# CR-E-004: _write_adapter's OTIOError hint, chosen per-format so a fcpxml
# write failure never tells the caller to "export to FCPXML instead" (the
# caller already requested fcpxml).
_WRITE_FAILURE_HINTS: dict[str, str] = {
    "edl": (
        "The EDL format supports only a single video track; review the "
        "track layout, or export to FCPXML instead."
    ),
    "fcpxml": (
        "Review the timeline for a track layout or structure the FCPXML "
        "adapter cannot represent, or use clipwright-render to produce a "
        "flat MP4 instead."
    ),
}

# Loss-report label tables (architecture §5.1). Marker kinds map to a display
# label; scene_boundary is excluded because both adapters transcribe marker
# position (spike-report §7b). Directives live on timeline metadata, not the
# clip (see conftest note / the real satellite tools).
_MARKER_KIND_LABELS: dict[str, str] = {
    "caption": "captions",
    "word_caption": "captions",
    "text_overlay": "text overlays",
    "image_overlay": "image overlays",
    "pip_overlay": "picture-in-picture overlays",
    "bgm": "background music tracks",
}
_EXCLUDED_MARKER_KINDS: frozenset[str] = frozenset({"scene_boundary"})
_DIRECTIVE_LABELS: dict[str, str] = {
    "color": "color grades",
    "denoise": "noise reductions",
    "loudness": "loudness adjustments",
    "stabilize": "stabilizations",
}
# Stable emission order for the aggregated loss sentence (§5.2).
_LABEL_ORDER: tuple[str, ...] = (
    "captions",
    "text overlays",
    "image overlays",
    "picture-in-picture overlays",
    "background music tracks",
    "color grades",
    "noise reductions",
    "loudness adjustments",
    "stabilizations",
    "speed changes",
    "transitions",
)

# Non-integer rate detection threshold (ADR-EX-10 §13.1).
_RATE_EPS = 1e-6

# Frame-boundary rounding tolerance (ADR-EX-12). Distinct from _RATE_EPS: this
# absorbs float representation noise when snapping cumulative record boundaries
# to whole frames in _quantize_to_frame_boundaries (a different concern than
# non-integer rate detection, so it is kept as an independent constant).
_FRAME_EPS = 1e-6

# SR-V-001 (CWE-400): unknown marker-kind strings embedded in the aggregated
# loss-report warning are truncated to this many characters.
_UNKNOWN_KIND_MAX_LEN = 64
_TRUNCATION_SUFFIX = "..."


# ---------------------------------------------------------------------------
# Local path helpers (core pathpolicy is not modified; these mirror its
# normalization/symlink logic for the absolute-reference branch that
# check_media_ref cannot serve, because absolute-missing must skip, not fail).
# ---------------------------------------------------------------------------


def _normalize_ref(ref: str) -> Path:
    """Return a Path from *ref*, normalising backslashes to forward slashes."""
    return Path(ref.replace("\\", "/"))


def _has_symlink_component(path: Path) -> bool:
    """Return True if any component of *path* is a symlink (leaf to root)."""
    current = path
    while True:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _iter_clips(
    tl: otio.schema.Timeline, *, video_only: bool = False
) -> Iterator[otio.schema.Clip]:
    """Yield every Clip on every track, in track/item order.

    When *video_only* is True, only clips on Video-kind tracks are yielded
    (CR-NEW: used by _representative_rate so an Audio track enumerated
    before the Video track in tl.tracks does not skew the representative
    rate).
    """
    for track in tl.tracks:
        if video_only and track.kind != otio.schema.TrackKind.Video:
            continue
        for item in track:
            if isinstance(item, otio.schema.Clip):
                yield item


def _video_clip_count(tl: otio.schema.Timeline) -> int:
    """Count Clip objects on Video-kind tracks."""
    return sum(
        1
        for track in tl.tracks
        if track.kind == otio.schema.TrackKind.Video
        for item in track
        if isinstance(item, otio.schema.Clip)
    )


def _representative_rate(tl: otio.schema.Timeline) -> int:
    """Return the timeline's representative frame rate as an integer.

    Uses the first Video-track clip's source_range rate (CR-NEW: Stack order
    in tl.tracks is not kind-sorted, so an Audio track enumerated before the
    Video track must not skew the EDL rate warning / write-then-verify rate).
    Falls back to the first clip of any kind when no Video-track clip carries
    a source_range, preserving the original behaviour for audio-only or
    source_range-less timelines. Non-integer rates are already rejected by
    _check_frame_rates before this runs, so rounding is lossless. Falls back
    to 24 when no clip at all carries a source_range.
    """
    for clip in _iter_clips(tl, video_only=True):
        sr = clip.source_range
        if sr is not None:
            return int(round(sr.start_time.rate))
    for clip in _iter_clips(tl):
        sr = clip.source_range
        if sr is not None:
            return int(round(sr.start_time.rate))
    return 24


def _safe_unlink(output: str) -> None:
    """Best-effort delete of *output*; a failed delete must not mask the
    primary error signal (ADR-EX-11 §13.2)."""
    with contextlib.suppress(OSError):
        Path(output).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ADR-EX-10: non-integer frame rate rejection
# ---------------------------------------------------------------------------


def _check_frame_rates(tl: otio.schema.Timeline) -> None:
    """Reject a timeline that uses a non-integer (NTSC) frame rate.

    Inspects each clip's source_range and available_range rates. If any rate
    fails ``abs(rate - round(rate)) > 1e-6`` a ClipwrightError(INVALID_INPUT)
    is raised before any file is written (ADR-EX-10). The detected rate is
    included in the message (a rate value is not sensitive; paths are).
    """
    for clip in _iter_clips(tl):
        rates: list[float] = []
        sr = clip.source_range
        if sr is not None:
            rates.append(sr.start_time.rate)
            rates.append(sr.duration.rate)
        mr = clip.media_reference
        if isinstance(mr, otio.schema.ExternalReference):
            avail = mr.available_range
            if avail is not None:
                rates.append(avail.start_time.rate)
                rates.append(avail.duration.rate)
        for rate in rates:
            if abs(rate - round(rate)) > _RATE_EPS:
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        f"This timeline uses a non-integer frame rate ({rate:g}). "
                        "EDL and FCPXML timecode is frame-integer, and the "
                        "installed exchange adapters do not serialize NTSC rates "
                        "correctly (they would write a file that no NLE can import)."
                    ),
                    hint=(
                        "Render the program to a flat MP4 with clipwright-render "
                        "for handoff, or conform the sequence to an integer rate "
                        "(24/25/30) before exporting to EDL/FCPXML."
                    ),
                )


# ---------------------------------------------------------------------------
# ADR-EX-12: EDL frame-boundary quantization (write-time deep copy only)
# ---------------------------------------------------------------------------


def _round_half_up(x: float) -> int:
    """Half-up rounding (floor(x + 0.5)); NOT banker's round().

    _FRAME_EPS absorbs float representation error so a value that is
    conceptually an exact integer or exact .5 boundary snaps deterministically
    (e.g. 61.9999998 -> 62, 62.5 -> 63). Python's built-in round() uses
    banker's rounding and would send 62.5 -> 62, breaking the half-up
    consistency the boundary walk relies on.
    """
    return math.floor(x + 0.5 + _FRAME_EPS)


def _quantize_warnings(
    adjusted: int, zero_collapse: int, max_shift_frames: float, rate: int
) -> list[str]:
    """Build the aggregated ADR-EX-12 quantization warning (architecture §4).

    Returns [] when nothing was adjusted. Otherwise one aggregated sentence
    (with an inline lowercase ``hint:``) reporting the adjustment count and the
    largest adjustment in seconds; a second sentence is appended to the
    same string when clips collapsed to zero length. Only counts and seconds
    are exposed -- never paths or clip names (CWE-209).
    """
    if adjusted == 0:
        return []
    max_shift_s = max_shift_frames / rate
    sentence = (
        f"{adjusted} clip/gap boundary(ies) were quantized to whole frames for "
        f"the EDL at {rate} fps; the largest adjustment was "
        f"{max_shift_s:.4f}s (at most half a frame, no cumulative drift). "
        "hint: this is expected for second-based trims and silence cuts; "
        "re-cut on frame boundaries in the source OTIO if the shift matters."
    )
    if zero_collapse > 0:
        sentence += (
            f" {zero_collapse} clip(s) shorter than half a frame collapsed to "
            "zero length in the EDL. hint: lengthen or remove these clips in the "
            "source OTIO if they are meant to be visible."
        )
    return [sentence]


def _quantize_to_frame_boundaries(tl: otio.schema.Timeline, rate: int) -> list[str]:
    """Quantize each track item's source_range to whole-frame boundaries.

    EDL-only conform (ADR-EX-12): cmx_3600 timecode floors fractional frames,
    so adjacent fractional-duration clips desync source vs record cumulative
    boundaries and fail write-then-verify. This walks each track's item
    sequence (Clips and Gaps -- _iter_clips skips Gaps, so the track is walked
    directly) and snaps the cumulative record boundaries to whole frames at
    *rate* using half-up rounding, deriving each item's record duration from
    the rounded boundary difference. Source start is rounded independently to
    the nearest frame and source duration is set equal to the record duration,
    which constructively satisfies cmx_3600's src_duration == rec_duration
    check. Only track items' source_range is mutated (markers, timeline
    metadata, clipwright metadata and effects are untouched). Mutates *tl* (the
    write-time deep copy) in place and returns an aggregated warning list (empty
    when nothing was adjusted). Never raises.
    """
    adjusted_count = 0
    zero_collapse_count = 0
    max_shift_frames = 0.0

    for track in tl.tracks:
        # Each track is an independent record timeline; reset the accumulators
        # per track (EDL is single-video, but close per-track defensively).
        raw_acc = 0.0
        prev_rounded = 0
        for item in track:
            # Only Clip/Gap advance the record timeline. Transitions etc. are
            # not produced by clipwright (ADR-EX-6); source_range-less items are
            # non-reachable under the create contract. Skip both without
            # advancing the accumulator.
            if not isinstance(item, (otio.schema.Clip, otio.schema.Gap)):
                continue
            sr = item.source_range
            if sr is None:
                continue

            # 1) raw duration in rep-rate frames (rate is a single rep_rate).
            dur_frames = sr.duration.value * rate / sr.duration.rate
            raw_acc += dur_frames
            # 2) snap the record boundary half-up; duration = boundary diff.
            new_rounded = _round_half_up(raw_acc)
            quant_dur = new_rounded - prev_rounded  # >= 0 (raw_acc monotonic)
            boundary_shift = abs(new_rounded - raw_acc)  # <= 0.5 + eps
            prev_rounded = new_rounded
            # 3) source start rounded independently; source dur == record dur.
            start_frames = sr.start_time.value * rate / sr.start_time.rate
            new_start = _round_half_up(start_frames)
            start_shift = abs(new_start - start_frames)  # <= 0.5 + eps

            # 4) mutation judgement in frame space (eps-tolerant). Already
            # aligned items keep their source_range untouched (zero mutation).
            if (
                abs(quant_dur - dur_frames) > _FRAME_EPS
                or abs(new_start - start_frames) > _FRAME_EPS
            ):
                item.source_range = otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(float(new_start), rate),
                    duration=otio.opentime.RationalTime(float(quant_dur), rate),
                )
                adjusted_count += 1
                max_shift_frames = max(max_shift_frames, boundary_shift, start_shift)
                if quant_dur == 0:
                    zero_collapse_count += 1

    return _quantize_warnings(
        adjusted_count, zero_collapse_count, max_shift_frames, rate
    )


# ---------------------------------------------------------------------------
# §4: media reference absolutization (on the deep copy only)
# ---------------------------------------------------------------------------


def _absolutize_media_refs(
    tl: otio.schema.Timeline, otio_dir: Path
) -> tuple[int, list[str]]:
    """Absolutize ExternalReference target_urls in place on *tl* (a deep copy).

    Returns ``(absolutized_count, unresolved_refs)``. Per ADR-EX-4:
    - Relative refs are validated for boundary/symlink via check_media_ref
      (a boundary escape or symlink raises PATH_NOT_ALLOWED and fails the whole
      export, CWE-22). Existing in-boundary refs are rewritten to absolute
      POSIX; missing ones are skipped (kept relative) and returned as unresolved.
    - Absolute refs with a symlink component fail; missing absolute refs are
      skipped; existing ones are rewritten to absolute POSIX.

    available_range is left untouched (architecture §4.1 step 5).
    """
    absolutized = 0
    unresolved: list[str] = []

    for clip in _iter_clips(tl):
        mr = clip.media_reference
        if not isinstance(mr, otio.schema.ExternalReference):
            continue
        ref = mr.target_url
        if not ref:
            continue

        norm = _normalize_ref(ref)
        if norm.is_absolute():
            if _has_symlink_component(norm):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Symbolic links are not accepted for a media reference.",
                    hint="Reference a real media file, not a symbolic link.",
                )
            if not norm.is_file():
                unresolved.append(ref)
                continue
            mr.target_url = norm.resolve().as_posix()
            absolutized += 1
        else:
            # Boundary + symlink guard (raises PATH_NOT_ALLOWED on escape).
            check_media_ref(ref, otio_dir, "media")
            joined = otio_dir / norm
            if not joined.is_file():
                unresolved.append(ref)
                continue
            mr.target_url = joined.resolve().as_posix()
            absolutized += 1

    return absolutized, unresolved


# ---------------------------------------------------------------------------
# §5: loss report
# ---------------------------------------------------------------------------


def _truncate_kind(kind: str) -> str:
    """Truncate an unknown marker *kind* to _UNKNOWN_KIND_MAX_LEN chars.

    SR-V-001 (CWE-400): a clipwright marker's metadata["kind"] is
    AI/tool-controlled, unbounded text; embedding it verbatim in the
    aggregated loss-report warning risks an unbounded-size log/response.
    Short kinds pass through unchanged.
    """
    if len(kind) <= _UNKNOWN_KIND_MAX_LEN:
        return kind
    keep = _UNKNOWN_KIND_MAX_LEN - len(_TRUNCATION_SUFFIX)
    return kind[:keep] + _TRUNCATION_SUFFIX


def _loss_report(tl: otio.schema.Timeline) -> list[str]:
    """Aggregate clipwright edit data that the exchange formats cannot carry.

    Whitelist-counts marker kinds, speed warps, timeline-level directives
    (color/denoise/loudness/stabilize/transition), grouping unrecognised
    clipwright marker kinds under a generic "other clipwright annotations"
    bucket (ADR-EX-5). scene_boundary markers are excluded (both adapters
    transcribe their position). Returns a single aggregated warning sentence,
    or [] when nothing is dropped (§5.2).
    """
    counts: dict[str, int] = {}
    other: dict[str, int] = {}

    # (a) markers
    for marker in get_markers(tl):
        cw = get_clipwright_metadata(marker)
        kind = cw.get("kind")
        if not isinstance(kind, str):
            continue
        if kind in _EXCLUDED_MARKER_KINDS:
            continue
        label = _MARKER_KIND_LABELS.get(kind)
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
        else:
            other[kind] = other.get(kind, 0) + 1

    # (b) speed warps (clipwright LinearTimeWarp on a clip)
    for clip in _iter_clips(tl):
        for effect in clip.effects:
            if isinstance(effect, otio.schema.LinearTimeWarp):
                ecw = get_clipwright_metadata(effect)
                if ecw.get("kind") == "speed":
                    counts["speed changes"] = counts.get("speed changes", 0) + 1

    # (c) timeline-level directives
    tl_cw = get_clipwright_metadata(tl)
    for key, label in _DIRECTIVE_LABELS.items():
        if key in tl_cw:
            counts[label] = counts.get(label, 0) + 1
    transition = tl_cw.get("transition")
    if isinstance(transition, collections.abc.Mapping):
        trans_list = transition.get("transitions")
        if trans_list is None:
            n = 1
        else:
            try:
                n = len(trans_list)
            except TypeError:
                n = 1
        counts["transitions"] = counts.get("transitions", 0) + n

    parts: list[str] = []
    for label in _LABEL_ORDER:
        if label in counts:
            parts.append(f"{counts[label]} {label}")
    for kind in sorted(other):
        parts.append(
            f"{other[kind]} other clipwright annotations (kind={_truncate_kind(kind)})"
        )

    if not parts:
        return []

    sentence = (
        "The exchange format does not carry clipwright-specific edit data. "
        "Dropped: " + ", ".join(parts) + ". hint: keep the source OTIO as the "
        "master and re-run clipwright-render to bake these into a flat MP4."
    )
    return [sentence]


# ---------------------------------------------------------------------------
# §11 / ADR-EX-11: adapter write + write-then-verify
# ---------------------------------------------------------------------------


def _write_adapter(
    tl: otio.schema.Timeline, output: str, fmt: str, verify_rate: int
) -> None:
    """Write *tl* to *output* via the *fmt* adapter, then verify by re-reading.

    - Missing adapter -> DEPENDENCY_MISSING (ADR-EX-9; normally does not occur).
    - Adapter write OTIOError (e.g. EDL video-track-count != 1 NotSupportedError,
      spike §(7)) -> OTIO_ERROR; any partial artifact is removed. The hint text
      is chosen per *fmt* (CR-E-004, _WRITE_FAILURE_HINTS): the EDL wording
      names the single-video-track constraint, the fcpxml wording does not
      reference EDL (the caller already requested fcpxml).
    - write-then-verify (ADR-EX-11): re-read with the same adapter (EDL passes
      rate= explicitly, C-2). Any re-read exception -> delete the artifact and
      raise OTIO_ERROR. Judgement is exception presence only (no value compare),
      so normal integer-rate frame quantisation does not false-fail (§13.2).
    All error messages are path-free (CWE-209).
    """
    adapter = _ADAPTER_NAMES[fmt]

    try:
        available = list(otio.adapters.available_adapter_names())
    except Exception:
        available = []
    if adapter not in available:
        raise ClipwrightError(
            code=ErrorCode.DEPENDENCY_MISSING,
            message=f"The {fmt} exchange adapter is not installed.",
            hint=(
                "Reinstall clipwright-export; the otio-*-adapter dependency "
                "should be pulled in automatically."
            ),
        )

    try:
        otio.adapters.write_to_file(tl, output, adapter_name=adapter)
    except otio.exceptions.OTIOError as exc:
        _safe_unlink(output)
        raise ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message=(
                "The timeline could not be written to the requested exchange format."
            ),
            hint=_WRITE_FAILURE_HINTS[fmt],
        ) from exc

    # write-then-verify (ADR-EX-11). Broad except: EDL raises OTIOError/ValueError,
    # FCPXML raises ValueError on structurally broken output (spike §(4)).
    try:
        if fmt == "edl":
            otio.adapters.read_from_file(output, adapter_name=adapter, rate=verify_rate)
        else:
            otio.adapters.read_from_file(output, adapter_name=adapter)
    except Exception as exc:
        _safe_unlink(output)
        raise ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message=(
                "The exchange file was written but failed re-read verification, so "
                "it was discarded to avoid handing off a file that no NLE can import."
            ),
            hint=(
                "Check the timeline for structures the EDL/FCPXML adapter cannot "
                "represent; render to a flat MP4 with clipwright-render as a fallback."
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _export_timeline_inner(
    timeline: str, output: str, options: ExportTimelineOptions
) -> ToolResult:
    """Raising implementation of export_timeline (validation order in module doc)."""
    fmt = options.format
    out = Path(output)
    inp = Path(timeline)

    # --- Step 1: output must not resolve to the input timeline ---
    # (precedes the suffix check; see module docstring for why.)
    check_output_not_source(out, [timeline])

    # --- Step 2: output suffix matches the requested format ---
    # SR L-1 / CWE-209: never echo the offending suffix in message or hint.
    if out.suffix.lower() != _EXPECTED_SUFFIX[fmt]:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The output file extension does not match the requested export format."
            ),
            hint="Use an output path whose extension matches the requested format.",
        )

    # --- Step 3: output parent directory exists ---
    if not out.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="Output directory does not exist.",
            hint="Create the output directory before exporting.",
        )

    # --- Step 4: source exists + load timeline ---
    validate_source_or_basename(
        timeline,
        message=f"Timeline file not found: {inp.name}",
        hint="Verify the timeline path and ensure the file exists.",
    )
    # load_timeline wraps otio.exceptions.OTIOError as OTIO_ERROR, but a
    # structurally malformed .otio raises a bare ValueError (JSON parse error)
    # that escapes it; convert any non-ClipwrightError load failure to
    # OTIO_ERROR here (path-free message, CWE-209).
    try:
        tl = load_timeline(timeline)
    except ClipwrightError:
        raise
    except Exception as exc:
        raise ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message="Failed to load the OTIO timeline file.",
            hint="Specify a valid .otio timeline file.",
        ) from exc

    # --- Step 5: deep copy, then reject non-integer frame rate (ADR-EX-10) ---
    tl_copy: otio.schema.Timeline = tl.deepcopy()
    _check_frame_rates(tl_copy)

    otio_dir = inp.resolve().parent
    warnings_out: list[str] = []

    # --- Step 6a: loss report (§5) ---
    warnings_out.extend(_loss_report(tl_copy))

    # --- Step 6b: absolutize media refs (may raise PATH_NOT_ALLOWED) ---
    _absolutized, unresolved = _absolutize_media_refs(tl_copy, otio_dir)
    if unresolved:
        warnings_out.append(
            f"{len(unresolved)} media reference(s) could not be resolved to an "
            "absolute path; the exchange file keeps the original relative "
            "reference(s). hint: ensure the media files exist at the referenced "
            "location before importing into an NLE."
        )

    rep_rate = _representative_rate(tl_copy)

    # --- Step 6c: EDL-specific warnings + audio removal (ADR-NI-7, §13.3) ---
    if fmt == "edl":
        audio_tracks = [
            t for t in tl_copy.tracks if t.kind == otio.schema.TrackKind.Audio
        ]
        if audio_tracks:
            warnings_out.append(
                "The EDL exchange format carries video cuts only; "
                f"{len(audio_tracks)} audio track(s) were not written to the EDL. "
                "hint: re-link or lay back the audio in your NLE, or use "
                "clipwright-render for a muxed MP4."
            )
            # ADR-NI-7: Remove all Audio tracks from the write-time deep copy
            # (architect ruling: full removal, not "keep up to 2"). This ensures
            # that otio.adapters.write_to_file does not encounter cmx_3600's
            # NotSupportedError for timelines with >2 Audio tracks. The removal
            # happens on tl_copy only; the source OTIO file remains unmodified.
            for track in audio_tracks:
                tl_copy.tracks.remove(track)
        warnings_out.append(
            "EDL timecode carries no frame rate; set your NLE project/sequence to "
            f"{rep_rate} fps on import so the cut points land at the intended times."
        )
        # ADR-EX-12: quantize the write-time copy to whole-frame boundaries so
        # adjacent fractional-duration clips do not desync source vs record and
        # fail write-then-verify. Same rep_rate as verify_rate below (invariant).
        warnings_out.extend(_quantize_to_frame_boundaries(tl_copy, rep_rate))

    # --- Step 7: write adapter + write-then-verify (ADR-EX-11) ---
    _write_adapter(tl_copy, output, fmt, verify_rate=rep_rate)

    vclips = _video_clip_count(tl_copy)
    summary = (
        f"Exported the timeline to {fmt.upper()} as {out.name} "
        f"({vclips} video clip(s)); {len(warnings_out)} warning(s)."
    )
    return ok_result(
        summary=summary,
        data={
            "format": fmt,
            "video_clip_count": vclips,
            "warning_count": len(warnings_out),
        },
        artifacts=[
            {
                "role": "exchange",
                "path": str(out.resolve()),
                "format": fmt,
            }
        ],
        warnings=warnings_out,
    )


def export_timeline(
    timeline: str, output: str, options: ExportTimelineOptions
) -> ToolResult:
    """Export an OTIO timeline to an NLE-interchange format (EDL or FCPXML).

    Input contract (transform): OTIO timeline -> new exchange file. The input
    OTIO file and its media are never modified; only a new sidecar is written.
    Media references are absolutized for NLE hand-off. Non-integer (NTSC) frame
    rates are rejected (ADR-EX-10); clipwright-specific edit data that the
    exchange format cannot carry is reported in warnings (§5).

    Args:
        timeline: Input OTIO timeline file path.
        output: Output exchange file path (extension must match the format:
            edl -> .edl, fcpxml -> .fcpxml, and must differ from timeline).
        options: ExportTimelineOptions with the required format.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _export_timeline_inner(timeline, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # CWE-209: fixed generic wording; never expose input/output paths.
        return error_result(
            ErrorCode.INTERNAL,
            "Exporting the timeline failed due to an internal error.",
            "Retry after verifying that the output directory is writable.",
        )
