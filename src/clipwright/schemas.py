"""schemas.py — Shared Pydantic types and time-conversion helpers for Clipwright.

These form the contract surface and are locked in first.
All tool input/output types share the vocabulary defined here; do not redefine per tool.
"""

from __future__ import annotations

from typing import Any

import opentimelineio as otio
from pydantic import BaseModel, ConfigDict

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
    Extra keys in input dicts are silently ignored so that satellite tool dicts
    with additional metadata can be coerced without raising ValidationError (M-002).
    """

    model_config = ConfigDict(extra="ignore")

    role: str
    """File role. E.g. "timeline" | "output" | "caption" | "analysis"."""
    path: str
    format: str
    """File format. E.g. "otio" | "mp4" | "srt" | "json"."""


# ===========================================================================
# Return value envelope types (§6.3 / §6.4)
# ===========================================================================


class ToolError(BaseModel):
    """Error detail. The three-part set: code / message / hint (§6.4)."""

    code: str
    message: str
    hint: str


class ToolResult(BaseModel):
    """Unified return envelope for all Clipwright tools (§6.3 / §6.4).

    A single model carries both success and error responses; inspect ``ok`` to branch.
    Using a union (ToolResult | ToolErrorResult) is intentionally avoided because
    FastMCP 1.27.2 detects unions and activates ``wrap_output=True``, which wraps
    ``structuredContent`` in a ``{"result": ...}`` object and breaks the wire contract.

    Success: ok=True, summary set, error=None.
    Failure: ok=False, error populated, summary may be None.

    Dict-like access (``result["ok"]``, ``result.get("key")``, ``"key" in result``)
    is supported for backward compatibility with callers that treat the envelope as a
    plain dict.  These methods delegate to ``model_dump()`` on each call.
    """

    # allow_inf_nan=False prevents Infinity/NaN from leaking into the JSON wire format.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    ok: bool
    summary: str | None = None
    data: dict[str, Any] = {}
    artifacts: list[Artifact] = []
    warnings: list[str] = []
    error: ToolError | None = None

    def __getitem__(self, key: str) -> Any:
        """Backward-compatibility shim for dict-style read access (``result["ok"]``).

        Will be removed after all callers migrate to attribute access.
        """
        return self.model_dump()[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Backward-compatibility shim for dict-style ``.get()`` access.

        Example: ``result.get("artifacts", [])``.
        Will be removed after all callers migrate to attribute access.
        """
        return self.model_dump().get(key, default)

    def __contains__(self, key: object) -> bool:
        """Backward-compatibility shim for ``"key" in result`` membership tests.

        Will be removed after all callers migrate to attribute access.
        """
        return key in self.model_dump()


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
    nb_frames: int | None = None
    channel_layout: str | None = None
    """Audio channel layout (e.g. "mono", "stereo"). None if not detected."""
    start_timecode: str | None = None
    """Per-stream timecode tag (raw; validation handled by nle_interop)."""


class MediaInfo(BaseModel):
    """Whole-file media information returned by ffprobe."""

    path: str
    container: str | None
    duration: RationalTimeModel | None
    streams: list[StreamInfo]
    bit_rate: int | None = None
    start_timecode: str | None = None
    """Resolved timecode (format.tags priority, then streams[].tags)."""


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


def full_media_range(media_info: MediaInfo) -> TimeRangeModel:
    """Build a TimeRangeModel spanning the whole referenced media asset (0..duration).

    This is the ``available_range`` semantics from ADR-3: it describes the full
    extent of the source media that a MediaRef points to, not any individual
    clip's own (possibly partial) source_range. Callers that build multiple
    clips referencing the same media should construct this once and reuse it
    for every clip.

    Requires media_info.duration to be set; raises ValueError otherwise
    (callers are expected to have already validated duration is not None).
    """
    duration = media_info.duration
    if duration is None:
        raise ValueError("media_info.duration must not be None")
    return TimeRangeModel(
        start_time=RationalTimeModel(value=0.0, rate=duration.rate),
        duration=RationalTimeModel(value=duration.value, rate=duration.rate),
    )
