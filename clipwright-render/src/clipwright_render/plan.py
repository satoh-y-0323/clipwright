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

import dataclasses
import math
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import opentimelineio as otio
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import get_markers
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from clipwright_render.encoders import ResolvedEncoder
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

# ---------------------------------------------------------------------------
# Speed / warp constants
# ---------------------------------------------------------------------------

# Supported playback speed range.
# These constants are intentionally defined here independently of clipwright-speed's
# own _SPEED_MIN / _SPEED_MAX to avoid cross-package imports and preserve layering.
# Both packages own their validation boundary; when changing either constant,
# update the other package's copy to keep them in sync.
_SPEED_MIN: float = 0.25
_SPEED_MAX: float = 8.0

# Tolerance for treating a time_scalar as identity (1.0) after OTIO round-trip
# float drift. H-1 validation guarantees values are already in [0.25, 8.0] before
# these checks run; 1.0 is the only identity value in that range.
_WARP_IDENTITY_THRESHOLD: float = 1e-9

# Default smoothing value for vidstabtransform.
# Must match DetectShakeOptions.smoothing and _RenderStabilize.smoothing defaults (30).
# When changing this value, update all three locations to keep them in sync.
_DEFAULT_STABILIZE_SMOOTHING: int = 30

# Allowlist for stabilize basename characters used in filtergraph input= option.
# vid.stab input= does not support _escape_filtergraph (\:) escaping, so basenames
# containing filtergraph special characters are rejected (INVALID_INPUT / CWE-78).
_STABILIZE_BASENAME_SAFE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_warp_identity(s: float) -> bool:
    """Return True when *s* is close enough to 1.0 to be treated as no-warp.

    Protects against OTIO round-trip float drift (CR M-2 / ADR-SP-5).
    Callers must ensure *s* is already validated finite and in [_SPEED_MIN, _SPEED_MAX]
    (i.e. resolve_kept_ranges has already run) before calling this function.
    """
    return abs(s - 1.0) <= _WARP_IDENTITY_THRESHOLD


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
    time_scalar: playback speed multiplier from LinearTimeWarp (ADR-SP-2).
        1.0 means no warp (default; backward compatible with ADR-SP-5).
    """

    source: str
    source_range: otio.opentime.TimeRange
    time_scalar: float = 1.0


class KeptRangeList(list):  # type: ignore[type-arg]
    """List subclass that carries an optional reference to the source timeline.

    Used by resolve_kept_ranges to propagate the OTIO timeline object into
    build_plan without changing the public list[KeptRange] contract.  Callers
    that treat the return value as a plain list continue to work unchanged.

    _timeline is accessed via getattr(ranges, '_timeline', None) so that plain
    list arguments (e.g. in existing tests that construct ranges manually) also
    work safely (getattr returns None and no marker lookup is attempted).

    _timeline_path: absolute path to the OTIO file (str or None). Set by
    resolve_kept_ranges via render.py so that _collect_image_overlays can
    reconstruct relative image_paths stored in image_overlay markers (V2-3).
    Accessed via getattr so that plain list callers remain unaffected.
    """

    def __init__(
        self,
        ranges: list[KeptRange],
        timeline: otio.schema.Timeline | None = None,
        timeline_path: str | None = None,
    ) -> None:
        super().__init__(ranges)
        self._timeline: otio.schema.Timeline | None = timeline
        self._timeline_path: str | None = timeline_path


@dataclass(frozen=True)
class TextOverlay:
    """Immutable value object representing a single text overlay instruction.

    Constructed from an OTIO text_overlay marker in _marker_to_text_overlay.
    All fields are validated on construction; invalid values raise INVALID_INPUT
    before this dataclass is instantiated (multi-layer defence; ADR-T4).

    font_path: resolved absolute path to the font file, or None when not yet
        resolved.  render.py resolves this via _resolve_font_path before
        calling build_plan.
    """

    text: str
    start_s: float
    end_s: float  # = start_s + duration_s
    x: str
    y: str
    font_size: int
    font_color: str
    box: bool
    box_color: str
    fade_in_s: float
    fade_out_s: float
    font_path: str | None  # resolved absolute path, or None


# ===========================================================================
# Image overlay constants and dataclass (ADR-OV-4 / V2-1 / V2-5 / V2-9)
# ===========================================================================

# Allowed image file extensions for overlay inputs (V2-3 / render.py re-validates).
# Keep in sync with render._ALLOWED_IMAGE_EXTENSIONS (cross-layer defence-in-depth).
_ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp"}
)

# Maximum number of image_overlay markers per timeline (V2-9).
_MAX_IMAGE_OVERLAYS: int = 64

# Allowlist regex for overlay x/y position expressions (V2-5 / CWE-78).
# Accepts common ffmpeg expression tokens: identifiers, digits, parentheses,
# arithmetic operators, dots, and spaces.  Colons, semicolons, quotes, etc.
# are rejected to prevent filtergraph injection.
_XY_ALLOWLIST_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_()+\-*/. ]+$")


@dataclass(frozen=True)
class ImageOverlay:
    """Immutable value object representing a single image overlay instruction.

    Constructed from an OTIO image_overlay marker in _marker_to_image_overlay.
    All fields are validated on construction; invalid values raise INVALID_INPUT
    before this dataclass is instantiated (multi-layer defence; V2-3).

    image_path: reconstructed absolute path to the overlay image file.
        Stored as an absolute path; used as the -i input for ffmpeg.
    start_s: overlay start time in seconds (program time).
    end_s: overlay end time in seconds (= start_s + duration_s).
    x: ffmpeg overlay x= expression (allowlist-validated; V2-5).
    y: ffmpeg overlay y= expression (allowlist-validated; V2-5).
    scale: width scale factor relative to the image's own width (iw*scale; V2-2).
    opacity: constant alpha channel multiplier 0.0–1.0 (colorchannelmixer aa; V2-1/G1).
    fade_in_s: fade-in duration in seconds; 0 means no fade (V2-1/G2).
    fade_out_s: fade-out duration in seconds; 0 means no fade (V2-1/G2).
    input_index: ffmpeg stream index for this image input (ADR-OV-5/G4).
    """

    image_path: str
    start_s: float
    end_s: float
    x: str
    y: str
    scale: float
    opacity: float
    fade_in_s: float
    fade_out_s: float
    input_index: int


def _marker_to_image_overlay(
    marker: otio.schema.Marker,
    timeline_path: str | None,
    input_index: int,
) -> ImageOverlay:
    """Convert an OTIO image_overlay marker to a validated ImageOverlay.

    Re-validates all fields on the render side (multi-layer defence; V2-3).
    When timeline_path is provided, reconstructs the absolute image path by
    resolving the marker's relative image_path relative to the timeline directory.
    When timeline_path is None, image_path is stored as-is.

    Args:
        marker: OTIO Marker with kind=="image_overlay" in metadata["clipwright"].
        timeline_path: absolute path to the OTIO timeline file, or None.
        input_index: ffmpeg stream index for this image input (ADR-OV-5/G4).

    Returns:
        Validated ImageOverlay value object.

    Raises:
        ClipwrightError(INVALID_INPUT): when any field fails validation.
    """
    cw: Any = marker.metadata.get("clipwright", {})
    if not isinstance(cw, Mapping):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an image_overlay marker with missing metadata."
            ),
            hint="Re-annotate with clipwright_add_overlay.",
        )

    raw_image_path: str = str(cw.get("image_path", ""))
    start_sec: float = float(cw.get("start_sec", 0.0))
    duration_sec: float = float(cw.get("duration_sec", 1.0))
    x: str = str(cw.get("x", "(W-w)/2"))
    y: str = str(cw.get("y", "(H-h)/2"))
    scale: float = float(cw.get("scale", 1.0))
    opacity: float = float(cw.get("opacity", 1.0))
    fade_in_s: float = float(cw.get("fade_in_sec", 0.0))
    fade_out_s: float = float(cw.get("fade_out_sec", 0.0))

    # V2-3: reconstruct absolute path when timeline_path is available.
    if timeline_path is not None:
        reconstructed = (
            Path(timeline_path).resolve().parent / raw_image_path
        ).resolve()
        image_path_abs = str(reconstructed)
    else:
        image_path_abs = raw_image_path

    # Multi-layer re-validation (V2-3 / defence-in-depth).

    # Non-finite guard for start/duration (same pattern as text_overlay NL-1).
    if not math.isfinite(start_sec):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid image overlay:"
                " start_sec is not finite."
            ),
            hint="Re-annotate with a finite start_sec value.",
        )
    if not math.isfinite(duration_sec):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid image overlay:"
                " duration_sec is not finite."
            ),
            hint="Re-annotate with a finite duration_sec value.",
        )

    # Range checks.
    if start_sec < 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=("The timeline contains an invalid image overlay: start_sec < 0."),
            hint="Re-annotate with a non-negative start_sec.",
        )
    if duration_sec <= 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid image overlay: duration_sec <= 0."
            ),
            hint="Re-annotate with a positive duration_sec.",
        )
    if not (0 < scale <= 8.0):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid image overlay:"
                " scale must be in (0, 8.0]."
            ),
            hint="Re-annotate with a scale value in the range (0, 8.0].",
        )
    if not (0 <= opacity <= 1):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid image overlay:"
                " opacity must be in [0, 1]."
            ),
            hint="Re-annotate with an opacity value in the range [0, 1].",
        )
    if fade_in_s + fade_out_s > (duration_sec + 1e-9):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid image overlay:"
                " fade_in_sec + fade_out_sec exceeds duration_sec."
            ),
            hint="Re-annotate so that fade_in_sec + fade_out_sec <= duration_sec.",
        )

    # x/y allowlist (V2-5 / CWE-78).
    for xy_val in (x, y):
        if not _XY_ALLOWLIST_RE.fullmatch(xy_val):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The timeline contains an invalid image overlay:"
                    " x or y contains a disallowed character."
                ),
                hint=(
                    "x/y must contain only alphanumeric characters, parentheses,"
                    " arithmetic operators (+,-,*,/,.), and spaces."
                ),
            )

    # image_path safety: single-quote and control character check (CWE-78).
    for ch in image_path_abs:
        if ch == "'":
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The timeline contains an invalid image overlay:"
                    " image_path contains a single quote."
                ),
                hint="image_path must not contain single quotes.",
            )
        if ch in _CONTROL_CHARS:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The timeline contains an invalid image overlay:"
                    " image_path contains a control character."
                ),
                hint="image_path must not contain control characters.",
            )

    end_s = start_sec + duration_sec

    return ImageOverlay(
        image_path=image_path_abs,
        start_s=start_sec,
        end_s=end_s,
        x=x,
        y=y,
        scale=scale,
        opacity=opacity,
        fade_in_s=fade_in_s,
        fade_out_s=fade_out_s,
        input_index=input_index,
    )


def _collect_image_overlays(
    timeline: otio.schema.Timeline,
    image_index_base: int,
    timeline_path: str | None = None,
) -> list[ImageOverlay]:
    """Read image_overlay markers from the timeline and convert to ImageOverlay objects.

    Called by build_plan when a KeptRangeList with an attached timeline is received.
    Returns an empty list when there are no image_overlay markers (backward compat).

    Args:
        timeline: OTIO timeline object.
        image_index_base: ffmpeg stream index for the first image input (ADR-OV-5/G4).
            = len(input_sources) + (1 if bgm else 0).
        timeline_path: absolute path to the OTIO timeline file, or None.
            When provided, relative image_paths are reconstructed to absolute.

    Returns:
        List of validated ImageOverlay objects, or [].

    Raises:
        ClipwrightError(INVALID_INPUT): when more than _MAX_IMAGE_OVERLAYS markers
            are present, or when any marker fails validation (V2-9).
    """
    markers = get_markers(timeline, kind="image_overlay")
    if not markers:
        return []

    if len(markers) > _MAX_IMAGE_OVERLAYS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"The timeline contains more than {_MAX_IMAGE_OVERLAYS}"
                " image_overlay markers."
            ),
            hint=(
                f"Reduce the number of image_overlay markers to"
                f" {_MAX_IMAGE_OVERLAYS} or fewer."
            ),
        )

    overlays: list[ImageOverlay] = []
    for i, marker in enumerate(markers):
        input_index = image_index_base + i
        overlays.append(_marker_to_image_overlay(marker, timeline_path, input_index))
    return overlays


def _build_overlay_segment(
    o: ImageOverlay,
    base_label: str,
    i: int,
) -> tuple[list[str], str]:
    """Build two filter segment strings for one image overlay (V2-1 confirmed chain).

    Segment 1: image processing chain.
        [{input_index}:v]scale=iw*{scale}:-2,format=rgba,colorchannelmixer=aa={opacity}
        [,fade=t=in:st={start_s}:d={fade_in_s}:alpha=1]
        [,fade=t=out:st={end_s-fade_out_s}:d={fade_out_s}:alpha=1]
        [ov{i}]

    Segment 2: overlay composition.
        {base_label}[ov{i}]overlay=x='{x}':y='{y}':enable='between(t,{start_s},{end_s})'
        [outvimg{i}]

    Note on fade alpha=1: the fade filter with alpha=1 multiplies the existing
    alpha channel (set by colorchannelmixer=aa={opacity}) so the effective alpha
    ramps 0 -> opacity -> 0 (G2 confirmed on ffmpeg 8.1.1).

    DO NOT pass d=0 fades to ffmpeg — omit fade stages entirely when d==0 (V2-1).

    Args:
        o: validated ImageOverlay value object.
        base_label: current terminal video label consumed as the background input.
        i: overlay loop index (position in the overlays list) used for label
            naming (ov{i} / outvimg{i}). Must be passed explicitly by
            _append_overlay_filter; never defaults.

    Returns:
        (segments, new_label) where segments is a list of two filter strings and
        new_label is '[outvimg{i}]'.
    """
    idx = i

    seg1 = (
        f"[{o.input_index}:v]"
        f"scale=iw*{o.scale:g}:-2,"
        f"format=rgba,"
        f"colorchannelmixer=aa={o.opacity:g}"
    )
    if o.fade_in_s > 0:
        seg1 += f",fade=t=in:st={o.start_s:g}:d={o.fade_in_s:g}:alpha=1"
    if o.fade_out_s > 0:
        fade_out_st = o.end_s - o.fade_out_s
        seg1 += f",fade=t=out:st={fade_out_st:g}:d={o.fade_out_s:g}:alpha=1"
    seg1 += f"[ov{idx}]"

    seg2 = (
        f"{base_label}"
        f"[ov{idx}]overlay="
        f"x='{o.x}':y='{o.y}':"
        f"enable='between(t,{o.start_s:g},{o.end_s:g})'"
        f"[outvimg{idx}]"
    )

    return [seg1, seg2], f"[outvimg{idx}]"


def _append_overlay_filter(
    filter_parts: list[str],
    video_map_label: str,
    overlays: list[ImageOverlay],
) -> str:
    """Append image overlay stages to filter_parts and return the new video label.

    No-op when overlays is empty (backward compatible — output byte-identical
    to existing render when image_overlays is absent).

    Image overlays are stacked sequentially: each overlay consumes the previous
    output label as its base_label, so [outv] → [outvimg0] → [outvimg1] → ...

    Args:
        filter_parts: mutable list of filter_complex segments.
        video_map_label: current terminal video label.
        overlays: list of validated ImageOverlay objects.

    Returns:
        New video_map_label (last '[outvimg{N}]') when overlays is non-empty;
        original video_map_label otherwise.
    """
    if not overlays:
        return video_map_label

    for i, o in enumerate(overlays):
        segs, video_map_label = _build_overlay_segment(o, video_map_label, i)
        filter_parts.extend(segs)

    return video_map_label


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
    stabilize_cwd: trf parent directory for run(cwd=...) when stabilize is
        enabled. None when stabilize is absent (backward compatible; §6-E).
    image_sources: ordered list of absolute image overlay paths (ADR-OV-5).
        Images are appended as -i after bgm. Empty by default (backward compat).
    """

    filter_complex: str
    ffmpeg_args: list[str]
    segment_count: int
    total_duration_seconds: float
    estimated_size_bytes: float | None = None
    warnings: list[str] = field(default_factory=list)
    input_sources: list[str] = field(default_factory=list)
    bgm_source: str | None = None
    stabilize_cwd: str | None = None
    image_sources: list[str] = field(default_factory=list)


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


