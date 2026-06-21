"""schemas.py — Pydantic schema for AddOverlayOptions.

Defines the options model for image overlay annotation. Core shared types
(MediaRef, Artifact, ToolResult) are imported from clipwright.schemas and must
not be redefined here (§6 convention contract).

Value-range validation (start_sec>=0, duration_sec>0, opacity 0..1, etc.) is
intentionally NOT enforced here via Pydantic constraints, except for scale which
uses Field(gt=0, le=8.0) per V2-9. All other range checks are validated manually
inside overlay.py so that the error envelope carries a precise hint (decision OQ-1).
AddOverlayOptions uses extra="forbid" so unknown keys are rejected at the schema
boundary before business logic runs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AddOverlayOptions(BaseModel):
    """Options for adding an image overlay marker to an OTIO timeline.

    image_path, start_sec, and duration_sec are required. All other fields are
    optional with sensible defaults for a centered watermark-style overlay.

    Value-range validation (start_sec>=0, duration_sec>0, opacity 0..1, etc.)
    is performed manually in _add_overlay_inner to produce precise error hints —
    not via Pydantic constraints (decision OQ-1), except scale which has
    Field(gt=0, le=8.0) per V2-9 (schema is the first line of defence for scale).

    x/y expressions are validated against the allowlist ^[A-Za-z0-9_()+\\-*/. ]+$
    in overlay.py (V2-5): this rejects `:;[],'` and control characters.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    image_path: str
    """Path to the image file to overlay.

    Must be co-located in the output parent dir tree.
    """

    start_sec: float
    """Start time in seconds from the beginning of the timeline. Must be >= 0."""

    duration_sec: float
    """Duration of the image overlay in seconds. Must be > 0."""

    x: str = "(W-w)/2"
    """Horizontal position as an ffmpeg overlay expression (uses CAPITAL W/w).

    Default: horizontally centered. Validated against allowlist in overlay.py (V2-5).
    """

    y: str = "(H-h)/2"
    """Vertical position as an ffmpeg overlay expression (uses CAPITAL H/h).

    Default: vertically centered. Validated against allowlist in overlay.py (V2-5).
    """

    scale: float = Field(default=1.0, gt=0, le=8.0)
    """Scale factor for the overlay image. Must be in range (0, 8.0] (V2-9).

    1.0 = original size. Values > 1.0 enlarge; < 1.0 shrink.
    Schema enforces gt=0 and le=8.0 as the first line of defence (V2-9).
    overlay.py also validates this range manually to emit a precise hint (OQ-1).
    """

    opacity: float = 1.0
    """Opacity of the overlay image.

    Range [0.0, 1.0] validated manually in overlay.py.
    """

    fade_in_sec: float = 0.3
    """Fade-in duration in seconds. Must be >= 0.

    Sum of fade_in_sec + fade_out_sec must not exceed duration_sec.
    """

    fade_out_sec: float = 0.3
    """Fade-out duration in seconds. Must be >= 0.

    Sum of fade_in_sec + fade_out_sec must not exceed duration_sec.
    """
