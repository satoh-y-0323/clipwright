"""schemas.py — Pydantic models for clipwright-scene options.

Defines DetectScenesOptions for the scene boundary detection tool.
Core shared types (MediaRef, Artifact, ToolResult) are imported from
clipwright.schemas and must not be redefined here (§6 convention contract).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DetectScenesOptions(BaseModel):
    """Options for scene boundary detection.

    Controls the behaviour of the underlying backend and post-processing
    (boundary merging) without exposing backend-specific internals to callers.
    """

    threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description=(
                "Scene change sensitivity (0.0=most sensitive, 1.0=least sensitive). "
                "AI agents should adjust: 'major cuts only' → ~0.5, "
                "'subtle transitions' → ~0.1. Default 0.3 balances FP/FN."
            ),
        ),
    ] = 0.3

    min_scene_duration: Annotated[
        float,
        Field(
            default=1.0,
            ge=0.0,
            description=(
                "Minimum seconds between boundaries. "
                "Closer boundaries are merged (keep highest confidence). "
                "Set 0.0 to disable merging."
            ),
        ),
    ] = 1.0

    backend: Annotated[
        Literal["ffmpeg", "pyscenedetect"],
        Field(
            default="ffmpeg",
            description=(
                "Detection backend. 'ffmpeg' uses built-in scdet filter. "
                "'pyscenedetect' uses scenedetect CLI "
                "(more accurate, requires install)."
            ),
        ),
    ] = "ffmpeg"