def resolve_kept_ranges(timeline: otio.schema.Timeline) -> KeptRangeList:
    """Scan the first video track's Clips and return the list of kept segments
    (ADR-5/DC-AS-006).

    - Gaps are skipped (they represent removed regions).
    - Raises UNSUPPORTED_OPERATION if Transitions are present.
    - Raises UNSUPPORTED_OPERATION if two or more video tracks are present.
    - Multiple sources are allowed (ADR-C3; old single-source-only behaviour
      removed per DC-AS-005). Each Clip retains its own source in the KeptRange.
    - Raises INVALID_INPUT if there are zero Clips.

    Returns:
        KeptRangeList (list[KeptRange] subclass) with the source timeline
        attached as _timeline for downstream text_overlay marker lookup.
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

    _ranges: list[KeptRange] = []

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
            # Extract time_scalar from the first LinearTimeWarp effect (ADR-SP-2).
            # First-found wins; non-LinearTimeWarp effects are ignored.
            # Default is 1.0 (no warp; ADR-SP-5).
            # Use type() exact check rather than isinstance() because FreezeFrame
            # is a subclass of LinearTimeWarp with time_scalar=0.0; it represents
            # a freeze (speed=0) rather than a playback-speed warp and must be
            # excluded from the warp path.
            time_scalar = 1.0
            for effect in item.effects:
                if type(effect) is otio.schema.LinearTimeWarp:
                    time_scalar = float(effect.time_scalar)
                    # SR H-1 / M-3: validate time_scalar value domain before
                    # building KeptRange. nan/inf and values outside the
                    # supported range [_SPEED_MIN, _SPEED_MAX] are rejected here
                    # — the single chokepoint where untrusted OTIO values enter.
                    if math.isnan(time_scalar) or math.isinf(time_scalar):
                        # NR-L-2 / SR NL-1: message is a fixed string; raw
                        # time_scalar value is intentionally excluded from message
                        # to avoid leaking untrusted OTIO data. Diagnostic range
                        # info belongs in hint only.
                        raise ClipwrightError(
                            code=ErrorCode.INVALID_INPUT,
                            message=(
                                "LinearTimeWarp time_scalar is not a finite number."
                            ),
                            hint=("Supported playback speed range is 0.25 to 8.0."),
                        )
                    if not (_SPEED_MIN <= time_scalar <= _SPEED_MAX):
                        # NR-L-2 / SR NL-1: message is a fixed string; raw
                        # time_scalar value is intentionally excluded from message
                        # to avoid leaking untrusted OTIO data. Diagnostic range
                        # info belongs in hint only.
                        raise ClipwrightError(
                            code=ErrorCode.INVALID_INPUT,
                            message=(
                                "time_scalar is outside the supported playback"
                                " speed range."
                            ),
                            hint=("Supported playback speed range is 0.25 to 8.0."),
                        )
                    break
            _ranges.append(
                KeptRange(
                    source=source, source_range=source_range, time_scalar=time_scalar
                )
            )

    if len(_ranges) == 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="No kept segments found (no Clips).",
            hint="Use an OTIO timeline that contains at least one Clip.",
        )

    return KeptRangeList(_ranges, timeline=timeline)


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


# ===========================================================================
# drawtext helpers (WP-2 — text_overlay → drawtext extension)
# ===========================================================================

# Color allowlist for fontcolor / boxcolor.
# Allows: named colors, #RRGGBB, name@alpha.
# Rejects: spaces, quotes, colons, commas, semicolons — i.e. chars that
# could break the filtergraph option syntax when placed unquoted.
# NOTE: Keep this constant in sync with clipwright-text's _COLOR_PATTERN regex.
# Both packages own their validation boundary; cross-package import is avoided
# (衛星間結合回避方針 / same rationale as _check_output_within_timeline_dir).
_COLOR_ALLOWLIST_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9#@._-]+$")

# Control characters forbidden in text / x / y to prevent filtergraph injection.
# NOTE: Keep this set in sync with clipwright-text's _CONTROL_CHARS set.
_CONTROL_CHARS: frozenset[str] = frozenset(
    chr(c) for c in range(0x00, 0x20)
) | frozenset({chr(0x7F)})

# Maximum overlay.text length used in warning strings (SR-M-2).
# Prevents oversized MCP response payloads when overlay text is very long.
_WARNING_TEXT_MAX_LEN: int = 80

# Platform-default font paths searched when font_path is not specified.
# Order: prefer the first existing path.  font_path kwarg always takes
# precedence (ADR-T5).
# H-1: dict.get() can return None when the key is absent, but the explicit
# default argument guarantees a list[str] here.  The if/elif/else form makes
# the return type unambiguous to mypy strict mode.
_LINUX_FONT_CANDIDATES: list[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
if sys.platform == "win32":
    _PLATFORM_FONT_CANDIDATES: list[str] = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\Arial.ttf",
    ]
elif sys.platform == "darwin":
    _PLATFORM_FONT_CANDIDATES = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
else:
    _PLATFORM_FONT_CANDIDATES = _LINUX_FONT_CANDIDATES


def _escape_drawtext_text(s: str) -> str:
    r"""Escape a string for use as the drawtext text= / x= / y= value.

    Applies escaping in this order (backslash first to avoid double-escaping):
    1. Backslash (\\) → \\\\
    2. Single-quote (') → \'

    The caller wraps the result in single quotes, so these two replacements
    are sufficient to prevent filtergraph injection via the text value.

    Args:
        s: raw string to escape.

    Returns:
        Escaped string suitable for wrapping in single quotes in a drawtext
        option value.
    """
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _resolve_font_path(font_path: str | None) -> str:
    """Resolve a font path to an existing absolute path (ADR-T5).

    When font_path is specified, verify it exists.  When None, search
    platform-default candidates in order.  Raises INVALID_INPUT if no
    usable font is found.

    NOTE: Path.is_file() is used so that tests can mock it via
    ``unittest.mock.patch('pathlib.Path.is_file', return_value=True)``.

    Args:
        font_path: explicit font file path, or None for platform default.

    Returns:
        Absolute path string to an existing font file.

    Raises:
        ClipwrightError(INVALID_INPUT): when the specified path does not
            exist, or when all platform defaults are missing.
    """
    if font_path is not None:
        if not Path(font_path).is_file():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="The specified font file was not found.",
                hint=(
                    "Specify font_path with an absolute path to an existing"
                    " .ttf or .otf font file."
                ),
            )
        # Intentional: no path-boundary restriction; system fonts are allowed by
        # ADR-T5 (same policy as subtitle fonts_dir).  S-L-1.
        return font_path

    # font_path is None: search platform defaults
    for candidate in _PLATFORM_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate

    raise ClipwrightError(
        code=ErrorCode.INVALID_INPUT,
        message="No usable font was found for text overlay.",
        hint=(
            "Specify font_path with an absolute path to a .ttf/.otf font"
            " file (e.g. via the font_path field in clipwright_add_text)."
        ),
    )


def _marker_to_text_overlay(
    marker: otio.schema.Marker,
    resolved_font_path: str,
) -> TextOverlay:
    """Convert an OTIO text_overlay marker to a validated TextOverlay.

    Re-validates all fields on the render side (multi-layer defence; ADR-T4).
    The caller has already resolved the font path via _resolve_font_path.

    Validation mirrors clipwright-text's _validate_text_overlay_fields.
    NOTE: keep validation rules (ranges, colour allowlist, control chars) in
    sync with clipwright-text.  Cross-package import is intentionally avoided
    (衛星間結合回避).

    Args:
        marker: OTIO Marker with kind=="text_overlay" in metadata["clipwright"].
        resolved_font_path: absolute path to an existing font file.

    Returns:
        Validated TextOverlay value object.

    Raises:
        ClipwrightError(INVALID_INPUT): when any field fails validation.
    """
    cw: Any = marker.metadata.get("clipwright", {})
    if not isinstance(cw, Mapping):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains a text_overlay marker with missing metadata."
            ),
            hint="Re-annotate with clipwright_add_text.",
        )

    # L-3: expected_type argument removed (unused; callers only pass key and default).
    def _get(key: str, default: Any = None) -> Any:
        val = cw.get(key, default)
        if val is None and default is None:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The timeline contains an invalid text overlay: a required"
                    " field is missing."
                ),
                hint="Re-annotate with clipwright_add_text using valid values.",
            )
        return val

    start_sec: float = float(_get("start_sec", 0.0))
    duration_sec: float = float(_get("duration_sec", 1.0))
    text: str = str(_get("text", ""))
    x: str = str(_get("x", "(w-tw)/2"))
    y: str = str(_get("y", "h-th-40"))
    font_size: int = int(_get("font_size", 48))
    font_color: str = str(_get("font_color", "white"))
    box: bool = bool(_get("box", False))
    box_color: str = str(_get("box_color", "black@0.5"))
    fade_in_s: float = float(_get("fade_in_sec", 0.3))
    fade_out_s: float = float(_get("fade_out_sec", 0.3))

    # M-2 / S-L-2: validate font_path for dangerous characters and max_length
    # before passing to _resolve_font_path.  Single-quote breaks the
    # fontfile='...' quoting used in _build_drawtext_segment; control characters
    # (including newline \n) can corrupt the filtergraph string.
    # max_length=4096 matches the POSIX PATH_MAX convention.
    # NOTE: Keep this validation in sync with clipwright-text's font_path checks.
    raw_font_path: Any = cw.get("font_path")
    if raw_font_path is not None:
        fp_str = str(raw_font_path)
        if len(fp_str) > 4096:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The timeline contains an invalid text overlay:"
                    " font_path exceeds the maximum allowed length."
                ),
                hint="Use a font_path of 4096 characters or fewer.",
            )
        for ch in fp_str:
            if ch == "'" or ch in _CONTROL_CHARS:
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "The timeline contains an invalid text overlay:"
                        " font_path contains a disallowed character."
                    ),
                    hint=(
                        "font_path must not contain single-quotes or control"
                        " characters (including newlines)."
                    ),
                )

    # Re-validate value ranges (multi-layer defence)
    # NL-1: reject non-finite values (inf/-inf/nan) before the < 0 / <= 0 checks,
    # because inf passes both guards and propagates to RationalTime.from_seconds(inf)
    # and ultimately generates 'between(t,inf,inf)' in the filtergraph, causing
    # SUBPROCESS_FAILED in ffmpeg.
    if not math.isfinite(start_sec):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid text overlay:"
                " start_sec is not finite."
            ),
            hint=(
                "Re-annotate with clipwright_add_text using a finite start_sec value."
            ),
        )
    if not math.isfinite(duration_sec):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid text overlay:"
                " duration_sec is not finite."
            ),
            hint=(
                "Re-annotate with clipwright_add_text using a finite"
                " duration_sec value."
            ),
        )
    if start_sec < 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The timeline contains an invalid text overlay: start_sec < 0.",
            hint="Re-annotate with clipwright_add_text using a non-negative start_sec.",
        )
    if duration_sec <= 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The timeline contains an invalid text overlay: duration_sec <= 0.",
            hint="Re-annotate with clipwright_add_text using a positive duration_sec.",
        )
    if font_size <= 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The timeline contains an invalid text overlay: font_size <= 0.",
            hint="Re-annotate with clipwright_add_text using a positive font_size.",
        )

    # S-M-3: reject empty or whitespace-only text.
    # Whitespace-only strings produce an effectively invisible overlay (CWE-20).
    # NOTE: Keep in sync with clipwright-text's _validate_text_overlay_fields.
    if not text.strip():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid text overlay: text must not be empty."
            ),
            hint="Re-annotate with clipwright_add_text using a non-empty text value.",
        )

    # M-1 / S-M-2: validate that fade_in + fade_out does not exceed duration.
    # Ensures the alpha fade expression is meaningful and consistent with the
    # overlay duration (multi-layer defence; ADR-T4).
    # Tolerance 1e-9 guards against float noise from OTIO round-trips.
    # NOTE: Keep in sync with clipwright-text's _validate_text_overlay_fields.
    if fade_in_s + fade_out_s > (duration_sec + 1e-9):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The timeline contains an invalid text overlay:"
                " fade_in_sec + fade_out_sec exceeds duration_sec."
            ),
            hint=(
                "Re-annotate with clipwright_add_text so that"
                " fade_in_sec + fade_out_sec <= duration_sec."
            ),
        )

    # S-M-1: validate text / x / y for control characters.
    # Fixed message wording — field names are intentionally excluded from
    # .message to prevent information leakage (CWE-209).  Diagnostic details
    # belong in .hint only.
    # NOTE: Keep in sync with clipwright-text's _validate_text_overlay_fields.
    for _field_val in (text, x, y):
        for ch in _field_val:
            if ch in _CONTROL_CHARS:
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "The timeline contains an invalid text overlay:"
                        " a field contains a control character."
                    ),
                    hint=(
                        "Re-annotate with clipwright_add_text without"
                        " control characters in text, x, or y."
                    ),
                )

    # S-M-1: validate colour values against allowlist.
    # Fixed message wording — color field names are intentionally excluded from
    # .message (CWE-209); diagnostic details belong in .hint only.
    # NOTE: Keep in sync with clipwright-text's _validate_text_overlay_fields.
    for color_val in (font_color, box_color):
        if not _COLOR_ALLOWLIST_RE.match(color_val):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The timeline contains an invalid text overlay:"
                    " a color value is not in the allowed format."
                ),
                hint=(
                    "Use a named color, #RRGGBB, or name@alpha"
                    " (e.g. white, #FFCC00, black@0.5)."
                ),
            )

    end_s = round(start_sec + duration_sec, 6)

    return TextOverlay(
        text=text,
        start_s=start_sec,
        end_s=end_s,
        x=x,
        y=y,
        font_size=font_size,
        font_color=font_color,
        box=box,
        box_color=box_color,
        fade_in_s=fade_in_s,
        fade_out_s=fade_out_s,
        font_path=resolved_font_path,
    )


def _build_alpha_expr(o: TextOverlay) -> str:
    """Build the ffmpeg drawtext alpha= expression for fade-in/fade-out.

    Zero-division guard: when fade_in_s or fade_out_s is 0, the corresponding
    branch is omitted (returning '1' instead of '(t-s)/0').

    Expression shape (when both fades are non-zero):
        if(lt(t,s+fi),(t-s)/ fi,if(gt(t,e-fo),(e-t)/ fo,1))

    Division uses a space before the denominator (``/ {fi}``) to prevent the
    string ``/0`` from appearing when a denominator like ``0.3`` is used.
    ffmpeg's av_expr_eval ignores whitespace in expressions.

    Args:
        o: TextOverlay with start_s, end_s, fade_in_s, fade_out_s populated.

    Returns:
        Alpha expression string suitable for alpha= in a drawtext filter.
    """
    s = o.start_s
    e = o.end_s
    fi = o.fade_in_s
    fo = o.fade_out_s

    has_fi = fi > 0
    has_fo = fo > 0

    if not has_fi and not has_fo:
        return "1"

    if has_fi and not has_fo:
        return f"if(lt(t,{s}+{fi}),(t-{s})/ {fi},1)"

    if not has_fi and has_fo:
        return f"if(gt(t,{e}-{fo}),({e}-t)/ {fo},1)"

    # Both fades non-zero
    return f"if(lt(t,{s}+{fi}),(t-{s})/ {fi},if(gt(t,{e}-{fo}),({e}-t)/ {fo},1))"


def _build_drawtext_segment(o: TextOverlay) -> str:
    """Build a single drawtext filter option string for one TextOverlay.

    Encoding rules (ADR-T7 / §8):
    - text / x / y: single-quoted + _escape_drawtext_text applied.
    - fontfile: _escape_filtergraph applied, then single-quoted (Windows path
      support; same convention as subtitle filename=).
    - fontcolor / boxcolor: allowlist-validated, placed without quotes.
    - fontsize: int, no quoting needed.
    - enable: single-quoted 'between(t,start,end)'.
    - alpha: single-quoted expression string.
    - box / boxcolor: omitted entirely when o.box is False.

    Args:
        o: validated TextOverlay value object.

    Returns:
        drawtext filter option string (without leading/trailing
        filter separators).
    """
    # S-L-3: replace assert (no-op under python -O) with an explicit if-raise so
    # that python -O execution still produces a diagnostic error rather than an
    # AttributeError or incorrect output.
    if o.font_path is None:
        raise ClipwrightError(
            code=ErrorCode.INTERNAL,
            message="font_path is not resolved.",
            hint=("This is an internal error; report with reproduction steps."),
        )

    esc_text = _escape_drawtext_text(o.text)
    esc_fontfile = _escape_filtergraph(o.font_path)
    esc_x = _escape_drawtext_text(o.x)
    esc_y = _escape_drawtext_text(o.y)
    alpha_expr = _build_alpha_expr(o)

    seg = (
        f"drawtext=text='{esc_text}'"
        f":fontfile='{esc_fontfile}'"
        f":fontsize={o.font_size}"
        f":fontcolor={o.font_color}"
        f":x='{esc_x}'"
        f":y='{esc_y}'"
        f":enable='between(t,{o.start_s},{o.end_s})'"
        f":alpha='{alpha_expr}'"
    )
    if o.box:
        seg += f":box=1:boxcolor={o.box_color}"
    return seg


def retime_text_overlays(
    overlays: list[TextOverlay],
    tmap: Any,  # ProgramTimeMap from clipwright_render.retiming (inline import)
    retime: bool,
) -> tuple[list[TextOverlay], list[str]]:
    """Return re-timed overlays (1 marker -> N windows) and warning strings.

    When retime is False or tmap has no cut and no warp (identity), returns
    overlays unchanged and an empty warning list.

    Each output TextOverlay is produced by converting (start_s, end_s) to
    RationalTime, calling remap_window, then converting each ProgramWindow back
    to seconds via _to_seconds (final edge only — NFR-2 / D7).

    1 marker -> N copies (one per ProgramWindow).  Windows are flattened into
    the returned list so that _append_drawtext_filter receives N independent
    TextOverlay objects (ADR-1: no OR-enable concatenation).

    Warning text follows FR-6 / §5 (B3):
      - drop:  "text_overlay '{text}' dropped (source range removed by cuts)"
      - split: "text_overlay '{text}' split across cut boundary into {N} windows"
      - clip:  "text_overlay '{text}' clipped at cut boundary (context lost)"
      - shift: "text_overlay '{text}' shifted by {delta:.3f}s due to re-timing"

    Warning strings are composed here (ADR-4); remap_window returns only
    disposition flags.

    Args:
        overlays: List of TextOverlay objects from _collect_text_overlays.
        tmap:     ProgramTimeMap built by retiming.build_program_time_map.
        retime:   False -> skip re-timing regardless of tmap content.

    Returns:
        Tuple of (re-timed overlays, warning strings).
    """
    # Inline import to break the circular dependency: retiming imports plan for
    # KeptRange / _is_warp_identity; plan imports retiming only at runtime here.
    import clipwright_render.retiming as retiming_mod

    # No-op conditions
    if not retime or (not tmap.has_cut and not tmap.has_warp):
        return overlays, []

    result_overlays: list[TextOverlay] = []
    result_warnings: list[str] = []

    for overlay in overlays:
        # CR-M-2: use RationalTime.from_seconds with a high fixed rate (1000) to
        # avoid non-integer value when overlay.start_s * fps is not an integer.
        # Markers carry float seconds; rate=1000 matches retiming.py's internal
        # ms-rate convention (_parse_timecode).
        src_start = otio.opentime.RationalTime.from_seconds(overlay.start_s, 1000)
        src_end = otio.opentime.RationalTime.from_seconds(overlay.end_s, 1000)

        rr = retiming_mod.remap_window(tmap, src_start, src_end)

        # SR-M-2: truncate overlay.text in warning strings to prevent large MCP
        # response payloads when text content is excessively long.
        _text = overlay.text
        warn_text = (
            _text[:_WARNING_TEXT_MAX_LEN] + "…"
            if len(_text) > _WARNING_TEXT_MAX_LEN
            else _text
        )

        if rr.dropped:
            result_warnings.append(
                f"text_overlay '{warn_text}' dropped (source range removed by cuts)"
            )
            # Dropped overlays produce no output TextOverlay
            continue

        if rr.split:
            n_wins = len(rr.windows)
            result_warnings.append(
                f"text_overlay '{warn_text}' split across cut boundary"
                f" into {n_wins} windows"
            )
        elif rr.clipped:
            result_warnings.append(
                f"text_overlay '{warn_text}' clipped at cut boundary (context lost)"
            )
        elif rr.shifted:
            first_prog_start_s = _to_seconds(rr.windows[0].program_start)
            delta = first_prog_start_s - overlay.start_s
            result_warnings.append(
                f"text_overlay '{warn_text}' shifted by {delta:.3f}s due to re-timing"
            )

        for win in rr.windows:
            new_start_s = _to_seconds(win.program_start)
            new_end_s = _to_seconds(win.program_end)
            result_overlays.append(
                dataclasses.replace(overlay, start_s=new_start_s, end_s=new_end_s)
            )

    return result_overlays, result_warnings


def _append_drawtext_filter(
    filter_parts: list[str],
    video_map_label: str,
    overlays: list[TextOverlay],
) -> str:
    """Append the drawtext stage to filter_parts and return the new video label.

    Modelled after _append_subtitle_filter (745-811).  When overlays is empty,
    filter_parts is left unchanged and video_map_label is returned as-is
    (backward compatible; ADR-T3).

    When overlays is non-empty, all segments are comma-joined into a single
    filter chain entry:
        {label}seg1,seg2,...[outvtext]
    and [outvtext] is returned as the new video_map_label.

    OQ-4: multiple overlays share one [outvtext] label via comma-joining
    (no intermediate [outvtext0]/[outvtext1] labels).

    Args:
        filter_parts: mutable list of filter_complex segments.
        video_map_label: current terminal video label (e.g. '[outv]').
        overlays: list of validated TextOverlay objects.

    Returns:
        New video_map_label ('[outvtext]') when overlays is non-empty;
        original video_map_label otherwise.
    """
    if not overlays:
        return video_map_label

    segments = ",".join(_build_drawtext_segment(o) for o in overlays)
    filter_parts.append(f"{video_map_label}{segments}[outvtext]")
    return "[outvtext]"


def _collect_text_overlays(
    timeline: otio.schema.Timeline,
) -> list[TextOverlay]:
    """Read text_overlay markers from the timeline, resolve fonts, and convert.

    Called by build_plan when a KeptRangeList with an attached timeline is
    received.  Returns an empty list when there are no text_overlay markers
    (preserving backward compatibility).

    Font resolution: all overlays that share the same font_path value reuse the
    same resolved path.  Resolution failure raises INVALID_INPUT.

    Args:
        timeline: OTIO timeline object (from KeptRangeList._timeline).

    Returns:
        List of validated TextOverlay objects, or [].
    """
    markers = get_markers(timeline, kind="text_overlay")
    if not markers:
        return []

    overlays: list[TextOverlay] = []
    # Cache resolved font paths to avoid repeated is_file() calls for the same
    # raw font_path value.
    font_cache: dict[str | None, str] = {}

    for marker in markers:
        cw: Any = marker.metadata.get("clipwright", {})
        raw_font = cw.get("font_path") if isinstance(cw, Mapping) else None

        if raw_font not in font_cache:
            font_cache[raw_font] = _resolve_font_path(
                str(raw_font) if raw_font is not None else None
            )
        resolved = font_cache[raw_font]

        overlays.append(_marker_to_text_overlay(marker, resolved))

    return overlays


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


# ===========================================================================
# Stabilize schema (no dependency on clipwright-stabilize; defined inline for render)
# ADR-ST-5: reader uses extra="ignore" so unused keys (severity/shakiness/accuracy)
# do not break validation. Reader must not be stricter than writer (ADR-CO-3 parity).
# ===========================================================================


class _RenderStabilize(BaseModel):
    """Reader-side validation of the stabilize directive (no dependency on
    clipwright-stabilize). Only trf_path / smoothing are consumed; tool /
    version / kind / severity / shakiness / accuracy are ignored. Reader must
    not be stricter than writer (ADR-CO-3 parity)."""

    model_config = {"extra": "ignore", "allow_inf_nan": False}

    trf_path: str
    smoothing: Annotated[int, Field(ge=0, le=1000)] = 30


def _validate_stabilize(stabilize: dict[str, Any]) -> _RenderStabilize | None:
    """Validate the stabilize directive; raises INVALID_INPUT on failure.

    Returns None when trf_path is absent/None (no stabilization; backward compat).
    Security: input values are not included in error messages (CWE-209).
    """
    if stabilize.get("trf_path") is None:
        return None
    try:
        return _RenderStabilize(**stabilize)
    except (ValidationError, TypeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Stabilize directive validation failed. Check trf_path and smoothing."
            ),
            hint="trf_path must be a path string and smoothing an integer in 0..1000.",
        ) from None


def _validate_stabilize_basename(basename: str) -> None:
    """Reject stabilize trf basenames with filtergraph special characters (CWE-78).

    vid.stab input= does not support _escape_filtergraph (\\:) escaping, so basenames
    must consist only of safe characters (alphanumeric, hyphens, underscores, dots).
    Characters such as ':', ';', '[', ']', '\\', ',', and newlines would be
    interpreted by the filtergraph parser and could cause unintended injection.

    The normal flow produces safe basenames because fix-stabilize-pkg sanitizes the
    stem in analyze.py. This function provides defence-in-depth for OTIO-embedded
    trf_path values that arrive directly at the render stage (e.g. crafted OTIO).

    Raises:
        ClipwrightError: INVALID_INPUT when the basename contains disallowed characters.
            Raw input is not included in the message (CWE-209).
    """
    if not _STABILIZE_BASENAME_SAFE_RE.match(basename):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Stabilize trf filename contains characters not allowed in"
                " the filtergraph input= option."
            ),
            hint=(
                "The .trf filename must contain only alphanumeric characters,"
                " hyphens, underscores, and dots."
            ),
        )


# ===========================================================================
# Color eq schema (no dependency on clipwright-color; defined inline for render)
# ADR-CO-3: re-declares the same ranges as EqParams in clipwright-color to avoid
# satellite-to-satellite coupling. When changing ranges, update both copies.
# ===========================================================================


class _RenderEqParams(BaseModel):
    """Reader-side validation of color["eq"] (no dependency on clipwright-color).

    Ranges mirror clipwright-color's EqParams (writer). Reader must not be
    stricter than writer. Unknown keys forbidden; inf/nan rejected (CWE-20).
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    brightness: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    contrast: Annotated[float, Field(ge=0.0, le=2.0)] = 1.0
    saturation: Annotated[float, Field(ge=0.0, le=2.0)] = 1.0
    gamma: Annotated[float, Field(ge=0.1, le=10.0)] = 1.0


def _validate_color_eq(color: dict[str, Any]) -> _RenderEqParams | None:
    """Validate the color directive's eq block; raises INVALID_INPUT on failure.

    Only color["eq"] is consumed. tool / version / kind / target_luma / measured
    are intentionally ignored (the render side only needs eq parameters). When eq
    is absent or None, returns None (treated as no color correction; backward
    compatible).
    Security: input values are not included in error messages (CWE-209).
    """
    raw_eq = color.get("eq")
    if raw_eq is None:
        return None
    try:
        return _RenderEqParams(**raw_eq)
    except (ValidationError, TypeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Color eq directive validation failed."
                " Check field names, types, and values."
            ),
            hint=(
                "color['eq'] must have brightness in -1..1, contrast and"
                " saturation in 0..2, and gamma in 0.1..10."
            ),
        ) from None


