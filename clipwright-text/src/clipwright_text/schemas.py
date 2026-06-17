"""Schemas for clipwright-text."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AddTextOptions(BaseModel):
    """Options for add_text().

    Not yet implemented — stub for Red phase only.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    text: str
    start_sec: float
    duration_sec: float
    x: str = "(w-tw)/2"
    y: str = "h-th-40"
    font_size: int = 48
    font_color: str = "white"
    box: bool = False
    box_color: str = "black@0.5"
    fade_in_sec: float = 0.3
    fade_out_sec: float = 0.3
    font_path: str | None = None
