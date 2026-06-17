"""schemas.py — Pydantic schema for SetSpeedOptions.

Defines the options model for speed changes. Core shared types
(MediaRef, Artifact, ToolResult) are imported from clipwright.schemas
and must not be redefined here (§6 convention contract).

Speed range validation (0.25–8.0) is intentionally NOT enforced here via
Pydantic constraints. Per decision OQ-1, it is validated manually inside
_set_speed_inner so that the error envelope carries a precise hint.
SetSpeedOptions uses extra="forbid" so unknown keys are rejected at the
schema boundary before business logic runs.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SetSpeedOptions(BaseModel):
    """Options for applying a speed change to clips in an OTIO timeline.

    speed is required; clip_index is optional (None means apply to all clips).
    Speed range (0.25-8.0) is validated manually in _set_speed_inner to produce
    a precise error hint — not via Pydantic constraints (decision OQ-1).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    speed: Annotated[
        float,
        Field(
            description=(
                "Playback speed multiplier. Values > 1.0 speed up the clip; "
                "values < 1.0 slow it down. Valid range is 0.25 to 8.0 inclusive. "
                "A value of 1.0 attaches a warp with time_scalar=1.0 (no-op warp). "
                "Range is validated at runtime; inf/nan are rejected."
            ),
        ),
    ]

    clip_index: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            description=(
                "Zero-based index into the clip-only space of the V1 track "
                "(gaps are excluded from indexing). None applies the speed change "
                "to all clips. Must be >= 0 when specified."
            ),
        ),
    ] = None