def _append_eq_filter(
    filter_parts: list[str],
    video_map_label: str,
    eq: _RenderEqParams | None,
) -> str:
    """Append the eq color-correction stage and return the new video label.

    No-op when eq is None (backward compatible). :g formatting removes trailing
    zeros (consistent with afftdn nr/nf and atempo formatting).
    Placed scale-after / subtitle-before (ADR-CO-4).
    """
    if eq is None:
        return video_map_label
    seg = (
        f"eq=brightness={eq.brightness:g}"
        f":contrast={eq.contrast:g}"
        f":saturation={eq.saturation:g}"
        f":gamma={eq.gamma:g}"
    )
    filter_parts.append(f"{video_map_label}{seg}[outveq]")
    return "[outveq]"


# ===========================================================================
# Reframe schema and filter helpers (no dependency on clipwright-reframe; inline)
# Architecture §7.4 / §2 / §3 / §6.
# ADR-RF-1: ranges and color lists are intentionally re-declared here to avoid
# satellite-to-satellite coupling.  When changing, update both copies.
# ===========================================================================

# CSS named colors accepted as pad_color — curated safe subset (AC-05 / CWE-78).
_RF_NAMED_COLORS: frozenset[str] = frozenset(
    {
        "black",
        "blue",
        "cyan",
        "gray",
        "green",
        "grey",
        "magenta",
        "red",
        "white",
        "yellow",
    }
)

