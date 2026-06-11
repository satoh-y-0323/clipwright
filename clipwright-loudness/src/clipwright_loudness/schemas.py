"""schemas.py — clipwright-loudness Pydantic schemas.

Common types (MediaRef / Artifact / ToolResult, etc.) are defined centrally
in clipwright.schemas and are not redefined here.

DetectLoudnessOptions: Input options for clipwright_detect_loudness.
LoudnessDirective: Directive schema written to
    timeline-level metadata["clipwright"]["loudness"].
LoudnormTarget / PeakTarget: Normalization target values per mode.
LoudnormMeasured / PeakMeasured: Measured values per mode.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class DetectLoudnessOptions(BaseModel):
    """Options for clipwright_detect_loudness (design §3, ADR-L1/L2).

    mode: loudnorm (EBU R128 LUFS normalization) or peak (peak dB normalization).
    scope: track only (per_clip deferred per DC-AS-003).
    target_i/target_tp/target_lra: loudnorm target values (overridable).
    target_peak_db: peak target value (overridable).
    """

    mode: Annotated[
        Literal["loudnorm", "peak"],
        Field(
            default="loudnorm",
            description=(
                "Loudness normalization mode. "
                '"loudnorm" (default) applies EBU R128 LUFS normalization'
                " (ffmpeg loudnorm). "
                '"peak" applies peak dB normalization (ffmpeg volumedetect).'
            ),
        ),
    ] = "loudnorm"

    scope: Annotated[
        Literal["track"],
        Field(
            default="track",
            description=(
                "Processing scope. "
                '"track" (default) measures the entire timeline in a single pass. '
                "per_clip is deferred per DC-AS-003."
            ),
        ),
    ] = "track"

    # loudnorm target parameters (design: I=-14/TP=-1/LRA=11)
    target_i: Annotated[
        float,
        Field(
            default=-14.0,
            ge=-70.0,
            le=-5.0,
            description=(
                "loudnorm integrated loudness target (LUFS)."
                " Range [-70, -5]. Default -14."
            ),
        ),
    ] = -14.0

    target_tp: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-9.0,
            le=0.0,
            description=(
                "loudnorm true peak target (dBTP). Range [-9, 0]. Default -1."
            ),
        ),
    ] = -1.0

    target_lra: Annotated[
        float,
        Field(
            default=11.0,
            ge=1.0,
            le=50.0,
            description="loudnorm LRA target (LU). Range [1, 50]. Default 11.",
        ),
    ] = 11.0

    # peak target parameters
    target_peak_db: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-60.0,
            le=0.0,
            description="Peak mode peak target (dB). Range [-60, 0]. Default -1.",
        ),
    ] = -1.0


class LoudnormTarget(BaseModel):
    """Normalization target values for loudnorm mode (ADR-L4).

    Also independently redefined on the render side in plan.py
    (NR-M-1 lesson: no dependency on clipwright_loudness).
    """

    i: Annotated[
        float,
        Field(
            default=-14.0,
            ge=-70.0,
            le=-5.0,
            description="Integrated loudness target (LUFS). Range [-70, -5].",
        ),
    ] = -14.0

    tp: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-9.0,
            le=0.0,
            description="True peak target (dBTP). Range [-9, 0].",
        ),
    ] = -1.0

    lra: Annotated[
        float,
        Field(
            default=11.0,
            ge=1.0,
            le=50.0,
            description="LRA target (LU). Range [1, 50].",
        ),
    ] = 11.0


class PeakTarget(BaseModel):
    """Normalization target values for peak mode (ADR-L4)."""

    peak_db: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-60.0,
            le=0.0,
            allow_inf_nan=False,
            description="Peak target (dB). Range [-60, 0]. inf/nan not allowed.",
        ),
    ] = -1.0


class LoudnormMeasured(BaseModel):
    """Measured values output by the loudnorm filter (ADR-L1).

    Five values extracted from the JSON block at the end of ffmpeg
    loudnorm=print_format=json stderr.
    When the input is silent, "-inf" may be returned; in that case
    allow_inf_nan=False causes a ValidationError and the caller treats
    measured=None (U-1).
    """

    input_i: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="Input integrated loudness (LUFS). inf/nan not allowed.",
        ),
    ]

    input_tp: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="Input true peak (dBTP). inf/nan not allowed.",
        ),
    ]

    input_lra: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="Input LRA (LU). inf/nan not allowed.",
        ),
    ]

    input_thresh: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="Input threshold (LUFS). inf/nan not allowed.",
        ),
    ]

    target_offset: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="Target offset (LU). inf/nan not allowed.",
        ),
    ]


class PeakMeasured(BaseModel):
    """Measured values output by the volumedetect filter (ADR-L2).

    Value extracted from "max_volume: -X.X dB" in ffmpeg volumedetect stderr.
    """

    max_volume_db: Annotated[
        float,
        Field(
            ge=-200.0,
            le=0.0,
            allow_inf_nan=False,
            description="Maximum volume (dB). Range [-200, 0]. inf/nan not allowed.",
        ),
    ]


class LoudnessDirective(BaseModel):
    """Loudness directive schema written to timeline-level metadata
    (design §3.2, ADR-L4).

    Generated by loudness and read/validated by render.
    scope is track only (per_clip deferred per DC-AS-003).
    target is discriminated by mode (LoudnormTarget or PeakTarget).
    measured holds mode-specific measured values or None (U-1: when measurement fails).
    """

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["loudness"]
    mode: Literal["loudnorm", "peak"]
    scope: Literal["track"]
    target: LoudnormTarget | PeakTarget
    measured: LoudnormMeasured | PeakMeasured | None = None

    @model_validator(mode="after")
    def _validate_target_mode_consistency(self) -> LoudnessDirective:
        """Validate that the target type is consistent with mode.

        mode=loudnorm requires LoudnormTarget; mode=peak requires PeakTarget.
        """
        if self.mode == "loudnorm" and not isinstance(self.target, LoudnormTarget):
            raise ValueError("When mode=loudnorm, target must be LoudnormTarget.")
        if self.mode == "peak" and not isinstance(self.target, PeakTarget):
            raise ValueError("When mode=peak, target must be PeakTarget.")
        return self
