"""plan.py — pure logic layer for clipwright-render.

Does not execute ffmpeg/ffprobe. Probe results are received as ProbeInfo arguments
(DC-AM-007). Responsible for three concerns: timeline analysis, filter_complex
construction, and dry-run size estimation.

Design decisions:
- Single re-encode (ADR-1): filter_complex uses trim+concat for frame-accurate
  time control with less degradation than repeated re-encodes.
- concat=n=1 unconditionally (DC-AS-005): simplifies implementation; no branch for
  a single segment. ffmpeg handles n=1 correctly.
- First audio stream only (ADR-7): mapping multiple audio streams adds significant
  complexity; only the first stream is handled in this iteration.
- afftdn denoise injection (§B-2):
  filter_parts order is fixed as trim/atrim → concat → afftdn → scale.
  afftdn (audio chain) and scale (video chain) use independent labels without
  conflict. When has_audio=False, afftdn is not inserted and a warning is
  appended.
- loudness injection (ADR-L5/L5b/L6):
  loudness filter is chained after denoise (acoustically correct order).
  The audio map terminal label is resolved via a cumulative-pipe helper
  (DC-AM-001): [outa] → (denoise present → [outa_dn]) → (track loudness
  present → [outa_ln]). No loudness directive is fully backward compatible
  (ADR-L6).
- Multi-source support (ADR-C1–C12, §7 v2):
  Routing branches on unique source count; single-source backward compatibility
  is strictly preserved (ADR-C3). unique_sources_in_order is the single source
  of truth for input index assignment (ADR-C9-r2).
- Resolution pair constraint (DC-AM-004): width/height with only one specified is
  rejected by RenderOptions model_validator (schemas.py) as ValidationError.
  _build_multi_source_filter_complex assumes either both specified or both None.
- BGM mixing (ADR-B4-r2/B5-r2/B5-r3/B6-r2/B9-r3):
  resolve_bgm detects kind=="bgm" clips from all Audio tracks (ADR-B4-r2).
  When build_plan receives a non-None bgm argument, _append_bgm_pipe appends
  the BGM stage. has_main_audio (presence of main audio) and has_audio_output
  (final output audio presence) are separated (ADR-B5-r2).
  -stream_loop -1 is added by render.py; plan uses atrim=0:{main_dur} for
  duration (ADR-B6-r2). BGM index = len(input_sources) (bgm_source is not
  included in input_sources; DC-AS-005).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import opentimelineio as otio
from clipwright.errors import ClipwrightError, ErrorCode
from pydantic import BaseModel, Field, ValidationError, model_validator

from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ===========================================================================
# Denoise schema (no dependency on clipwright-noise; defined inline for render)
# ===========================================================================


class AfftdnParams(BaseModel):
    """Parameter validation model for the afftdn filter (DC-AS-006).

    nr: noise reduction amount (dB). Range: 0.01–97.
    nf: noise floor (dB). Range: -80 to -20.
    nt: noise type. "w" = white noise, "v" = vinyl noise.
    """

    nr: Annotated[float, Field(ge=0.01, le=97)]
    nf: Annotated[float, Field(ge=-80, le=-20)]
    nt: Literal["w", "v"] = "w"


# SR M-1: allowed value set for afftdn nt (module-level constant).
# Defence-in-depth alongside the Literal["w","v"] type constraint,
# referenced from _append_audio_pipe.
_VALID_NT_VALUES: frozenset[str] = frozenset({"w", "v"})


class DenoiseDirective(BaseModel):
    """Validation model for timeline metadata["clipwright"]["denoise"]
    (DC-AS-006/ADR-N9).

    Validated with Pydantic when render reads the timeline; raises INVALID_INPUT
    on failure. When backend=="afftdn", params are re-validated with AfftdnParams
    (done in render.py). When backend=="deepfilternet", params must be {}.

    SR L-1: max_length constraint on tool/version (guards against oversized string
    injection). SR L-3: measured_noise_floor_db accepts only finite values in -200–0
    dB (no inf/nan).
    """

    # NR-M-1: align max_length with noise-side schemas.py (writer); reader must
    # not be stricter than writer or it will reject valid values. Unified at 64
    # for tool/version.
    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["denoise"]
    backend: Literal["afftdn", "deepfilternet"]
    params: dict[str, Any]
    measured_noise_floor_db: (
        Annotated[float, Field(ge=-200.0, le=0.0, allow_inf_nan=False)] | None
    ) = None


# ===========================================================================
# Loudness schema (no dependency on clipwright-loudness; defined inline for render)
# NR-M-1: align max_length with loudness-side schemas.py (writer); unified at 64.
# ===========================================================================


class LoudnormTarget(BaseModel):
    """Target validation model for loudnorm mode (ADR-L1).

    i: integrated loudness target LUFS (-70 to -5).
    tp: true peak target dBTP (-9 to 0).
    lra: loudness range target LU (1 to 50).
    """

    i: Annotated[float, Field(ge=-70.0, le=-5.0)]
    tp: Annotated[float, Field(ge=-9.0, le=0.0)]
    lra: Annotated[float, Field(ge=1.0, le=50.0)]


class PeakTarget(BaseModel):
    """Target validation model for peak mode (ADR-L2).

    peak_db: peak target dB (-60 to 0).
    """

    peak_db: Annotated[float, Field(ge=-60.0, le=0.0)]


class LoudnormMeasured(BaseModel):
    """Measured-value validation model for loudnorm mode (ADR-L1 linear two-pass).

    All values must be finite (no inf/nan; CWE-20).
    """

    input_i: Annotated[float, Field(allow_inf_nan=False)]
    input_tp: Annotated[float, Field(allow_inf_nan=False)]
    input_lra: Annotated[float, Field(allow_inf_nan=False)]
    input_thresh: Annotated[float, Field(allow_inf_nan=False)]
    target_offset: Annotated[float, Field(allow_inf_nan=False)]


class PeakMeasured(BaseModel):
    """Measured-value validation model for peak mode (ADR-L2).

    max_volume_db: measured peak value dB (-200 to 0). Finite values only.
    """

    max_volume_db: Annotated[float, Field(ge=-200.0, le=0.0, allow_inf_nan=False)]


class LoudnessDirective(BaseModel):
    """Validation model for timeline metadata["clipwright"]["loudness"]
    (ADR-L4/ADR-L6).

    Validated with Pydantic when render reads the timeline; raises INVALID_INPUT
    on failure. Only scope="track" is supported (per_clip deferred until after
    concatenation; DC-AS-003). When mode="loudnorm", measured is required (needed
    for linear application). measured=None is INVALID_INPUT.

    NR-M-1: tool/version max_length=64 (maintains reader/writer compatibility).

    Difference from writer side (clipwright-loudness/schemas.py) — CR-M-001
    reader-strict:
      - schemas.py LoudnessDirective allows measured=None (U-1: design does not
        write loudness directive to OTIO when measurement fails).
      - This reader side treats loudnorm+measured=None as INVALID_INPUT
        (measured_* values are required for linear two-pass; a directive written
        to OTIO with measured=None is itself an invalid state; reader-strict).
    """

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["loudness"]
    mode: Literal["loudnorm", "peak"]
    scope: Literal["track"]
    target: LoudnormTarget | PeakTarget
    # None is kept in the type for compatibility with the writer side
    # (schemas.py). The writer allows measured=None for peak, so the reader
    # must be able to receive it. The invalid loudnorm + measured=None case is
    # rejected reader-strict by the model_validator below (runtime enforcement;
    # see docstring CR-M-001).
    measured: LoudnormMeasured | PeakMeasured | None = None

    @model_validator(mode="after")
    def _validate_measured_required_for_loudnorm(self) -> LoudnessDirective:
        """measured is required for loudnorm mode (needed for linear
        application)."""
        if self.mode == "loudnorm" and self.measured is None:
            raise ValueError(
                "measured is required for loudnorm mode (needed for linear"
                " application)."
            )
        return self

    @model_validator(mode="after")
    def _validate_target_matches_mode(self) -> LoudnessDirective:
        """Validate that mode and target type are consistent."""  # noqa: E501
        if self.mode == "loudnorm" and not isinstance(self.target, LoudnormTarget):
            raise ValueError(
                "loudnorm mode requires a LoudnormTarget (i/tp/lra) for target."
            )
        if self.mode == "peak" and not isinstance(self.target, PeakTarget):
            raise ValueError("peak mode requires a PeakTarget (peak_db) for target.")
        return self


# ===========================================================================
# BGM schema (no dependency on clipwright-bgm; defined inline for render)
# ADR-B9-r2: reader-strict, unknown keys forbidden, allow_inf_nan=False
# NR-M-1: tool/version max_length=64 (consistent with clipwright-bgm writer)
# ===========================================================================


class DuckingDirective(BaseModel):
    """Validation model for BGM ducking settings (ADR-B5-r3/DC-AS-006).

    enabled: when True, injects sidechaincompress to duck BGM under main audio.
    threshold: sidechaincompress threshold parameter. ffmpeg accepted range:
      0.000976563–1.0.
    ratio: sidechaincompress ratio parameter. ffmpeg accepted range: 1.0–20.0.
    SR M-1: allow_inf_nan=False rejects inf/nan originating from OTIO.
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    enabled: bool = False
    threshold: Annotated[float, Field(gt=0.0, le=1.0)]
    ratio: Annotated[float, Field(ge=1.0, le=20.0)]