# Hex color pattern: #RRGGBB or 0xRRGGBB (exactly 6 hex digits; case-insensitive).
_RF_HEX_COLOR_RE: re.Pattern[str] = re.compile(r"^(#|0x)[0-9A-Fa-f]{6}$")


def _rf_validate_pad_color(value: str) -> str:
    """Accept only allowlisted color names or #RRGGBB / 0xRRGGBB hex (AC-05).

    Rejects filtergraph special characters to prevent command injection (CWE-78).
    """
    stripped = value.strip()
    if stripped != value or not stripped:
        raise ValueError(
            f"pad_color must not be empty or contain leading/trailing whitespace:"
            f" {value!r}"
        )
    if value in _RF_NAMED_COLORS:
        return value
    if _RF_HEX_COLOR_RE.match(value):
        return value
    raise ValueError(
        f"pad_color {value!r} is not in the allowed list."
        " Use a CSS color name (black, white, red, …) or #RRGGBB / 0xRRGGBB hex."
    )


# Crop offset dict: anchor → (ox_expr, oy_expr) in iw/ih coordinates (§2.2).
# Max clamp bounds: ox_max = iw-W, oy_max = ih-H (replaced with literal integers
# at call time; ffmpeg crop does not expose W/H as variables).
_CROP_OX: dict[str, str] = {
    "center": "(iw-{W})/2",
    "top": "(iw-{W})/2",
    "bottom": "(iw-{W})/2",
    "left": "0",
    "right": "iw-{W}",
    "top_left": "0",
    "top_right": "iw-{W}",
    "bottom_left": "0",
    "bottom_right": "iw-{W}",
}
_CROP_OY: dict[str, str] = {
    "center": "(ih-{H})/2",
    "top": "0",
    "bottom": "ih-{H}",
    "left": "(ih-{H})/2",
    "right": "(ih-{H})/2",
    "top_left": "0",
    "top_right": "0",
    "bottom_left": "ih-{H}",
    "bottom_right": "ih-{H}",
}

