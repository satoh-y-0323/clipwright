"""schemas.py — STUB: ExtractFramesOptions Pydantic schema.

This stub exists only so that test_plan.py can import ExtractFramesOptions
without ImportError, to verify test logic (not production logic).
All validation will raise NotImplementedError.
"""

from __future__ import annotations

from pydantic import BaseModel


class ExtractFramesOptions(BaseModel):
    """STUB — placeholder only. Real implementation in developer task."""

    mode: str = "interval"
    interval_sec: float = 10.0
    scene_timeline: str | None = None
    timestamps: list[float] = []
    format: str = "jpeg"
    quality: int = 2
    max_width: int | None = None