class BgmDirective(BaseModel):
    """Validation model for BGM clip metadata["clipwright"] (ADR-B9-r2/B9-r3).

    Validated with Pydantic when render reads the timeline; raises INVALID_INPUT
    on failure. Reader-strict (unknown keys forbidden), allow_inf_nan=False.
    fade_in_sec / fade_out_sec default to 0.0 (no fade; ADR-B9-r3).
    afade is only injected when the value is > 0.
    SR I-1: volume_db has ge=-60.0/le=20.0 constraint (consistent with writer
    BgmOptions).
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["bgm"]
    volume_db: Annotated[float, Field(ge=-60.0, le=20.0, allow_inf_nan=False)]
    fade_in_sec: Annotated[float, Field(ge=0)] = 0.0
    fade_out_sec: Annotated[float, Field(ge=0)] = 0.0
    ducking: DuckingDirective


# ===========================================================================
# Data types
# ===========================================================================


@dataclass
class KeptRange:
    """Value object representing a kept segment on the timeline.

    source: target_url of the media file (source path).
    source_range: OTIO TimeRange (held as opentime; seconds conversion is deferred).
    """

    source: str
    source_range: otio.opentime.TimeRange


@dataclass(frozen=True)
class BgmClip:
    """Value object representing BGM clip information (ADR-B4-r2).

    source: target_url of the BGM media file (source path).
    source_range: full duration of the BGM media (OTIO TimeRange).
    directive: BGM directive validated by BgmDirective.
    """

    source: str
    source_range: otio.opentime.TimeRange
    directive: BgmDirective


@dataclass
class ProbeInfo:
    """Value object representing ffprobe probe results (DC-AM-007).

    plan.py receives this type as an argument and never calls subprocess directly.
    bit_rate: when None, estimated_size_bytes cannot be computed (ADR-3).
    width/height/fps: used for output spec normalisation in multi-source paths
        (ADR-C2; optional for backward compatibility).
    """

    has_video: bool
    audio_count: int
    bit_rate: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None


@dataclass
class RenderPlan:
    """Execution plan returned by build_plan.

    filter_complex: single string for the ffmpeg -filter_complex argument
        (prevents injection).
    ffmpeg_args: argument list passed to ffmpeg (excluding -filter_complex).
        All elements are str (M-1).
    segment_count: number of kept segments.
    total_duration_seconds: total output duration (seconds).
    estimated_size_bytes: estimated file size (bytes). None when bit_rate is None.
    warnings: notes about the dry-run estimate.
    input_sources: ordered, deduplicated list of input sources. Single source
        of truth for ADR-C9-r2.
    bgm_source: BGM source path. None when there is no BGM (ADR-B5/B7).
    """

    filter_complex: str
    ffmpeg_args: list[str]
    segment_count: int
    total_duration_seconds: float
    estimated_size_bytes: float | None = None
    warnings: list[str] = field(default_factory=list)
    input_sources: list[str] = field(default_factory=list)
    bgm_source: str | None = None


# ===========================================================================
# Utility functions
# ===========================================================================


def unique_sources_in_order(ranges: list[KeptRange]) -> list[str]:
    """Return source URLs from a KeptRange list in order of first appearance,
    deduplicated (ADR-C9-r2).

    Serves as the single source of truth for input index assignment and
    input_sources. When the same source appears in multiple clips, its position
    is determined by its first occurrence.
    """
    seen: set[str] = set()
    result: list[str] = []
    for r in ranges:
        if r.source not in seen:
            seen.add(r.source)
            result.append(r.source)
    return result


# ===========================================================================
# resolve_kept_ranges
# ===========================================================================


def resolve_kept_ranges(timeline: otio.schema.Timeline) -> list[KeptRange]:
    """Scan the first video track's Clips and return the list of kept segments
    (ADR-5/DC-AS-006).

    - Gaps are skipped (they represent removed regions).
    - Raises UNSUPPORTED_OPERATION if Transitions are present.
    - Raises UNSUPPORTED_OPERATION if two or more video tracks are present.
    - Multiple sources are allowed (ADR-C3; old single-source-only behaviour
      removed per DC-AS-005). Each Clip retains its own source in the KeptRange.
    - Raises INVALID_INPUT if there are zero Clips.

    Returns:
        List of KeptRange (source and source_range held as opentime).
    """
    # Retrieve the first video track (multiple video tracks are not supported)
    video_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video]
    if len(video_tracks) >= 2:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="The timeline contains two or more video tracks.",
            hint=("Use an OTIO timeline with only a single video track."),
        )

    if len(video_tracks) == 0:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="No video track found.",
            hint="Use an OTIO timeline that contains a video track.",
        )

    video_track = video_tracks[0]

    ranges: list[KeptRange] = []

    for item in video_track:
        if isinstance(item, otio.schema.Gap):
            # Gaps represent removed regions; skip them
            continue
        if isinstance(item, otio.schema.Transition):
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="The timeline contains a Transition.",
                hint="Use an OTIO timeline that does not contain Transitions.",
            )
        if isinstance(item, otio.schema.Clip):
            mr = item.media_reference
            if isinstance(mr, otio.schema.MissingReference):
                # MissingReference indicates invalid timeline data (missing
                # reference). Treated as INVALID_INPUT (invalid data) rather than
                # UNSUPPORTED_OPERATION (unsupported configuration).
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message="Media reference is missing (MissingReference).",
                    hint="Use an ExternalReference with a target_url.",
                )
            if not isinstance(mr, otio.schema.ExternalReference):
                # Unsupported configuration (e.g. GeneratorReference) →
                # UNSUPPORTED_OPERATION.
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message=(
                        "Media references other than ExternalReference are not"
                        " supported."
                    ),
                    hint="Use an ExternalReference with a target_url.",
                )
            source = mr.target_url
            source_range = item.source_range
            ranges.append(KeptRange(source=source, source_range=source_range))

    if len(ranges) == 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="No kept segments found (no Clips).",
            hint="Use an OTIO timeline that contains at least one Clip.",
        )

    return ranges


# ===========================================================================
# resolve_bgm
# ===========================================================================


def resolve_bgm(timeline: otio.schema.Timeline) -> BgmClip | None:
    """Scan all Audio tracks and return a BgmClip when a kind=="bgm" clip is
    detected.

    Conforms to ADR-B4-r2.

    Detection is based on the count of kind=="bgm" clips, not the number of Audio
    tracks (DC-AS-002). A single BGM clip is detected correctly even when a main
    audio track (kind!="bgm") is also present.

    Returns:
        BgmClip when exactly one BGM clip exists. None when there are zero
        (backward compatible).

    Raises:
        ClipwrightError(UNSUPPORTED_OPERATION): when two or more BGM clips are
            found (only a single BGM is supported).
        ClipwrightError(INVALID_INPUT): when BGM clip metadata validation fails.
    """
    bgm_clips: list[tuple[str, otio.opentime.TimeRange, Mapping[str, Any]]] = []

    # Scan all Audio tracks and collect kind=="bgm" clips
    for track in timeline.tracks:
        if track.kind != otio.schema.TrackKind.Audio:
            continue
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            cw_meta = item.metadata.get("clipwright")
            # OTIO metadata values are AnyDictionary (not a dict subclass);
            # use the Mapping protocol for type checking (DC-AS-002).
            if not isinstance(cw_meta, Mapping):
                continue
            if cw_meta.get("kind") != "bgm":
                continue
            mr = item.media_reference
            if not isinstance(mr, otio.schema.ExternalReference):
                continue
            source_range = item.source_range
            bgm_clips.append((mr.target_url, source_range, cw_meta))

    if len(bgm_clips) == 0:
        return None

    if len(bgm_clips) >= 2:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=(
                "The timeline contains two or more BGM clips (only a single BGM is"
                " supported)."
            ),
            hint=(
                "Reduce the number of BGM clips in the timeline to one."
                " Mixing multiple BGM tracks is not currently supported."
            ),
        )

    # Exactly one clip: validate BgmDirective and return a BgmClip
    source, source_range, raw_meta = bgm_clips[0]
    try:
        directive = BgmDirective(**raw_meta)
    except (ValidationError, TypeError, ValueError):
        # ValueError is included because future model_validator raise ValueError
        # calls must also be caught (follows the same catch list as
        # _validate_loudness_directive).
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "BGM clip metadata validation failed. Check field names, types,"
                " and values."
            ),
            hint=(
                "Verify that metadata['clipwright'] of the BGM clip has"
                " kind='bgm', volume_db, fade_in_sec, fade_out_sec, and ducking"
                " set correctly."
            ),
        ) from None

    return BgmClip(source=source, source_range=source_range, directive=directive)


# ===========================================================================
# build_plan
# ===========================================================================


# ---------------------------------------------------------------------------
# ADR-F3 (revised): counter-scale constant and helper
# ---------------------------------------------------------------------------

# Default PlayResY used by ffmpeg when converting SRT/VTT to ASS internally.
# libass scales ASS script coordinates by frame_H / PLAYRES_Y_SRT_DEFAULT when
# compositing, so dimension-style values written to force_style are multiplied
# by that ratio at render time. Counter-scale by the inverse (288 / frame_H) to
# make API pixel values render at their intended output-pixel size.
# Pinned by e2e regression; see ADR-F3. Update this constant (and the regression
# test) if a future ffmpeg changes the default PlayResY.
PLAYRES_Y_SRT_DEFAULT: int = 288


def _counter_scale(px_value: int, frame_h: int) -> int:
    """Inverse of libass's frame_h / PLAYRES_Y_SRT_DEFAULT upscale.

    Computes the ASS script-coordinate value that, after libass multiplies it
    by frame_h / PLAYRES_Y_SRT_DEFAULT, yields approximately px_value output
    pixels. Round-trip rounding error is at most ±0.5 px.

    Args:
        px_value: desired dimension in output pixels (e.g. FontSize, MarginV).
        frame_h: height of the frame entering the subtitle stage (output height).

    Returns:
        Script-coordinate integer to pass in force_style.
    """
    return round(px_value * PLAYRES_Y_SRT_DEFAULT / frame_h)


def _escape_filtergraph(path: str) -> str:
    """Escape a path for use in filtergraph filename= / fontsdir= options.

    Verified escape rules (M2 2026-06-11 / DC-AS-005):
    1. Backslash (\\) → \\\\
    2. Colon (:) → \\:
    Applying in this order ensures Windows absolute paths (C:\\...) reach ffmpeg
    without depending on the current working directory.

    Example: C:\\Users\\sub.srt → C\\:\\\\Users\\\\sub.srt
    """  # noqa: E501
    return path.replace("\\", "\\\\").replace(":", "\\:")


def _rgb_to_ass_colour(hex_color: str) -> str:
    """Convert a #RRGGBB colour string to ASS PrimaryColour (&H00BBGGRR).

    Verified in practice (M2 2026-06-11 / DC-AM-002):
    - 8-digit &H00BBGGRR (AA=00 = fully opaque) ensures opaque rendering.
    - Example: #FF0000 (red: R=FF, G=00, B=00) → &H000000FF (BGR order).

    Args:
        hex_color: colour string in '#RRGGBB' format.

    Returns:
        ASS PrimaryColour string in '&H00BBGGRR' format (uppercase).
    """
    # Strip leading # and extract R/G/B
    hex_str = hex_color.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    # ASS uses BGR order; AA=00 (fully opaque), 8 digits
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _build_force_style(
    subtitle: SubtitleOptions,
    is_ass: bool,
    frame_h: int | None = None,
) -> str | None:
    """Build the force_style string for the filtergraph from SubtitleOptions.

    Returns None for ASS input (force_style not applied; ADR-S6-r2 / DC-AS-002).
    Returns None when all style fields are None (omit force_style= entirely).

    When frame_h is provided, dimension-style fields (FontSize, MarginV, Outline)
    are counter-scaled via _counter_scale so that libass's frame_h/288 upscale
    results in output-pixel-accurate rendering (ADR-F3 revised). Non-dimension
    fields (FontName, Alignment, PrimaryColour) are emitted unchanged.
    When frame_h is None, raw values are emitted (legacy PlayResY=288-based
    behaviour; backward compatible).

    Args:
        subtitle: SubtitleOptions with style fields.
        is_ass: True when the subtitle file is ASS (force_style not applied).
        frame_h: height of the output frame entering the subtitle stage.
            None means no counter-scaling (legacy fallback).

    Returns:
        String in 'FontName=...,FontSize=...' format, or None when not needed.
    """
    if is_ass:
        # ASS has embedded styles; do not apply force_style (DC-AS-002)
        return None

    parts: list[str] = []
    if subtitle.font_name is not None:
        # FontName is a string identifier; not a dimension field.
        parts.append(f"FontName={subtitle.font_name}")
    if subtitle.font_size is not None:
        # FontSize is a dimension field: counter-scale when frame_h is known.
        if frame_h is not None:
            fs = _counter_scale(subtitle.font_size, frame_h)
        else:
            fs = subtitle.font_size
        parts.append(f"FontSize={fs}")
    if subtitle.font_color is not None:
        # PrimaryColour is a colour value; not a dimension field.
        ass_colour = _rgb_to_ass_colour(subtitle.font_color)
        parts.append(f"PrimaryColour={ass_colour}")
    if subtitle.outline is not None:
        # Outline is a dimension field: counter-scale when frame_h is known.
        if frame_h is not None:
            # Truncate float to int before counter-scaling; sub-pixel outline
            # widths are not meaningful in ASS coordinates.
            outline_val = _counter_scale(int(subtitle.outline), frame_h)
            parts.append(f"Outline={outline_val}")
        else:
            # :g format removes trailing decimal zeros
            parts.append(f"Outline={subtitle.outline:g}")
    if subtitle.alignment is not None:
        # Alignment is an enumeration (numpad 1–9); not a dimension field.
        parts.append(f"Alignment={subtitle.alignment}")
    if subtitle.margin_v is not None:
        # MarginV is a dimension field: counter-scale when frame_h is known.
        if frame_h is not None:
            mv = _counter_scale(subtitle.margin_v, frame_h)
        else:
            mv = subtitle.margin_v
        parts.append(f"MarginV={mv}")

    if not parts:
        return None
    return ",".join(parts)


def _append_subtitle_filter(
    filter_parts: list[str],
    video_map_label: str,
    subtitle: SubtitleOptions,
    frame_h: int | None = None,
) -> str:
    """Append the subtitle stage (subtitles filter) to filter_parts and return
    the new video label.

    Follows the verified syntax (M2 2026-06-11) per ADR-S4-r2 / ADR-S5-r2 /
    ADR-S6-r2. Does not take a timeline_dir argument (boundary validation is
    centralised in render.py; DC-AS-001).

    Filter format:
    {L_v}subtitles=filename='{esc(path)}'[:fontsdir='{esc(dir)}']
                  [:force_style='{style}'][:charenc=UTF-8][outvsub]

    ASS input: force_style not applied; charenc/fontsdir may still be added
    (DC-AS-002). SRT/VTT input: charenc=UTF-8 and force_style are added
    (M2 truth table).

    Note: original_size is NOT injected. spike-original-size confirmed that
    original_size does not affect force_style-overridden dimensions on
    ffmpeg 8.1.1 (ADR-F3 revised). Counter-scaling via frame_h is used instead.

    Args:
        filter_parts: list of filter_complex segments (mutated in place).
        video_map_label: terminal label of the video chain (e.g. '[outv]').
        subtitle: SubtitleOptions with path already resolved to absolute
            (ADR-S5-r2).
        frame_h: height of the frame entering the subtitle stage. When set,
            dimension-style fields are counter-scaled in _build_force_style
            (ADR-F3 revised). None means no counter-scaling (legacy fallback).

    Returns:
        New video_map_label '[outvsub]'.
    """
    path = subtitle.path
    ext = os.path.splitext(path)[1].lower()
    is_ass = ext == ".ass"

    # Escape the path (verified syntax: \\ → \\\\ then : → \\:)
    esc_path = _escape_filtergraph(path)

    # Build the subtitles filter
    # filename= wraps the absolute path in single quotes (ADR-S5-r2)
    filter_str = f"{video_map_label}subtitles=filename='{esc_path}'"

    # Add fontsdir if specified (applies to ASS, SRT, and VTT)
    if subtitle.fonts_dir is not None:
        esc_dir = _escape_filtergraph(subtitle.fonts_dir)
        filter_str += f":fontsdir='{esc_dir}'"

    # Add force_style (SRT/VTT only; ASS uses its embedded styles).
    # Pass frame_h through for counter-scaling (ADR-F3 revised).
    force_style = _build_force_style(subtitle, is_ass, frame_h)
    if force_style is not None:
        filter_str += f":force_style='{force_style}'"

    # Add charenc=UTF-8 (SRT/VTT only; ASS encodes its own character set)
    if not is_ass:
        filter_str += ":charenc=UTF-8"

    filter_str += "[outvsub]"
    filter_parts.append(filter_str)

    return "[outvsub]"


def _to_seconds(rt: otio.opentime.RationalTime) -> float:
    """Convert RationalTime to seconds (6 decimal places).

    OTIO's type stubs define to_seconds() as Any, so an explicit float
    cast is used to satisfy mypy strict mode.
    """
    return round(float(rt.to_seconds()), 6)


def _validate_denoise_directive(denoise: dict[str, Any]) -> DenoiseDirective:
    """Validate the denoise directive dict with DenoiseDirective; raises
    INVALID_INPUT on failure.

    Also re-validates params with AfftdnParams when backend=="afftdn".
    """
    try:
        directive = DenoiseDirective(**denoise)
    except (ValidationError, TypeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Denoise directive validation failed. Check field names, types,"
                " and values."
            ),
            hint=(
                "Verify that the denoise field in the timeline metadata is in the"
                " correct format. backend must be 'afftdn' or 'deepfilternet'."
            ),
        ) from None

    if directive.backend == "afftdn":
        try:
            AfftdnParams(**directive.params)
        except (ValidationError, TypeError):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "afftdn params validation failed. Check field names, types,"
                    " and values."
                ),
                hint=(
                    "params.nr must be a float in 0.01–97, params.nf in -80 to"
                    " -20, and params.nt must be 'w' or 'v'."
                ),
            ) from None

    return directive


def _validate_loudness_directive(loudness: dict[str, Any]) -> LoudnessDirective:
    """Validate the loudness directive dict; raises INVALID_INPUT on failure.

    Also validates consistency between mode and target type.
    Security: input values are not included in error messages (SR M-1).
    """
    try:
        # Manually convert target/measured to model instances before constructing
        # LoudnessDirective. Pydantic v2 attempts the first matching model for a
        # bare Union[LoudnormTarget, PeakTarget] from a dict; since the two models
        # have different field names, auto-conversion is usually correct, but
        # mode/target consistency is delegated to the model_validator.
        # Pre-converting makes ValidationError easier to attribute to target/measured
        # issues (L-3).
        raw = dict(loudness)
        if isinstance(raw.get("target"), dict):
            mode = raw.get("mode")
            if mode == "loudnorm":
                raw["target"] = LoudnormTarget(**raw["target"])
            elif mode == "peak":
                raw["target"] = PeakTarget(**raw["target"])
        if isinstance(raw.get("measured"), dict):
            mode = raw.get("mode")
            if mode == "loudnorm":
                raw["measured"] = LoudnormMeasured(**raw["measured"])
            elif mode == "peak":
                raw["measured"] = PeakMeasured(**raw["measured"])
        directive = LoudnessDirective(**raw)
    except (ValidationError, TypeError, ValueError):
        # ValueError is included because model_validator uses raise ValueError.
        # ValidationError alone would miss ValueError raised inside model_validator.
        # from None: CWE-209 information leakage prevention.
        # ValidationError details may contain paths, so they are not exposed
        # externally.
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Loudness directive validation failed."
                " Check field names, types, and values."
            ),
            hint=(
                "Check the format of the loudness field in the timeline metadata."
                " mode must be 'loudnorm' or 'peak'; scope must be 'track'."
                " loudnorm mode requires measured."
            ),
        ) from None
    return directive


def _append_audio_pipe(
    filter_parts: list[str],
    has_audio: bool,
    denoise_directive: DenoiseDirective | None,
    loudness_directive: LoudnessDirective | None,
) -> tuple[bool, bool]:
    """Append denoise afftdn / loudness filters to filter_parts and return usage
    flags.

    Shared helper for single-source and multi-source paths (ADR-C11-r2; eliminates
    duplication). Uses [outa] as the starting point and chains labels cumulatively.
    When has_audio=False, nothing is added (warnings are the responsibility of
    build_plan).

    Returns:
        (use_afftdn, use_loudness)
    """
    use_afftdn = False
    use_loudness = False

    if not has_audio:
        return use_afftdn, use_loudness

    # Inject afftdn denoise
    if denoise_directive is not None and denoise_directive.backend == "afftdn":
        params = AfftdnParams(**denoise_directive.params)
        nr_str = f"{params.nr:g}"
        nf_str = f"{params.nf:g}"
        # SR M-1: defence-in-depth with frozenset alongside the Literal["w","v"]
        # constraint (guards against injection if the Literal constraint is ever
        # removed).
        nt_str = params.nt
        if nt_str not in _VALID_NT_VALUES:
            raise ClipwrightError(
                code=ErrorCode.INTERNAL,
                message="afftdn nt parameter is invalid (internal error).",
                hint="params.nt must be 'w' or 'v'.",
            )
        filter_parts.append(
            f"[outa]afftdn=nr={nr_str}:nf={nf_str}:nt={nt_str}[outa_dn]"
        )
        use_afftdn = True

    # Inject loudness
    if loudness_directive is not None:
        loudness_input_label = "[outa_dn]" if use_afftdn else "[outa]"

        if loudness_directive.mode == "loudnorm":
            target = loudness_directive.target
            measured = loudness_directive.measured
            if not isinstance(target, LoudnormTarget) or not isinstance(
                measured, LoudnormMeasured
            ):
                raise ClipwrightError(
                    code=ErrorCode.INTERNAL,
                    message=(
                        "loudnorm directive type consistency is invalid (internal"
                        " error)."
                    ),
                    hint="LoudnessDirective model_validator is not functioning.",
                )
            i_str = f"{target.i:g}"
            tp_str = f"{target.tp:g}"
            lra_str = f"{target.lra:g}"
            mi_str = f"{measured.input_i:g}"
            mtp_str = f"{measured.input_tp:g}"
            mlra_str = f"{measured.input_lra:g}"
            mthresh_str = f"{measured.input_thresh:g}"
            offset_str = f"{measured.target_offset:g}"
            filter_parts.append(
                f"{loudness_input_label}loudnorm="
                f"I={i_str}:TP={tp_str}:LRA={lra_str}"
                f":measured_I={mi_str}:measured_TP={mtp_str}"
                f":measured_LRA={mlra_str}:measured_thresh={mthresh_str}"
                f":offset={offset_str}:linear=true[outa_ln]"
            )
            use_loudness = True

        elif loudness_directive.mode == "peak":
            target = loudness_directive.target
            measured = loudness_directive.measured
            if not isinstance(target, PeakTarget) or not isinstance(
                measured, PeakMeasured
            ):
                raise ClipwrightError(
                    code=ErrorCode.INTERNAL,
                    message=(
                        "peak directive type consistency is invalid (internal error)."
                    ),
                    hint="LoudnessDirective model_validator is not functioning.",
                )
            gain_db = target.peak_db - measured.max_volume_db
            gain_str = f"{gain_db:g}"
            filter_parts.append(f"{loudness_input_label}volume={gain_str}dB[outa_ln]")
            use_loudness = True

    return use_afftdn, use_loudness


def _build_filter_complex(
    ranges: list[KeptRange],
    has_audio: bool,
    denoise_directive: DenoiseDirective | None,
    loudness_directive: LoudnessDirective | None,
    options: RenderOptions,
    probe_info: ProbeInfo | None = None,
) -> tuple[str, str, str, bool, bool]:
    """Build the filter_complex string, video_map_label, and audio_map_label
    (M-2).

    Responsibility: constructs the filter_complex string for trim/atrim → concat
    → denoise afftdn → loudness → scale, and determines the terminal label for
    each chain. Single-source path only (maintains backward compatibility; ADR-C3).

    When width/height are both specified, the scale stage uses fit-based branching
    (ADR-F2) with even-rounding applied to W/H (ADR-F4). probe_info is used to
    determine frame_h for subtitle counter-scaling when width/height are not
    specified (ADR-F3 revised).

    Returns:
        (filter_complex, video_map_label, audio_map_label, use_afftdn,
        use_loudness)
    """
    n = len(ranges)

    # Generate trim/atrim filter segments for each segment
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filter_parts: list[str] = []

    for i, r in enumerate(ranges):
        start = _to_seconds(r.source_range.start_time)
        end = round(start + _to_seconds(r.source_range.duration), 6)
        vl = f"v{i}"
        filter_parts.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[{vl}]"
        )
        video_labels.append(f"[{vl}]")

        if has_audio:
            al = f"a{i}"
            filter_parts.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{al}]"
            )
            audio_labels.append(f"[{al}]")

    # concat filter (interleave video/audio labels as inputs)
    v_count = 1
    a_count = 1 if has_audio else 0
    if has_audio:
        interleaved: list[str] = []
        for vl, al in zip(video_labels, audio_labels, strict=True):
            interleaved.append(vl)
            interleaved.append(al)
        input_labels = "".join(interleaved)
    else:
        input_labels = "".join(video_labels)

    concat_output = "[outv]" if not has_audio else "[outv][outa]"
    filter_parts.append(
        f"{input_labels}concat=n={n}:v={v_count}:a={a_count}{concat_output}"
    )

    # Cumulative audio pipe for denoise/loudness (shared single/multi-source helper)
    use_afftdn, use_loudness = _append_audio_pipe(
        filter_parts, has_audio, denoise_directive, loudness_directive
    )

    # When width/height is specified: integrate scale into filter_complex
    # (ADR-1 compliant). -vf and -filter_complex cannot be used simultaneously
    # (ffmpeg error), so scale is chained after concat output [outv] to produce
    # [outvscaled], and -map [outvscaled] is used instead.
    # Even-rounding applied to W/H (ADR-F4; yuv420p even constraint).
    # fit-based branching: contain / cover / stretch (ADR-F2).
    use_scale = options.width is not None and options.height is not None
    frame_h: int | None = None
    if use_scale:
        raw_w: int = options.width  # type: ignore[assignment]
        raw_h: int = options.height  # type: ignore[assignment]
        W = (raw_w // 2) * 2
        H = (raw_h // 2) * 2
        frame_h = H
        fit = options.fit
        if fit == "contain":
            filter_parts.append(
                f"[outv]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[outvscaled]"
            )
        elif fit == "cover":
            filter_parts.append(
                f"[outv]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1[outvscaled]"
            )
        else:
            # stretch: scale exactly to W:H, no aspect-ratio preservation
            filter_parts.append(f"[outv]scale={W}:{H},setsar=1[outvscaled]")
        video_map_label = "[outvscaled]"
    else:
        video_map_label = "[outv]"
        # No scale stage: use probe height as frame_h for subtitle counter-scale
        # (ADR-F3 revised §5.3). Falls back to None when probe height unavailable.
        if probe_info is not None:
            frame_h = probe_info.height  # may be None → counter-scale skipped

    # Inject subtitle stage after video_map_label is finalised (ADR-S4-r3).
    # When subtitle=None, nothing is done (backward compatible; ADR-S8).
    if options.subtitle is not None:
        video_map_label = _append_subtitle_filter(
            filter_parts, video_map_label, options.subtitle, frame_h
        )

    filter_complex = ";".join(filter_parts)

    # Determine the audio map terminal label via cumulative pipe (ADR-L5b; DC-AM-001):
    # loudness present → [outa_ln], denoise only → [outa_dn], neither → [outa]
    if use_loudness:
        audio_map_label = "[outa_ln]"
    elif use_afftdn:
        audio_map_label = "[outa_dn]"
    else:
        audio_map_label = "[outa]"

    return filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness


def _resolve_target_spec(
    source_probes: dict[str, ProbeInfo],
    first_source: str,
    options: RenderOptions,
) -> tuple[int, int, float]:
    """Determine output spec (target_w, target_h, target_fps) and return it
    (ADR-C4-r2).

    Helper extracted from _build_multi_source_filter_complex.
    When width/height are both specified, they are used; otherwise the first
    source spec is used. Specifying only one is rejected by
    RenderOptions._validate_resolution_pair (DC-AM-004), so this function is
    only reached with both specified or both None.

    Even-number rounding (ADR-C4-r2; yuv420p even constraint) is also applied
    here.

    Returns:
        Tuple of (target_w, target_h, target_fps).

    Raises:
        ClipwrightError: when the first source's resolution or fps cannot be
            obtained.
    """
    first_probe = source_probes[first_source]
    if options.width is not None and options.height is not None:
        raw_w = options.width
        raw_h = options.height
    else:
        if first_probe.width is None or first_probe.height is None:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Cannot obtain resolution from the first source clip.",
                hint=(
                    "Set width/height on the first source in source_probes, or"
                    " specify both width and height in RenderOptions."
                ),
            )
        raw_w = first_probe.width
        raw_h = first_probe.height

    # Even-number rounding (ADR-C4-r2; yuv420p even constraint)
    target_w = (raw_w // 2) * 2
    target_h = (raw_h // 2) * 2

    # fps: use options.fps if specified; otherwise use the first source fps
    if options.fps is not None:
        target_fps: float = options.fps
    else:
        if first_probe.fps is None:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Cannot obtain fps from the first source clip.",
                hint=(
                    "Set fps on the first source in source_probes, or specify"
                    " fps in RenderOptions."
                ),
            )
        target_fps = first_probe.fps

    return target_w, target_h, target_fps


def _build_clip_filters(
    ranges: list[KeptRange],
    source_index: dict[str, int],
    source_probes: dict[str, ProbeInfo],
    has_audio_overall: bool,
    target_w: int,
    target_h: int,
    target_fps: float,
    fit: str = "contain",
) -> tuple[list[str], list[str], list[str]]:
    """Generate video/audio filter strings for each clip (ADR-C5-r2/C7-r2).

    Helper extracted from _build_multi_source_filter_complex.
    Handles per-clip spec normalisation (fps/scale/pad/setsar) and silent audio
    padding (anullsrc) for audio-less clips.

    fit controls the per-clip frame fitting strategy (ADR-F4):
    - 'contain': scale:decrease + pad (letterbox/pillarbox, default).
    - 'cover': scale:increase + crop (fill and clip overflow).
    - 'stretch': scale only (no aspect-ratio preservation).

    Note:
        target_w and target_h must already be even (caller's responsibility;
        _resolve_target_spec applies (v // 2) * 2 before calling this function).
        Passing odd values may cause yuv420p encoding failures.

    Returns:
        Tuple of (filter_parts, video_labels, audio_labels).
    """
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filter_parts: list[str] = []

    for i, r in enumerate(ranges):
        k = source_index[r.source]
        start = _to_seconds(r.source_range.start_time)
        dur = _to_seconds(r.source_range.duration)
        end = round(start + dur, 6)
        vl = f"v{i}"
        # Per-clip video: trim → setpts → fps → scale/pad/crop/setsar (fit-based).
        # fps written with at least 5 decimal places (ADR-C2-r2; NTSC fps precision)
        base = (
            f"[{k}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"fps={target_fps:.5f},"
        )
        if fit == "cover":
            filter_parts.append(
                base
                + f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},setsar=1[{vl}]"
            )
        elif fit == "stretch":
            filter_parts.append(base + f"scale={target_w}:{target_h},setsar=1[{vl}]")
        else:
            # contain (default): scale:decrease + pad + setsar
            filter_parts.append(
                base
                + f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{vl}]"
            )
        video_labels.append(f"[{vl}]")

        if has_audio_overall:
            al = f"a{i}"
            probe = source_probes[r.source]
            if probe.audio_count >= 1:
                # Audio present: atrim → asetpts → aformat for spec normalisation.
                filter_parts.append(
                    f"[{k}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo[{al}]"
                )
            else:
                # No audio: pad with anullsrc (same duration as the video clip)
                filter_parts.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=0:{dur},asetpts=PTS-STARTPTS[{al}]"
                )
            audio_labels.append(f"[{al}]")

    return filter_parts, video_labels, audio_labels


def _build_multi_source_filter_complex(
    ranges: list[KeptRange],
    source_index: dict[str, int],
    source_probes: dict[str, ProbeInfo],
    has_audio_overall: bool,
    denoise_directive: DenoiseDirective | None,
    loudness_directive: LoudnessDirective | None,
    options: RenderOptions,
    first_source: str,
) -> tuple[str, str, str, bool, bool]:
    """Build the filter_complex for the multi-source path
    (ADR-C1/C5-r2/C7-r2/C11-r2).

    Normalises each clip's spec (fps/scale/pad/setsar) before concatenating.
    When has_audio_overall=True, audio-less sources are padded with anullsrc
    (ADR-C7-r2). Output labels are unified with the single-source version
    ([outv]/[outa]; ADR-C11-r2).

    Responsibility breakdown:
    - _resolve_target_spec: determines output spec (target_w/h/fps).
    - _build_clip_filters: generates per-clip video/audio filter strings.
    - This function: assembles the concat filter, calls _append_audio_pipe,
      and determines return values.

    Returns:
        (filter_complex, video_map_label, audio_map_label, use_afftdn,
        use_loudness)
    """
    n = len(ranges)

    # Delegate output spec determination to helper (ADR-C4-r2)
    target_w, target_h, target_fps = _resolve_target_spec(
        source_probes, first_source, options
    )

    # Generate per-clip video/audio filter strings (fit propagated; ADR-F4)
    clip_filter_parts, video_labels, audio_labels = _build_clip_filters(
        ranges,
        source_index,
        source_probes,
        has_audio_overall,
        target_w,
        target_h,
        target_fps,
        fit=options.fit,
    )
    # Carry forward as local variable to append concat filter and audio pipe.
    filter_parts: list[str] = clip_filter_parts

    # concat filter
    v_count = 1
    a_count = 1 if has_audio_overall else 0
    if has_audio_overall:
        interleaved: list[str] = []
        for vl, al in zip(video_labels, audio_labels, strict=True):
            interleaved.append(vl)
            interleaved.append(al)
        input_labels = "".join(interleaved)
    else:
        input_labels = "".join(video_labels)

    concat_output = "[outv]" if not has_audio_overall else "[outv][outa]"
    filter_parts.append(
        f"{input_labels}concat=n={n}:v={v_count}:a={a_count}{concat_output}"
    )

    # Cumulative audio pipe for denoise/loudness (shared single/multi-source
    # helper; ADR-C11-r2)
    use_afftdn, use_loudness = _append_audio_pipe(
        filter_parts, has_audio_overall, denoise_directive, loudness_directive
    )

    # In the multi-source path, per-clip spec normalisation is already done up
    # front, so no post-concat scale is applied (ADR-C5-r2).
    video_map_label = "[outv]"

    # Inject subtitle stage after video_map_label is finalised (ADR-S4-r3).
    # When subtitle=None, nothing is done (backward compatible; ADR-S8).
    # frame_h = target_h (subtitle stage follows per-clip normalisation to target
    # size; ADR-F3 revised §5.3).
    if options.subtitle is not None:
        video_map_label = _append_subtitle_filter(
            filter_parts, video_map_label, options.subtitle, target_h
        )

    filter_complex = ";".join(filter_parts)

    # Determine the audio map terminal label via cumulative pipe
    if use_loudness:
        audio_map_label = "[outa_ln]"
    elif use_afftdn:
        audio_map_label = "[outa_dn]"
    else:
        audio_map_label = "[outa]"

    return filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness


def _append_bgm_pipe(
    filter_parts: list[str],
    bgm: BgmClip,
    audio_map_label: str,
    has_main_audio: bool,
    main_dur: float,
    bgm_index: int,
) -> str:
    """Append the BGM audio chain to filter_parts and return the new
    audio_map_label.

    Conforms to ADR-B5-r2/B5-r3. Follows the verified syntax exactly
    (DC-AS-004).

    When has_main_audio=True:
        Aformats the main terminal label L to [main_fmt], then amixes with BGM.
        ducking OFF:
            [main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]
        ducking ON:
            [main_fmt]asplit→[bgm][main_sc]sidechaincompress→amix→alimiter
            [outa_bgm]
    When has_main_audio=False:
        BGM-only path:
        [{bgm_index}:a]aformat...atrim,asetpts,volume,(afade)[outa_bgm]

    -stream_loop -1 is added by render.py, so plan.py uses only atrim=0:{main_dur}
    for duration (ADR-B6-r2). afade is injected only when fade_in_sec > 0 /
    fade_out_sec > 0 (ADR-B9-r3).
    """
    d = bgm.directive
    vol_str = f"{d.volume_db:g}dB"
    dur_str = f"{main_dur:g}"

    # SR M-3: raise INVALID_INPUT when fade duration exceeds the main duration,
    # as this would produce unintended audio output. BgmOptions cannot enforce an
    # upper bound without knowing main_dur, so a runtime guard is required.
    if d.fade_in_sec > main_dur:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="fade_in_sec exceeds the main content duration.",
            hint=f"Keep fade within the main duration of {main_dur:.2f} seconds.",
        )
    if d.fade_out_sec > main_dur:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="fade_out_sec exceeds the main content duration.",
            hint=f"Keep fade within the main duration of {main_dur:.2f} seconds.",
        )

    # BGM audio chain common part: aformat → atrim → asetpts → volume → (afade).
    # afade is injected only when > 0 (ADR-B9-r3; DC-AM-003)
    bgm_chain = (
        f"[{bgm_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{dur_str},asetpts=PTS-STARTPTS,volume={vol_str}"
    )
    if d.fade_in_sec > 0:
        bgm_chain += f",afade=t=in:st=0:d={d.fade_in_sec:g}"
    if d.fade_out_sec > 0:
        st_out = max(0.0, main_dur - d.fade_out_sec)
        bgm_chain += f",afade=t=out:st={st_out:g}:d={d.fade_out_sec:g}"

    if not has_main_audio:
        # No main audio + BGM-only path (ADR-B5-r2/DC-AS-004): route BGM
        # directly to [outa_bgm]
        filter_parts.append(f"{bgm_chain}[outa_bgm]")
    else:
        # Main audio present: output BGM to intermediate label [bgm], then amix
        filter_parts.append(f"{bgm_chain}[bgm]")

        # Aformat the main terminal label L to [main_fmt] (DC-AS-007)
        filter_parts.append(
            f"{audio_map_label}aformat=sample_rates=48000:channel_layouts=stereo[main_fmt]"
        )

        if d.ducking.enabled:
            # ducking ON: [bgm][main_sc]sidechaincompress input order (DC-AS-006)
            filter_parts.append("[main_fmt]asplit[main_mix][main_sc]")
            filter_parts.append(
                f"[bgm][main_sc]sidechaincompress="
                f"threshold={d.ducking.threshold:g}:ratio={d.ducking.ratio:g}[bgm_duck]"
            )
            filter_parts.append(
                "[main_mix][bgm_duck]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
            )
        else:
            # ducking OFF: [main_fmt][bgm]amix→alimiter (DC-AM-001)
            filter_parts.append(
                "[main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
            )

    return "[outa_bgm]"


def _build_ffmpeg_args(
    filter_complex: str,
    video_map_label: str,
    audio_map_label: str,
    has_audio: bool,
    options: RenderOptions,
    use_multi_source: bool = False,
) -> list[str]:
    """Assemble and return the ffmpeg argument list from filter_complex and map
    labels (M-2).

    Centralises management of filter_complex / -map / codec / fps / crf options.
    ffmpeg_args is unified as list[str]; numeric values are converted with str()
    (M-1).

    When use_multi_source=True, fps has already been normalised by the per-clip
    fps filter in filter_complex, so -r is skipped to avoid unintended double
    resampling (CR M-2). For single-source paths (use_multi_source=False), -r is
    added as before (backward compatible).
    """
    ffmpeg_args: list[str] = [
        "-filter_complex",
        filter_complex,
        "-map",
        video_map_label,
    ]
    if has_audio:
        ffmpeg_args += ["-map", audio_map_label]

    # Map RenderOptions fields to ffmpeg arguments
    if options.video_codec is not None:
        ffmpeg_args += ["-c:v", options.video_codec]
    if options.audio_codec is not None:
        ffmpeg_args += ["-c:a", options.audio_codec]
    # width/height are integrated into filter_complex; -vf is not added (L-4).
    if options.fps is not None:
        if use_multi_source:
            # Multi-source path: fps is already normalised by the per-clip fps filter;
            # -r would cause unintended double resampling (CR M-2)
            pass
        else:
            # Single-source path: add -r as before (backward compatible; ADR-C3).
            ffmpeg_args += ["-r", str(options.fps)]
    if options.crf is not None:
        ffmpeg_args += ["-crf", str(options.crf)]

    return ffmpeg_args


def build_plan(
    ranges: list[KeptRange],
    probe_info: ProbeInfo,
    options: RenderOptions,
    denoise: dict[str, Any] | None = None,
    loudness: dict[str, Any] | None = None,
    source_probes: dict[str, ProbeInfo] | None = None,
    bgm: BgmClip | None = None,
) -> RenderPlan:
    """Return filter_complex string and ffmpeg argument list as a RenderPlan
    (ADR-1/ADR-7).

    Acts as a thin orchestrator: validate → build filter_complex
    (_build_filter_complex or _build_multi_source_filter_complex) →
    append BGM stage (_append_bgm_pipe) →
    build ffmpeg_args (_build_ffmpeg_args) → dry-run estimate and warning
    generation.

    - source_probes not provided or single unique source → single-source path
      (backward compatible).
    - Unique sources ≥ 2 → multi-source path (ADR-C3).
    - No video → UNSUPPORTED_OPERATION (DC-AS-002).
    - Single segment still uses concat=n=1 unconditionally (DC-AS-005).
    - Audio 0: a=0 (-map [outv] only).
    - Audio ≥ 1: a=1, first audio stream only (ADR-7).
    - Trim coordinates: opentime → seconds (6 decimal places) as numeric
      arguments (DC-AS-004).
    - filter_complex returned as a single string (prevents command injection).
    - When bit_rate is None: estimated_size_bytes=None + warning added (ADR-3).
    - When any of codec/resolution/fps/crf is non-None: "estimate is approximate"
      warning (DC-AM-005).
    - denoise: afftdn injection (B-2).
      has_audio=True + backend=="afftdn" → inject afftdn after concat, produce
      [outa_dn]. has_audio=False + denoise → skip afftdn and add warning.
      backend=="deepfilternet" → UNSUPPORTED_OPERATION.
    - loudness: track loudness injection (ADR-L5/L5b/L6).
      loudnorm mode: inject loudnorm linear=true after concat (after denoise if
      present). peak mode: inject volume filter (gain = target_peak - max_volume).
      has_audio=False + loudness → skip filter + add warning.
      peak + denoise together → add warning (DC-AM-002: measurement timing
      mismatch). audio map terminal label resolved via cumulative pipe (DC-AM-001
      ADR-L5b): [outa] → (denoise → [outa_dn]) → (loudness → [outa_ln])
    - When source_probes is provided (unique sources ≥ 2): raises
      UNSUPPORTED_OPERATION for any source with has_video=False (ADR-C12).
    - RenderPlan.input_sources = unique_sources_in_order(ranges) (ADR-C9-r2).
    - bgm: when BgmClip is non-None, appends the BGM stage as the final stage
      (ADR-B4-r2/B5-r2/B5-r3). has_main_audio (main audio presence) and
      has_audio_output (final output audio presence) are separated. BGM index =
      len(input_sources) (bgm_source is not included in input_sources; DC-AS-005).
      bgm=None is identical to the previous behaviour (backward compatible;
      ADR-B7).
    """
    # Validate the denoise directive (raises INVALID_INPUT /
    # UNSUPPORTED_OPERATION on failure)
    denoise_directive: DenoiseDirective | None = None
    if denoise is not None:
        denoise_directive = _validate_denoise_directive(denoise)
        if denoise_directive.backend == "deepfilternet":
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message=(
                    "backend=deepfilternet is not supported for render application."
                ),
                hint=(
                    "Re-detect with backend=afftdn, or wait for a future"
                    " version with deepfilternet render support."
                ),
            )

    # Validate the loudness directive (raises INVALID_INPUT on failure)
    loudness_directive: LoudnessDirective | None = None
    if loudness is not None:
        loudness_directive = _validate_loudness_directive(loudness)

    # Unique source list (single source of truth for ADR-C9-r2)
    input_sources = unique_sources_in_order(ranges)
    n = len(ranges)

    # Branch on source count (ADR-C3)
    use_multi_source = source_probes is not None and len(input_sources) >= 2

    if use_multi_source:
        # Multi-source path. When use_multi_source is True, source_probes is
        # guaranteed to be non-None (by the condition use_multi_source =
        # source_probes is not None and ...). assert is removed by -O, so an
        # if-raise is used for type narrowing (CR-CT-002). This defensive code
        # is structurally unreachable but is intentionally kept for mypy type
        # narrowing (CR L-2: unreachable defensive code is intentional).
        if source_probes is None:
            raise ClipwrightError(
                code=ErrorCode.INTERNAL,
                message="source_probes is None (internal error).",
                hint="Check the caller of build_plan.",
            )
        # SR Info-1: source_probes keys are built by render.py's _render_inner
        # from unique_sources_in_order(ranges) (after boundary validation,
        # existence checks, and probing), so there is no path for external
        # injection of arbitrary keys. Consistency with input_sources is
        # guaranteed on the render.py side.

        # has_video mix check (ADR-C12)
        for src in input_sources:
            probe = source_probes[src]
            if not probe.has_video:
                basename = os.path.basename(src)
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message=(
                        f"A source without a video stream is included: {basename}"
                    ),
                    hint=(
                        f"'{basename}' has no video stream."
                        " Use only media files that contain a video stream."
                    ),
                )

        # Overall audio presence check (ADR-C7-r2).
        has_audio_overall = any(
            source_probes[src].audio_count >= 1 for src in input_sources
        )

        # First source (first clip in ranges)
        first_source = ranges[0].source

        # Source → index mapping (ADR-C1)
        source_index: dict[str, int] = {src: i for i, src in enumerate(input_sources)}

        filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness = (
            _build_multi_source_filter_complex(
                ranges,
                source_index,
                source_probes,
                has_audio_overall,
                denoise_directive,
                loudness_directive,
                options,
                first_source,
            )
        )

        has_audio = has_audio_overall

    else:
        # Single-source path (backward compatible; ADR-C3)
        if not probe_info.has_video:
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="No video stream found.",
                hint="Use a media file that contains a video stream.",
            )

        # Audio presence: multiple audio streams use first only (treated as a=1)
        has_audio = probe_info.audio_count >= 1

        filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness = (
            _build_filter_complex(
                ranges,
                has_audio,
                denoise_directive,
                loudness_directive,
                options,
                probe_info,
            )
        )

    # ---------- Append BGM stage (ADR-B5-r2/B5-r3) ----------
    # has_main_audio: main audio presence after concat (equivalent to existing
    # has_audio). has_audio_output: final output audio presence (has_main_audio
    # or BGM present)
    has_main_audio = has_audio
    bgm_source_out: str | None = None

    if bgm is not None:
        # BGM index = len(input_sources) (bgm_source not included in
        # input_sources; DC-AS-005)
        bgm_index = len(input_sources)
        total_duration_for_bgm = sum(
            _to_seconds(r.source_range.duration) for r in ranges
        )

        # Expand filter_complex into filter_parts list and append the BGM stage
        filter_parts_bgm = filter_complex.split(";")
        audio_map_label = _append_bgm_pipe(
            filter_parts_bgm,
            bgm,
            audio_map_label,
            has_main_audio,
            total_duration_for_bgm,
            bgm_index,
        )
        filter_complex = ";".join(filter_parts_bgm)
        has_audio = (
            True  # BGM present means the final output has audio (has_audio_output=True)
        )
        bgm_source_out = bgm.source

    # ---------- Build ffmpeg_args ----------
    ffmpeg_args = _build_ffmpeg_args(
        filter_complex,
        video_map_label,
        audio_map_label,
        has_audio,
        options,
        use_multi_source=use_multi_source,
    )

    # ---------- Dry-run estimate ----------
    total_duration = sum(_to_seconds(r.source_range.duration) for r in ranges)

    estimated_size: float | None = None
    warnings: list[str] = []

    # has_main_audio=False + denoise directive → denoise skipped (no main
    # audio; DC-AM-004). Note: regardless of BGM presence, denoise does not
    # apply when there is no main audio.
    if denoise_directive is not None and not has_main_audio:
        warnings.append("No audio: denoise skipped — afftdn filter was not applied.")

    # has_main_audio=False + loudness directive → loudness skipped
    # (no main audio; DC-AM-004)
    if loudness_directive is not None and not has_main_audio:
        warnings.append(
            "No audio: loudness skipped — loudnorm/volume filter was not applied."
        )

    # peak + denoise together → measurement timing mismatch warning
    # (DC-AM-002). peak's max_volume was measured before denoise; applying it to
    # denoised audio may deviate from the target peak.
    if (
        loudness_directive is not None
        and loudness_directive.mode == "peak"
        and denoise_directive is not None
        and has_main_audio
    ):
        warnings.append(
            "peak mode combined with denoise: peak max_volume was measured"
            " before denoise was applied; applying it to denoised audio may"
            " deviate from the target peak (DC-AM-002)."
        )

    # Multi-source (unique sources ≥ 2) + loudness → measurement mismatch
    # warning (ADR-C11-r2)
    if loudness_directive is not None and has_main_audio and len(input_sources) >= 2:
        warnings.append(
            "track loudness applied to multi-source concatenation."
            " The measured values are from a single source; applying them to the"
            " entire concatenated track may not be strictly accurate"
            " (per_clip loudness is not supported)."
        )

    # Dry-run estimated size (ADR-C10: based on first source bit_rate)
    # For multi-source, probe_info (first source) is used as the representative value
    if probe_info.bit_rate is not None:
        estimated_size = probe_info.bit_rate * total_duration / 8.0
        if len(input_sources) >= 2:
            warnings.append(
                "Estimated file size is approximate for multi-source input. The"
                " bit_rate of the first source is used as the representative"
                " value."
            )
    else:
        warnings.append("Cannot estimate file size: bit_rate is not available.")

    # When any of codec/resolution/fps/crf/audio_codec is specified, add
    # "estimate is approximate" warning. audio_codec also affects output bit rate
    # and thus estimate accuracy (DC-AM-005)
    if (
        options.video_codec is not None
        or options.audio_codec is not None
        or options.width is not None
        or options.height is not None
        or options.fps is not None
        or options.crf is not None
    ):
        warnings.append(
            "Conversion options (codec/resolution/fps/crf) are specified; the"
            " estimated file size is approximate and the actual size may differ."
        )

    return RenderPlan(
        filter_complex=filter_complex,
        ffmpeg_args=ffmpeg_args,
        segment_count=n,
        total_duration_seconds=total_duration,
        estimated_size_bytes=estimated_size,
        warnings=warnings,
        input_sources=input_sources,
        bgm_source=bgm_source_out,
    )