# Pad offset dict: anchor → (ox_expr, oy_expr) in ow/oh coordinates (§2.3).
# Max clamp bounds: ox_max = ow-iw, oy_max = oh-ih.
_PAD_OX: dict[str, str] = {
    "center": "(ow-iw)/2",
    "top": "(ow-iw)/2",
    "bottom": "(ow-iw)/2",
    "left": "0",
    "right": "ow-iw",
    "top_left": "0",
    "top_right": "ow-iw",
    "bottom_left": "0",
    "bottom_right": "ow-iw",
}
_PAD_OY: dict[str, str] = {
    "center": "(oh-ih)/2",
    "top": "0",
    "bottom": "oh-ih",
    "left": "(oh-ih)/2",
    "right": "(oh-ih)/2",
    "top_left": "0",
    "top_right": "0",
    "bottom_left": "oh-ih",
    "bottom_right": "oh-ih",
}

# Anchor Literal type (9 values; §7.4).
_AnchorLiteral = Literal[
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
]


class _RenderReframe(BaseModel):
    """Reader-side validation model for the reframe directive (architecture §7.4).

    No dependency on clipwright-reframe; field ranges mirror ReframeOptions
    (writer).  Reader must not be stricter than writer on range; even-number
    constraint is defence-in-depth (AC-03).  Unknown keys forbidden; inf/nan
    rejected (CWE-20).
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    target_w: Annotated[int, Field(ge=2, le=7680)]
    target_h: Annotated[int, Field(ge=2, le=7680)]
    mode: Literal["crop", "pad", "blur_pad"] = "pad"
    anchor: _AnchorLiteral = "center"
    pad_color: Annotated[str, Field(max_length=64)] = "black"

    @field_validator("target_w")
    @classmethod
    def target_w_must_be_even(cls, v: int) -> int:
        """Defence-in-depth: reject odd target_w (AC-03)."""
        if v % 2 != 0:
            raise ValueError(f"target_w must be an even number, got {v}")
        return v

    @field_validator("target_h")
    @classmethod
    def target_h_must_be_even(cls, v: int) -> int:
        """Defence-in-depth: reject odd target_h (AC-03)."""
        if v % 2 != 0:
            raise ValueError(f"target_h must be an even number, got {v}")
        return v

    @field_validator("pad_color")
    @classmethod
    def pad_color_must_be_safe(cls, v: str) -> str:
        """Reject unsafe pad_color values (AC-05 / CWE-78)."""
        return _rf_validate_pad_color(v)


def _validate_reframe(raw: dict[str, Any] | None) -> _RenderReframe | None:
    """Validate a reframe directive dict and return a _RenderReframe, or None.

    Only target_w / target_h / mode / anchor / pad_color are consumed.
    tool / version / kind are intentionally stripped (extra=forbid on
    _RenderReframe).  Returns None when raw is None (backward compatible).

    Reader-side defence-in-depth (AC-03 / AC-05 / CWE-78):
    - Even-dimension check raises INVALID_INPUT before Pydantic sees the value.
    - pad_color allowlist re-checked inside _RenderReframe field_validator.
    - All ValidationError / TypeError → INVALID_INPUT (CWE-209: no raw value
      echoed in error messages).
    """
    if raw is None:
        return None

    # Extract only the keys _RenderReframe accepts.
    filtered: dict[str, Any] = {
        k: raw[k]
        for k in ("target_w", "target_h", "mode", "anchor", "pad_color")
        if k in raw
    }

    # Reader-side even validation before Pydantic (AC-03 defence-in-depth).
    for dim in ("target_w", "target_h"):
        v = filtered.get(dim)
        if isinstance(v, int) and v % 2 != 0:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=f"Reframe directive {dim} must be even.",
                hint=f"Set {dim} to an even integer (2, 4, 6, ...).",
            )

    # Reader-side pad_color allowlist (AC-05 defence-in-depth).
    pc = filtered.get("pad_color")
    if pc is not None:
        try:
            _rf_validate_pad_color(pc)
        except ValueError:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Reframe directive pad_color failed allowlist validation.",
                hint=(
                    "Use a CSS color name (black, white, red, …)"
                    " or #RRGGBB / 0xRRGGBB hex."
                ),
            ) from None

    try:
        return _RenderReframe(**filtered)
    except (ValidationError, TypeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Reframe directive validation failed."
                " Check field names, types, and values."
            ),
            hint=(
                "Check reframe directive fields: even target_w/h,"
                " valid mode and anchor values."
            ),
        ) from None


# ===========================================================================
# Transition directive: reader-side validation model and helpers (ADR-RT-9)
# ===========================================================================

_TRANSITION_TYPE_ALLOWLIST: frozenset[str] = frozenset(
    {"fade", "dissolve", "fadeblack", "fadewhite"}
)

# Minimum xfade duration: 1 frame at 30 fps as a safe floor.
# Used when clamped_d would become <= 0 (degenerate case; ADR-RT-8).
_MIN_XFADE_DUR: float = 1.0 / 30.0


class _RenderTransition(BaseModel):
    """Reader-side validation model for one boundary transition directive entry.

    Validates after_clip_index / type / duration_sec with Pydantic; extra
    fields are forbidden (defence-in-depth; ADR-RT-9).
    Upper-bound check on after_clip_index (n_clips-2) is done in
    _validate_transition, not here (n_clips unknown at model level).
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    after_clip_index: Annotated[int, Field(ge=0)]
    type: Literal["fade", "dissolve", "fadeblack", "fadewhite"]
    duration_sec: Annotated[float, Field(gt=0, le=5.0)]


def _validate_transition(
    raw: dict[str, Any] | None,
    n_clips: int,
) -> list[_RenderTransition] | None:
    """Validate a transition directive dict and return a list of _RenderTransition.

    Returns None when raw is None (backward compatible).

    Reader-side defence-in-depth (ADR-RT-9):
    - Validates each boundary entry with _RenderTransition (extra=forbid,
      type allowlist, duration_sec gt=0 le=5.0).
    - Checks after_clip_index in [0, n_clips-2], ascending order, no
      duplicates, and that the index set covers all internal boundaries
      (gaps → UNSUPPORTED_OPERATION; ADR-RT-5).
    - n_clips < 2 with a non-empty directive → INVALID_INPUT.
    - ValidationError / TypeError / KeyError → INVALID_INPUT (CWE-209: raw
      values are never echoed in error messages).
    """
    if raw is None:
        return None

    # Extract the transitions list (may raise KeyError / TypeError → caught below)
    try:
        raw_transitions = raw.get("transitions", [])
        if not raw_transitions:
            # Empty list → treat as no transition (backward compat)
            return None
    except (AttributeError, TypeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Transition directive validation failed. Malformed directive.",
            hint=(
                "Ensure the transition directive is a dict with a 'transitions'"
                " list field."
            ),
        ) from None

    # n_clips < 2 with a transition directive → INVALID_INPUT
    if n_clips < 2:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "A transition directive is present but the timeline has"
                " fewer than two clips."
            ),
            hint=(
                "Build a multi-clip timeline with clipwright-sequence or"
                " clipwright-trim first, then apply transitions."
            ),
        )

    # Validate each entry with _RenderTransition
    validated: list[_RenderTransition] = []
    try:
        for entry in raw_transitions:
            validated.append(_RenderTransition(**entry))
    except (ValidationError, TypeError, KeyError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Transition directive validation failed."
                " Check field names, types, and values."
            ),
            hint=(
                "Each transition entry must have after_clip_index (int >= 0),"
                " type in {fade, dissolve, fadeblack, fadewhite},"
                " and 0 < duration_sec <= 5.0."
            ),
        ) from None

    max_boundary = n_clips - 2  # inclusive upper bound

    # Check for out-of-range indices
    for t in validated:
        if t.after_clip_index > max_boundary:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="A boundary index is out of range.",
                hint=f"Use after_clip_index in [0, {max_boundary}].",
            )

    # Check for duplicate indices
    seen: set[int] = set()
    for t in validated:
        if t.after_clip_index in seen:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Duplicate boundary index in the transition directive.",
                hint="Specify each boundary at most once.",
            )
        seen.add(t.after_clip_index)

    # Sort by after_clip_index (ascending normal form)
    validated.sort(key=lambda t: t.after_clip_index)

    # Check for gaps: index set must equal {0, 1, ..., n_clips-2}
    expected = set(range(n_clips - 1))
    if seen != expected:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=(
                "Transition directive does not cover all internal clip boundaries."
            ),
            hint=(
                "Apply transitions to all internal boundaries (uniform);"
                " partial per-boundary is unsupported in v1."
                " Use uniform mode or specify all boundaries."
            ),
        )

    return validated


def _segment_program_durations(ranges: list[KeptRange]) -> list[float]:
    """Return the program duration (seconds) for each segment in ranges.

    program_dur = source_dur / time_scalar  (identity → source_dur).
    Mirrors the formula used in build_plan total_duration calculation
    (lines ~3603-3608) and build_program_time_map (ADR-RT-4).
    """
    result: list[float] = []
    for r in ranges:
        src_dur = _to_seconds(r.source_range.duration)
        if not _is_warp_identity(r.time_scalar):
            result.append(src_dur / r.time_scalar)
        else:
            result.append(src_dur)
    return result


