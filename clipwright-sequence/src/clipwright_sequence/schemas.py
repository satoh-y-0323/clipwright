"""schemas.py — Pydantic types for clipwright-sequence tool.

SequenceClip: a single clip specification within a multi-source sequence.

SequenceOptions is NOT defined in v0.1.0 (ADR-SEQ-1): the tool has no
adjustable parameters yet.  Introduce SequenceOptions only when concrete
options (e.g. transition hints) are added.

Common types (MediaRef, Artifact, ToolResult) are imported from clipwright.schemas;
do NOT redefine them here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SequenceClip(BaseModel):
    """A single clip specification within a multi-source sequence.

    Each SequenceClip refers to a source media file and an optional
    sub-range [start_sec, end_sec).  Omitting start_sec defaults to 0.0;
    omitting end_sec defaults to the source's full duration.

    Range validity (start_sec < end_sec, end_sec <= duration) is deferred
    to plan.resolve_clip_specs because duration is unknown at schema-validation
    time (ADR-SEQ-1).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    media: str = Field(
        description=(
            "Absolute or relative path to the source media file. "
            "The path is resolved by the orchestration layer before probe."
        )
    )
    start_sec: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Start of the clip in seconds from the beginning of the source media. "
            "Must be non-negative (ge=0) when provided. "
            "Defaults to 0.0 (beginning of the source) when omitted."
        ),
    )
    end_sec: float | None = Field(
        default=None,
        gt=0,
        description=(
            "End of the clip in seconds from the beginning of the source media. "
            "Must be strictly positive (gt=0) when provided. "
            "Defaults to the source's full duration when omitted."
        ),
    )
