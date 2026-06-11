"""schemas.py — Shared Pydantic types and time-conversion helpers for Clipwright.

These form the contract surface and are locked in first.
All tool input/output types share the vocabulary defined here; do not redefine per tool.
"""

from __future__ import annotations

from typing import Any, Literal

import opentimelineio as otio
from pydantic import BaseModel

# ===========================================================================
# Time models (equivalent to OTIO RationalTime / TimeRange)
# ===========================================================================


class RationalTimeModel(BaseModel):
    """Pydantic type equivalent to opentime.RationalTime.

    Always carries rate; never stores seconds as a bare float (§4.4).
    """

    value: float
    rate: float


class TimeRangeModel(BaseModel):
    """Pydantic type equivalent to opentime.TimeRange."""

    start_time: RationalTimeModel
    duration: RationalTimeModel


# ===========================================================================
# Media references and artifacts
# ===========================================================================


class MediaRef(BaseModel):
    """Reference to a local media file.

    Holds a path only; never stores raw bytes (§6.6 file I/O convention).
    """

    target_url: str
    name: str | None = None
    available_range: TimeRangeModel | None = None


class Artifact(BaseModel):
    """Reference to a tool output file.

    Large detail data should be stored here as a path rather than in data (§6.3).
    """

    role: str
    """File role. E.g. "timeline" | "output" | "caption" | "analysis"."""
    path: str
    format: str
    """File format. E.g. "otio" | "mp4" | "srt" | "json"."""


# ===========================================================================
# Return value envelope types (§6.3 / §6.4)
# ===========================================================================


class ToolResult(BaseModel):
    """Success envelope (§6.3).

    ok is always True. summary must contain the key points an AI needs to decide next.
    """

    ok: Literal[True] = True
    summary: str
    data: dict[str, Any] = {}
    artifacts: list[Artifact] = []
    warnings: list[str] = []


class ToolError(BaseModel):
    """Error detail. The three-part set: code / message / hint (§6.4)."""

    code: str
    message: str
    hint: str


class ToolErrorResult(BaseModel):
    """Failure envelope (§6.4).

    ok is always False. error must include what happened (message) and next step (hint).
    """

    ok: Literal[False] = False
    error: ToolError


# ===========================================================================
# Media probe result types
# ===========================================================================


class StreamInfo(BaseModel):
    """Information for a single stream returned by ffprobe."""

    index: int
    codec_type: str
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None


class MediaInfo(BaseModel):
    """Whole-file media information returned by ffprobe."""

    path: str
    container: str | None
    duration: RationalTimeModel | None
    streams: list[StreamInfo]
    bit_rate: int | None = None


# ===========================================================================
# Operation validation result types (§13.1 DC-AM-003)
# ===========================================================================


class OperationError(BaseModel):
    """Error information for a single entry in the operations list.

    Stored in the ValidationReport produced by apply_operations to indicate
    which operation failed and why.
    """

    index: int
    """Zero-based position in the operations list."""
    code: str
    """String representation of an ErrorCode value."""
    message: str


class ValidationReport(BaseModel):
    """Validation/apply result report for apply_operations (§13.1 DC-AM-003/DC-AM-004).

    All-or-nothing semantics: if any operation is invalid, applied_count=0 and
    nothing is written to the timeline.
    """

    valid: bool
    operation_count: int
    applied_count: int
    """0 when validate_only=True or when any invalid operation is present."""
    errors: list[OperationError] = []


# ===========================================================================
# OTIO time conversion helpers (placed in schemas.py per §13.1 DC-GP-005)
# ===========================================================================


def to_otio_time(rt: RationalTimeModel) -> otio.opentime.RationalTime:
    """Convert a RationalTimeModel to opentime.RationalTime.

    otio_utils imports this function rather than reimplementing the conversion.
    """
    return otio.opentime.RationalTime(value=rt.value, rate=rt.rate)


def from_otio_time(rt: otio.opentime.RationalTime) -> RationalTimeModel:
    """Convert opentime.RationalTime to a RationalTimeModel.

    Preserves rate as-is without normalising to a seconds float.
    """
    return RationalTimeModel(value=rt.value, rate=rt.rate)