def _build_transition_chain(
    filter_parts: list[str],
    video_labels: list[str],
    audio_labels: list[str],
    has_audio: bool,
    program_durations: list[float],
    transitions: list[_RenderTransition] | None,
) -> tuple[str, str, list[str], float]:
    """Append either a concat filter or an xfade/acrossfade chain to filter_parts.

    Returns (video_terminal_label, audio_terminal_label, warnings, sum_clamped_d).

    sum_clamped_d is the total overlap removed from the program timeline by
    transitions (Σ clamped_d). Callers use this to correct total_duration and
    total_duration_for_bgm without re-deriving the clamping logic (CR M-3 / DRY).

    When transitions is None or empty, the traditional concat filter is generated
    (output byte-identical to the previous implementation; ADR-RT-1).
    sum_clamped_d is 0.0 in that case.

    When transitions is non-empty (must cover all internal boundaries in ascending
    order), xfade filters are chained for video and, when has_audio=True,
    acrossfade filters are chained for audio.  Terminal labels are always
    '[outv]' (video) and '[outa]' (audio, has_audio only) so that downstream
    stages (audio pipe, scale, subtitle, drawtext, overlay, BGM) require no
    changes (ADR-RT-1).

    Intermediate video labels use the '[xf{i}]' prefix; intermediate audio labels
    use '[acf{i}]' where i is the after_clip_index of the boundary.

    Duration clamping (ADR-RT-8):
        clamped_d = min(requested_d, program_dur_i, program_dur_{i+1})
        When clamped_d <= 0 (degenerate), it is floored to _MIN_XFADE_DUR.
        A warning is appended for each clamped boundary (fixed wording + boundary
        identifier; SR M-1 — raw float values are not included in warnings).

    Offset formula (ADR-RT-3):
        offset = prog_cum − overlap_cum − clamped_d
        where prog_cum accumulates program_dur up to and including clip i,
        and overlap_cum accumulates previously applied clamped_d values.
    """
    n = len(video_labels)
    tr_warnings: list[str] = []

    # --- Backward-compat path: no transitions ---
    if not transitions:
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
        return "[outv]", "[outa]" if has_audio else "", tr_warnings, 0.0

    # --- Transition path: xfade/acrossfade chain ---
    # Video chain
    prog_cum: float = 0.0
    overlap_cum: float = 0.0
    prev_v = video_labels[0]

    # Audio chain
    prev_a = audio_labels[0] if has_audio else ""

    # transitions is already sorted ascending by after_clip_index (_validate_transition)
    for idx, tr in enumerate(transitions):
        i = tr.after_clip_index
        # Accumulate program duration up to and including clip i
        prog_cum += program_durations[i]
        prog_dur_next = program_durations[i + 1]

        # Duration clamping (ADR-RT-8).
        # SR M-1: warning text uses fixed wording and boundary identifier only;
        # raw float values (requested_d / clamped_d) are intentionally excluded.
        requested_d = tr.duration_sec
        clamped_d = min(requested_d, program_durations[i], prog_dur_next)
        if clamped_d <= 0.0:
            clamped_d = _MIN_XFADE_DUR
            tr_warnings.append(
                "A transition was clamped because an adjacent clip has zero"
                " program duration. [boundary " + str(i) + "]"
            )
        elif clamped_d < requested_d:
            tr_warnings.append(
                "A transition was clamped because an adjacent clip is shorter"
                " than the requested duration. [boundary " + str(i) + "]"
            )

        offset = prog_cum - overlap_cum - clamped_d
        overlap_cum += clamped_d

        next_v = video_labels[i + 1]
        # Last boundary: output label is [outv]; intermediate: [xf{i}]
        is_last = idx == len(transitions) - 1
        out_v = "[outv]" if is_last else f"[xf{i}]"
        filter_parts.append(
            f"{prev_v}{next_v}"
            f"xfade=transition={tr.type}:duration={clamped_d:g}:offset={offset:.6f}"
            f"{out_v}"
        )
        prev_v = out_v

        if has_audio:
            next_a = audio_labels[i + 1]
            out_a = "[outa]" if is_last else f"[acf{i}]"
            filter_parts.append(f"{prev_a}{next_a}acrossfade=d={clamped_d:g}{out_a}")
            prev_a = out_a

    return "[outv]", "[outa]" if has_audio else "", tr_warnings, overlap_cum


def _append_reframe_filter(
    filter_parts: list[str],
    video_map_label: str,
    reframe: _RenderReframe,
) -> str:
    """Append reframe filter segment(s) to filter_parts and return the terminal label.

    Architecture §3.3/§3.4/§6.  Each appended element must not contain ';'
    (individual-segment rule §3.1).  The terminal label is always '[outvrf]'.
    Intermediate labels use the 'reframe_' prefix (§3.2 non-collision).

    Modes:
    - crop (§3.4 / §2.2): 1 segment — scale increase, crop with anchor offset,
      setsar=1.
    - pad  (§3.4 / §2.3): 1 segment — scale decrease, pad with anchor offset and
      color, setsar=1.
    - blur_pad (§3.3 / FR-3.3): 4 segments — split, background (scale+crop+blur),
      foreground (scale), overlay+setsar.  Anchor is ignored for the background
      crop (center-fixed; AC-15).

    Caller contract:
        - ``video_map_label`` must be the terminal label of the video chain
          *after* concat/audio-pipe stages and *before* the eq filter.
          Typical value: ``"[outv]"``.
        - This function consumes ``video_map_label`` and emits ``[outvrf]``.
        - Insertion order (D4): reframe → eq → subtitle → drawtext.
          Callers must pass the output of this function as the input label
          to the subsequent eq/subtitle/drawtext stages.

    Args:
        filter_parts: list of filter_complex segments (mutated in place).
        video_map_label: terminal label of the preceding video chain.
        reframe: validated _RenderReframe instance.

    Returns:
        '[outvrf]'
    """
    w = reframe.target_w
    h = reframe.target_h
    anchor = reframe.anchor

    if reframe.mode == "crop":
        # Resolve anchor offset expressions (§2.2); substitute literal integers.
        ox_expr = _CROP_OX[anchor].format(W=w, H=h)
        oy_expr = _CROP_OY[anchor].format(W=w, H=h)
        # Clamp to valid crop origin range (defence-in-depth §2.1).
        # Commas inside min/max are escaped as \, (§2.1 ffmpeg filtergraph rule).
        ox = rf"min(max({ox_expr}\,0)\,iw-{w})"
        oy = rf"min(max({oy_expr}\,0)\,ih-{h})"
        seg = (
            f"{video_map_label}"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:{ox}:{oy},"
            f"setsar=1"
            f"[outvrf]"
        )
        filter_parts.append(seg)

    elif reframe.mode == "pad":
        # Resolve anchor offset expressions (§2.3); output coordinates ow/oh.
        ox_expr = _PAD_OX[anchor]
        oy_expr = _PAD_OY[anchor]
        # Clamp (defence-in-depth §2.1); commas escaped as \,.
        ox = rf"min(max({ox_expr}\,0)\,ow-iw)"
        oy = rf"min(max({oy_expr}\,0)\,oh-ih)"
        color = reframe.pad_color
        seg = (
            f"{video_map_label}"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:{ox}:{oy}:color={color},"
            f"setsar=1"
            f"[outvrf]"
        )
        filter_parts.append(seg)

    else:  # blur_pad (§3.3 / FR-3.3)
        # Segment 0: split into background and foreground streams.
        filter_parts.append(f"{video_map_label}split=2[reframe_bg][reframe_fg]")
        # Segment 1: background — scale to cover, center-crop (AC-15: anchor ignored),
        # blur.
        filter_parts.append(
            f"[reframe_bg]"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"boxblur=20:2"
            f"[reframe_bgb]"
        )
        # Segment 2: foreground — scale to fit (decrease).
        filter_parts.append(
            f"[reframe_fg]"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease"
            f"[reframe_fgs]"
        )
        # Segment 3: overlay foreground centered on blurred background, fix SAR.
        filter_parts.append(
            f"[reframe_bgb][reframe_fgs]overlay=({w}-w)/2:({h}-h)/2,setsar=1[outvrf]"
        )

    return "[outvrf]"


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


