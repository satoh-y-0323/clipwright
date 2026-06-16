"""schemas.py — Pydantic schema for ExtractFramesOptions.

Defines the options model for frame extraction. Core shared types
(MediaRef, Artifact, ToolResult) are imported from clipwright.schemas
and must not be redefined here (§6 convention contract).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractFramesOptions(BaseModel):
    """Options for frame extraction from a video source.

    Controls extraction mode, output format, and image quality without
    exposing backend-specific internals to callers.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    mode: Annotated[
        Literal["interval", "scene", "timestamps"],
        Field(
            default="interval",
            description=(
                "Frame extraction mode. 'interval' extracts one frame every "
                "interval_sec seconds. 'scene' extracts frames at scene "
                "boundaries using scene_timeline. 'timestamps' extracts frames "
                "at the explicit positions listed in timestamps."
            ),
        ),
    ] = "interval"

    interval_sec: Annotated[
        float,
        Field(
            default=10.0,
            gt=0.0,
            description=(
                "Seconds between extracted frames when mode='interval'. "
                "Must be greater than 0. Default is 10.0 seconds."
            ),
        ),
    ] = 10.0

    scene_timeline: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Path to an OTIO timeline file produced by clipwright-scene. "
                "Required when mode='scene'; ignored otherwise."
            ),
        ),
    ] = None

    timestamps: Annotated[
        list[float],
        Field(
            default_factory=list,
            description=(
                "Explicit timestamps in seconds at which to extract frames. "
                "Used when mode='timestamps'. May be empty."
            ),
        ),
    ] = Field(default_factory=list)

    format: Annotated[
        Literal["jpeg", "png"],
        Field(
            default="jpeg",
            description=(
                "Output image format. 'jpeg' produces smaller files; "
                "'png' produces lossless output."
            ),
        ),
    ] = "jpeg"

    quality: Annotated[
        int,
        Field(
            default=2,
            ge=1,
            le=31,
            description=(
                "FFmpeg -q:v quality value for jpeg output (1=best, 31=worst). "
                "Valid only for jpeg; ignored when format='png'."
            ),
        ),
    ] = 2

    max_width: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Maximum output width in pixels. Aspect ratio is preserved. "
                "None means no resizing."
            ),
        ),
    ] = None
