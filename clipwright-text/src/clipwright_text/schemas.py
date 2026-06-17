"""schemas.py — Pydantic schema for AddTextOptions.

Defines the options model for text overlay annotation. Core shared types
(MediaRef, Artifact, ToolResult) are imported from clipwright.schemas and must
not be redefined here (§6 convention contract).

Value-range validation (start_sec>=0, duration_sec>0, etc.) is intentionally
NOT enforced here via Pydantic constraints. It is validated manually inside
_add_text_inner so that the error envelope carries a precise hint (decision OQ-1).
AddTextOptions uses extra="forbid" so unknown keys are rejected at the schema
boundary before business logic runs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AddTextOptions(BaseModel):
    """Options for adding a text overlay marker to an OTIO timeline.

    text, start_sec, and duration_sec are required. All other fields are
    optional with sensible defaults for a lower-third subtitle-style overlay.

    Value-range validation (start_sec>=0, duration_sec>0, font_size>0, etc.)
    is performed manually in _add_text_inner to produce precise error hints —
    not via Pydantic constraints (decision OQ-1).

    Color fields (font_color, box_color) accept named colors, #RRGGBB, or
    name@alpha format. Validation against the allowlist is done in
    _add_text_inner.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    text: str
    """Text to display. Must be non-empty single-line (no newlines or control chars)."""

    start_sec: float
    """Start time in seconds from the beginning of the timeline. Must be >= 0."""

    duration_sec: float
    """Duration of the text overlay in seconds. Must be > 0."""

    x: str = "(w-tw)/2"
    """Horizontal position as an ffmpeg drawtext expression.

    Default: horizontally centered.
    """

    y: str = "h-th-40"
    """Vertical position as an ffmpeg drawtext expression. Default: lower-third."""

    font_size: int = 48
    """Font size in points. Must be > 0."""

    font_color: str = "white"
    """Font color. Named color, #RRGGBB, or name@alpha (e.g. white, #FFCC00, black@0.5).

    Validated against the allowlist ^[A-Za-z0-9#@._-]+$ in _add_text_inner.
    """

    box: bool = False
    """Whether to draw a background box behind the text."""

    box_color: str = "black@0.5"
    """Background box color. Named color, #RRGGBB, or name@alpha."""

    fade_in_sec: float = 0.3
    """Fade-in duration in seconds. Must be >= 0.

    Sum of fade_in_sec + fade_out_sec must not exceed duration_sec.
    """

    fade_out_sec: float = 0.3
    """Fade-out duration in seconds. Must be >= 0.

    Sum of fade_in_sec + fade_out_sec must not exceed duration_sec.
    """

    font_path: str | None = None
    """Absolute path to a .ttf/.otf font file.

    When None, clipwright-render resolves a platform default font.
    """