def _build_atempo_chain(speed: float) -> str:
    """Build an ffmpeg atempo filter chain string for the given playback speed.

    ffmpeg's atempo filter accepts values in [0.5, 2.0] only. For speeds outside
    that range, multiple stages are chained so their product equals speed (ADR-SP-3).

    Stage values are formatted with :g (no trailing zeros).

    Precondition: speed must be finite > 0 (callers pre-validate; values outside
    0.25–8.0 are rejected upstream in resolve_kept_ranges). speed=1.0 is valid
    and returns a single "atempo=1" stage, but callers typically skip this function
    for identity speed (_is_warp_identity).

    Examples:
        speed=2.0  -> "atempo=2"
        speed=4.0  -> "atempo=2,atempo=2"
        speed=0.5  -> "atempo=0.5"
        speed=0.25 -> "atempo=0.5,atempo=0.5"
        speed=3.0  -> "atempo=2,atempo=1.5"
        speed=0.3  -> "atempo=0.5,atempo=0.6"

    Args:
        speed: playback speed multiplier. Must be finite and > 0.

    Returns:
        Comma-separated atempo filter chain string.

    Raises:
        ValueError: when speed is not finite or not > 0 (defence-in-depth;
            CR L-2 / SR H-1(b)).
    """
    # Defence-in-depth guard (CR L-2 / SR H-1(b)): prevent infinite loops from
    # zero, negative, inf, or nan inputs. resolve_kept_ranges rejects these
    # upstream, so this branch should never be reached in normal usage.
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(
            f"_build_atempo_chain requires a finite speed > 0, got {speed!r}"
        )

    stages: list[float] = []
    remaining = speed

    if speed >= 1.0:
        # For speed > 2.0: emit atempo=2.0 stages until remainder <= 2.0,
        # then emit a final atempo=remainder stage.
        while remaining > 2.0:
            stages.append(2.0)
            remaining /= 2.0
        stages.append(remaining)
    else:
        # For speed < 0.5: emit atempo=0.5 stages until remainder >= 0.5,
        # then emit a final atempo=remainder stage.
        while remaining < 0.5:
            stages.append(0.5)
            remaining /= 0.5
        stages.append(remaining)

    return ",".join(f"atempo={s:g}" for s in stages)


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
    text_overlays: list[TextOverlay] | None = None,
    color_eq: _RenderEqParams | None = None,
    stabilize_basename: str | None = None,
    stabilize_smoothing: int = _DEFAULT_STABILIZE_SMOOTHING,
    reframe: _RenderReframe | None = None,
    image_overlays: list[ImageOverlay] | None = None,
    transitions: list[_RenderTransition] | None = None,
    program_durations: list[float] | None = None,
) -> tuple[str, str, str, bool, bool, list[str], float]:
    """Build the filter_complex string, video_map_label, and audio_map_label
    (M-2).

    Responsibility: constructs the filter_complex string for trim/atrim → concat
    → denoise afftdn → loudness → scale, and determines the terminal label for
    each chain. Single-source path only (maintains backward compatibility; ADR-C3).

    When width/height are both specified, the scale stage uses fit-based branching
    (ADR-F2) with even-rounding applied to W/H (ADR-F4). probe_info is used to
    determine frame_h for subtitle counter-scaling when width/height are not
    specified (ADR-F3 revised).

    text_overlays: when non-empty, _append_drawtext_filter is called after the
        subtitle stage to inject the drawtext filter chain (WP-2; ADR-T3).

    image_overlays: when non-empty, _append_overlay_filter is called immediately
        after drawtext to compose image overlays (topmost layer; ADR-OV-5).
        None or empty list is a no-op (backward compatible).

    transitions: list of validated transition directives (ascending by
        after_clip_index). When None, the traditional concat filter is used
        (backward compatible; ADR-RT-1). program_durations must be provided
        when transitions is non-None.

    program_durations: per-segment program duration in seconds (time_scalar
        applied). Required when transitions is non-None.

    Returns:
        (filter_complex, video_map_label, audio_map_label, use_afftdn,
        use_loudness, transition_warnings, sum_clamped_d)

        sum_clamped_d: total transition overlap in seconds (Σ clamped_d),
        propagated from _build_transition_chain so that build_plan can use the
        single authoritative value without re-deriving it (CR M-3 / DRY).
    """
    # Generate trim/atrim filter segments for each segment
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filter_parts: list[str] = []

    for i, r in enumerate(ranges):
        start = _to_seconds(r.source_range.start_time)
        end = round(start + _to_seconds(r.source_range.duration), 6)
        vl = f"v{i}"
        s = r.time_scalar
        # stabilize: vidstabtransform inserted trim-directly-after, setpts-before
        # (§6-F). basename is relative; cwd set by render.py (ADR-ST-1/P-2/P-3).
        # None → no insertion (backward compatible).
        vst = (
            f"vidstabtransform=input={stabilize_basename}:smoothing={stabilize_smoothing}"
            if stabilize_basename is not None
            else None
        )
        if not _is_warp_identity(s):
            # Warp: setpts=(PTS-STARTPTS)/{s} (ADR-SP-6) to change video speed.
            if vst is not None:
                filter_parts.append(
                    f"[0:v]trim=start={start}:end={end},{vst},setpts=(PTS-STARTPTS)/{s:g}[{vl}]"
                )
            else:
                filter_parts.append(
                    f"[0:v]trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{s:g}[{vl}]"
                )
        else:
            if vst is not None:
                filter_parts.append(
                    f"[0:v]trim=start={start}:end={end},{vst},setpts=PTS-STARTPTS[{vl}]"
                )
            else:
                filter_parts.append(
                    f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[{vl}]"
                )
        video_labels.append(f"[{vl}]")

        if has_audio:
            al = f"a{i}"
            if not _is_warp_identity(s):
                # Warp: apply atempo chain after asetpts (ADR-SP-3).
                atempo = _build_atempo_chain(s)
                filter_parts.append(
                    f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,{atempo}[{al}]"
                )
            else:
                filter_parts.append(
                    f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{al}]"
                )
            audio_labels.append(f"[{al}]")

    # concat or xfade/acrossfade chain (ADR-RT-1)
    _v_term, _a_term, transition_warnings, _sum_clamped_d = _build_transition_chain(
        filter_parts,
        video_labels,
        audio_labels,
        has_audio,
        program_durations
        if program_durations is not None
        else _segment_program_durations(ranges),
        transitions,
    )
    # _v_term is always "[outv]", _a_term is "[outa]" or "" — terminal labels
    # are fixed so downstream stages require no changes (ADR-RT-1).

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
    # D5: reframe present suppresses the regular scale stage; reframe uses its
    # own internal scale (architecture §8).
    use_scale = (
        options.width is not None and options.height is not None
    ) and reframe is None
    frame_h: int | None = None
    if reframe is not None:
        # Reframe path: insert reframe segments after concat (§4.2).
        # frame_h = target_h for subtitle counter-scaling (AC-16).
        frame_h = reframe.target_h
        video_map_label = _append_reframe_filter(filter_parts, "[outv]", reframe)
    elif use_scale:
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

    # Inject eq color-correction stage after scale/reframe, before subtitle
    # (ADR-CO-4: geometry normalise → colour correct → overlay burn-in).
    # When color_eq is None, this is a no-op (backward compatible).
    video_map_label = _append_eq_filter(filter_parts, video_map_label, color_eq)

    # Inject subtitle stage after video_map_label is finalised (ADR-S4-r3).
    # When subtitle=None, nothing is done (backward compatible; ADR-S8).
    if options.subtitle is not None:
        video_map_label = _append_subtitle_filter(
            filter_parts, video_map_label, options.subtitle, frame_h
        )

    # Inject drawtext stage after subtitle (ADR-T3; WP-2).
    # When text_overlays is empty/None, _append_drawtext_filter is a no-op
    # (backward compatible; FR-6-6).
    if text_overlays:
        video_map_label = _append_drawtext_filter(
            filter_parts, video_map_label, text_overlays
        )

    # Inject image overlay stage after drawtext (topmost layer; ADR-OV-5).
    # None or empty list is a no-op (backward compatible).
    if image_overlays:
        video_map_label = _append_overlay_filter(
            filter_parts, video_map_label, image_overlays
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

    return (
        filter_complex,
        video_map_label,
        audio_map_label,
        use_afftdn,
        use_loudness,
        transition_warnings,
        _sum_clamped_d,
    )


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
        s = r.time_scalar
        vl = f"v{i}"
        # Per-clip video: trim → setpts[warp] → fps → scale/pad/crop/setsar (fit-based).
        # fps written with at least 5 decimal places (ADR-C2-r2; NTSC fps precision)
        # Warp: setpts=(PTS-STARTPTS)/{s} when not identity (ADR-SP-5/SP-6).
        # fps stays downstream of setpts (per fixed decision in architecture).
        setpts_expr = (
            f"(PTS-STARTPTS)/{s:g}" if not _is_warp_identity(s) else "PTS-STARTPTS"
        )
        base = (
            f"[{k}:v]trim=start={start}:end={end},setpts={setpts_expr},"
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
                # Audio present: atrim → asetpts → [atempo warp] → aformat.
                if not _is_warp_identity(s):
                    atempo = _build_atempo_chain(s)
                    filter_parts.append(
                        f"[{k}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                        f"{atempo},"
                        f"aformat=sample_rates=48000:channel_layouts=stereo[{al}]"
                    )
                else:
                    filter_parts.append(
                        f"[{k}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                        f"aformat=sample_rates=48000:channel_layouts=stereo[{al}]"
                    )
            else:
                # No audio: pad with anullsrc. When warped, use warped duration
                # so the silent pad matches the video duration (OQ-2).
                pad_dur = dur / s if not _is_warp_identity(s) else dur
                if not _is_warp_identity(s):
                    atempo = _build_atempo_chain(s)
                    filter_parts.append(
                        f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                        f"atrim=0:{pad_dur:g},asetpts=PTS-STARTPTS,{atempo}[{al}]"
                    )
                else:
                    filter_parts.append(
                        f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                        f"atrim=0:{pad_dur:g},asetpts=PTS-STARTPTS[{al}]"
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
    text_overlays: list[TextOverlay] | None = None,
    color_eq: _RenderEqParams | None = None,
    image_overlays: list[ImageOverlay] | None = None,
    transitions: list[_RenderTransition] | None = None,
    program_durations: list[float] | None = None,
) -> tuple[str, str, str, bool, bool, list[str], float]:
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

    text_overlays: when non-empty, _append_drawtext_filter is called after the
        subtitle stage (WP-2; ADR-T3).

    image_overlays: when non-empty, _append_overlay_filter is called immediately
        after drawtext to compose image overlays (topmost layer; ADR-OV-5).
        None or empty list is a no-op (backward compatible).

    transitions: list of validated transition directives (ascending). When None,
        the traditional concat filter is used (ADR-RT-1).

    program_durations: per-segment program duration in seconds. Required when
        transitions is non-None.

    Returns:
        (filter_complex, video_map_label, audio_map_label, use_afftdn,
        use_loudness, transition_warnings, sum_clamped_d)

        sum_clamped_d: total transition overlap in seconds (Σ clamped_d),
        propagated from _build_transition_chain so that build_plan can use the
        single authoritative value without re-deriving it (CR M-3 / DRY).
    """
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

    # concat or xfade/acrossfade chain (ADR-RT-1)
    _v_term, _a_term, transition_warnings, _sum_clamped_d = _build_transition_chain(
        filter_parts,
        video_labels,
        audio_labels,
        has_audio_overall,
        program_durations
        if program_durations is not None
        else _segment_program_durations(ranges),
        transitions,
    )

    # Cumulative audio pipe for denoise/loudness (shared single/multi-source
    # helper; ADR-C11-r2)
    use_afftdn, use_loudness = _append_audio_pipe(
        filter_parts, has_audio_overall, denoise_directive, loudness_directive
    )

    # In the multi-source path, per-clip spec normalisation is already done up
    # front, so no post-concat scale is applied (ADR-C5-r2).
    video_map_label = "[outv]"

    # Inject eq color-correction stage after concat/normalisation, before subtitle
    # (ADR-CO-4). When color_eq is None, this is a no-op (backward compatible).
    video_map_label = _append_eq_filter(filter_parts, video_map_label, color_eq)

    # Inject subtitle stage after video_map_label is finalised (ADR-S4-r3).
    # When subtitle=None, nothing is done (backward compatible; ADR-S8).
    # frame_h = target_h (subtitle stage follows per-clip normalisation to target
    # size; ADR-F3 revised §5.3).
    if options.subtitle is not None:
        video_map_label = _append_subtitle_filter(
            filter_parts, video_map_label, options.subtitle, target_h
        )

    # Inject drawtext stage after subtitle (ADR-T3; WP-2).
    # When text_overlays is empty/None, _append_drawtext_filter is a no-op.
    if text_overlays:
        video_map_label = _append_drawtext_filter(
            filter_parts, video_map_label, text_overlays
        )

    # Inject image overlay stage after drawtext (topmost layer; ADR-OV-5).
    # None or empty list is a no-op (backward compatible).
    if image_overlays:
        video_map_label = _append_overlay_filter(
            filter_parts, video_map_label, image_overlays
        )

    filter_complex = ";".join(filter_parts)

    # Determine the audio map terminal label via cumulative pipe
    if use_loudness:
        audio_map_label = "[outa_ln]"
    elif use_afftdn:
        audio_map_label = "[outa_dn]"
    else:
        audio_map_label = "[outa]"

    return (
        filter_complex,
        video_map_label,
        audio_map_label,
        use_afftdn,
        use_loudness,
        transition_warnings,
        _sum_clamped_d,
    )


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
    resolved_encoder: ResolvedEncoder | None = None,
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

    When resolved_encoder is not None, the HW encoder path is used: -c:v is set
    to resolved_encoder.encoder_name and rate_control_flags are expanded verbatim.
    The legacy -c:v video_codec / -crf crf block is bypassed (AC-1/ADR-7).
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
    if resolved_encoder is None:
        # Existing (software) path — preserved byte-for-byte (AC-1/NFR-1).
        if options.video_codec is not None:
            ffmpeg_args += ["-c:v", options.video_codec]
        if options.audio_codec is not None:
            ffmpeg_args += ["-c:a", options.audio_codec]
        # width/height are integrated into filter_complex; -vf is not added (L-4).
        if options.fps is not None:
            if use_multi_source:
                # fps already normalised by per-clip filter; -r skipped (CR M-2).
                pass
            else:
                # Single-source path: add -r as before (backward compatible; ADR-C3).
                ffmpeg_args += ["-r", str(options.fps)]
        if options.crf is not None:
            ffmpeg_args += ["-crf", str(options.crf)]
    else:
        # HW encoder path (ADR-7): use resolved encoder name and rate-control flags.
        # -crf is never emitted here (AC-3); rate_control_flags carries the HW
        # equivalent (e.g. -cq/-rc for nvenc).
        ffmpeg_args += ["-c:v", resolved_encoder.encoder_name]
        ffmpeg_args += resolved_encoder.rate_control_flags
        if options.audio_codec is not None:
            ffmpeg_args += ["-c:a", options.audio_codec]
        if options.fps is not None:
            if use_multi_source:
                pass
            else:
                ffmpeg_args += ["-r", str(options.fps)]

    return ffmpeg_args


def build_plan(
    ranges: list[KeptRange],
    probe_info: ProbeInfo,
    options: RenderOptions,
    denoise: dict[str, Any] | None = None,
    loudness: dict[str, Any] | None = None,
    color: dict[str, Any] | None = None,
    stabilize: dict[str, Any] | None = None,
    source_probes: dict[str, ProbeInfo] | None = None,
    bgm: BgmClip | None = None,
    text_overlays: list[TextOverlay] | None = None,
    resolved_encoder: ResolvedEncoder | None = None,
    reframe: dict[str, Any] | None = None,
    transition: dict[str, Any] | None = None,
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
    - text_overlays: when None and ranges is a KeptRangeList with a _timeline,
      text_overlay markers are read from the timeline automatically.  When
      explicitly set to [] (empty list), no overlays are applied (backward
      compatible; FR-6-6).  When non-empty, drawtext is appended after subtitle
      (ADR-T3; WP-2).
    - image_overlays: auto-collected from timeline markers (kind=="image_overlay")
      when ranges is a KeptRangeList with _timeline. image_index_base =
      len(input_sources) + (1 if bgm else 0) (ADR-OV-5/G4). Reconstructed
      absolute image paths are stored in RenderPlan.image_sources.
      None/empty → no-op (backward compatible; output byte-identical).
    """
    # Resolve text_overlays: prefer the explicit argument; fall back to reading
    # markers from the attached timeline (KeptRangeList._timeline).
    # When text_overlays=[] is passed explicitly (backward-compat test), that
    # empty list is used directly (no marker lookup).
    if text_overlays is None:
        tl_ref: otio.schema.Timeline | None = getattr(ranges, "_timeline", None)
        text_overlays = _collect_text_overlays(tl_ref) if tl_ref is not None else []

    # Re-time text_overlays from source time to program time (§2.1 / ADR-1).
    # Build the source→program map from kept ranges, then apply retime_text_overlays.
    # retime is True only when retime_markers=="auto" AND (has_cut OR has_warp) AND
    # single source (multi-source skip warning is emitted in render.py — ADR-4).
    _retime_overlay_warnings: list[str] = []
    if text_overlays:
        import clipwright_render.retiming as _retiming_mod

        _tmap = _retiming_mod.build_program_time_map(ranges)
        _input_sources_pre = unique_sources_in_order(ranges)
        _is_single_source = len(_input_sources_pre) <= 1
        _do_retime = (
            options.retime_markers == "auto"
            and (_tmap.has_cut or _tmap.has_warp)
            and _is_single_source
        )
        text_overlays, _retime_overlay_warnings = retime_text_overlays(
            text_overlays, _tmap, _do_retime
        )

    # Collect image_overlays from the attached timeline (ADR-OV-5).
    # image_index_base is computed after input_sources is known (after bgm check),
    # but we need to know bgm presence before we have input_sources.  Use a sentinel
    # list here and compute the base index below after input_sources is determined.
    # _image_overlays_raw: collected with a temporary base=0; input_index will be
    # corrected below once image_index_base is known.
    _tl_ref_img: otio.schema.Timeline | None = getattr(ranges, "_timeline", None)
    # timeline_path for V2-3 relative image_path reconstruction (V2-3 round-trip).
    # Obtained from KeptRangeList._timeline_path when set by render.py.
    _tl_path_img: str | None = getattr(ranges, "_timeline_path", None)
    # Collect raw overlays now (validates all fields except input_index); we will
    # reconstruct with the correct base index below.
    _image_overlays_raw: list[ImageOverlay] = (
        _collect_image_overlays(
            _tl_ref_img, image_index_base=0, timeline_path=_tl_path_img
        )
        if _tl_ref_img is not None
        else []
    )

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

    # Validate the color eq directive (raises INVALID_INPUT on failure)
    # Only color["eq"] is consumed; tool/version/measured are ignored (ADR-CO-3).
    color_eq: _RenderEqParams | None = None
    if color is not None:
        color_eq = _validate_color_eq(color)

    # Validate the stabilize directive (raises INVALID_INPUT on failure)
    # Only trf_path / smoothing are consumed; severity/shakiness/accuracy etc. are
    # ignored (ADR-ST-5). Returns None when trf_path is absent/None (backward compat).
    stabilize_directive: _RenderStabilize | None = None
    if stabilize is not None:
        stabilize_directive = _validate_stabilize(stabilize)

    # Validate the reframe directive (raises INVALID_INPUT on failure).
    # Must run before multi-source check so that invalid directive wins (§5.2).
    reframe_directive: _RenderReframe | None = None
    if reframe is not None:
        reframe_directive = _validate_reframe(reframe)

    # Unique source list (single source of truth for ADR-C9-r2)
    input_sources = unique_sources_in_order(ranges)
    n = len(ranges)

    # Validate the transition directive (raises INVALID_INPUT /
    # UNSUPPORTED_OPERATION on failure; ADR-RT-9).
    # n_clips = n (number of kept ranges / segments).
    transition_directive: list[_RenderTransition] | None = _validate_transition(
        transition, n
    )

    # Pre-compute per-segment program durations for transition chain (ADR-RT-4).
    # Computed once here; passed to both filter-complex builders.
    _program_durations: list[float] = _segment_program_durations(ranges)

    # Resolve image_overlays with correct input_index (ADR-OV-5/G4).
    # image_index_base = len(input_sources) + (1 if bgm else 0).
    # Re-collect from the timeline with the correct base so that input_index values
    # in each ImageOverlay match the actual ffmpeg -i order.
    _image_index_base = len(input_sources) + (1 if bgm is not None else 0)
    if _tl_ref_img is not None and _image_overlays_raw:
        _image_overlays = _collect_image_overlays(
            _tl_ref_img,
            image_index_base=_image_index_base,
            timeline_path=_tl_path_img,
        )
    else:
        _image_overlays = []

    # Branch on source count (ADR-C3)
    use_multi_source = source_probes is not None and len(input_sources) >= 2

    # multi-source + stabilize → UNSUPPORTED_OPERATION (ADR-ST-2)
    if use_multi_source and stabilize_directive is not None:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="Stabilization is not supported for multi-source timelines.",
            hint=(
                "Use a single-source timeline for stabilization, "
                "or remove the stabilize directive."
            ),
        )

    # multi-source + reframe → UNSUPPORTED_OPERATION (§5.2/D6/AC-12)
    if use_multi_source and reframe_directive is not None:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="Reframing is not supported for multi-source timelines.",
            hint=(
                "v1 supports single-source only."
                " Trim/render to a single source first, then apply reframe."
            ),
        )

    # stabilize_cwd: initialized for both branches. Set in single-source branch;
    # remains None for multi-source (blocked above; ADR-ST-2).
    stabilize_cwd: str | None = None

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

        (
            filter_complex,
            video_map_label,
            audio_map_label,
            use_afftdn,
            use_loudness,
            _transition_warnings_multi,
            _sum_clamped_d,
        ) = _build_multi_source_filter_complex(
            ranges,
            source_index,
            source_probes,
            has_audio_overall,
            denoise_directive,
            loudness_directive,
            options,
            first_source,
            text_overlays=text_overlays,
            color_eq=color_eq,
            image_overlays=_image_overlays,
            transitions=transition_directive,
            program_durations=_program_durations,
        )
        _transition_warnings = _transition_warnings_multi

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

        # Compute stabilize basename and cwd for vidstabtransform injection (§6-G).
        # basename is relative (cwd+relative; P-2/P-3). cwd is the trf parent dir
        # passed to render.py's run(..., cwd=plan.stabilize_cwd).
        stabilize_basename: str | None = None
        if stabilize_directive is not None:
            stabilize_basename = Path(stabilize_directive.trf_path).name
            # Reject basenames with filtergraph special chars (CWE-78 / SR-INJ-002).
            # cwd+relative basename method (ADR-ST-1) is preserved; escaping is not
            # used because _escape_filtergraph (\:) does not work with vid.stab input=.
            _validate_stabilize_basename(stabilize_basename)
            stabilize_cwd = str(Path(stabilize_directive.trf_path).resolve().parent)

        (
            filter_complex,
            video_map_label,
            audio_map_label,
            use_afftdn,
            use_loudness,
            _transition_warnings_single,
            _sum_clamped_d,
        ) = _build_filter_complex(
            ranges,
            has_audio,
            denoise_directive,
            loudness_directive,
            options,
            probe_info,
            text_overlays=text_overlays,
            color_eq=color_eq,
            stabilize_basename=stabilize_basename,
            stabilize_smoothing=(
                stabilize_directive.smoothing
                if stabilize_directive is not None
                else _DEFAULT_STABILIZE_SMOOTHING
            ),
            reframe=reframe_directive,
            image_overlays=_image_overlays,
            transitions=transition_directive,
            program_durations=_program_durations,
        )
        _transition_warnings = _transition_warnings_single

    # ---------- Append BGM stage (ADR-B5-r2/B5-r3) ----------
    # has_main_audio: main audio presence after concat (equivalent to existing
    # has_audio). has_audio_output: final output audio presence (has_main_audio
    # or BGM present)
    has_main_audio = has_audio
    bgm_source_out: str | None = None

    # _sum_clamped_d is now the authoritative Σ clamped_d returned from
    # _build_transition_chain via _build_filter_complex /
    # _build_multi_source_filter_complex (CR M-3 / DRY). The previous
    # re-derivation loop has been removed; both total_duration and
    # total_duration_for_bgm now use this single value.

    if bgm is not None:
        # BGM index = len(input_sources) (bgm_source not included in
        # input_sources; DC-AS-005)
        bgm_index = len(input_sources)
        # BGM duration target must match the warped output duration (§6).
        # SR NL-2: use _is_warp_identity for identity detection (consistent with
        # filter_complex side; guards against OTIO round-trip float drift).
        total_duration_for_bgm = sum(_program_durations) - _sum_clamped_d

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
        resolved_encoder=resolved_encoder,
    )

    # ---------- Dry-run estimate ----------
    # Sum warped durations minus transition overlaps (ADR-RT-3).
    # render.py derives ffmpeg timeout from total_duration_seconds, so the
    # corrected (post-transition) duration is functionally required.
    total_duration = sum(_program_durations) - _sum_clamped_d

    estimated_size: float | None = None
    warnings: list[str] = []

    # Merge transition clamping warnings (ADR-RT-8).
    warnings.extend(_transition_warnings)

    # Merge re-timing warnings from text_overlay adapter (ADR-4 / §5).
    warnings.extend(_retime_overlay_warnings)

    # reframe + width/height → scale suppressed; warn that resolution options are
    # ignored (AC-11 / §8). Output resolution is fixed by reframe.target_w/h.
    if (
        reframe_directive is not None
        and options.width is not None
        and options.height is not None
    ):
        w_out = reframe_directive.target_w
        h_out = reframe_directive.target_h
        warnings.append(
            f"width/height ignored; output fixed to {w_out}x{h_out} by the reframe"
            " directive."
        )

    # reframe mode=crop → content outside target aspect ratio is lost (AC-13 / §8).
    if reframe_directive is not None and reframe_directive.mode == "crop":
        warnings.append(
            "content outside the target aspect ratio is cropped and discarded."
        )

    # retime interference warning (ADR-RT-6): transition + overlay markers present
    # → overlay timings will drift because the program timeline shortens by Σd.
    # Both single-source and multi-source paths emit this warning.
    if transition_directive and (text_overlays or _image_overlays):
        warnings.append(
            f"Transition overlaps shorten the program timeline by"
            f" {_sum_clamped_d:.3f}s;"
            " text/image overlay timings are NOT adjusted for transitions"
            " and may drift near transition boundaries (v1 limitation)."
        )

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
        stabilize_cwd=stabilize_cwd,
        image_sources=[o.image_path for o in _image_overlays],
    )
