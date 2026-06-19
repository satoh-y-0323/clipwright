"""schemas.py — Pydantic types for clipwright-trim tool.

TrimRange: a single (start_sec, end_sec) time range specification.
TrimOptions: keep/drop range lists with padding.

Mutual exclusion between keep and drop is NOT enforced here (ADR-6).
It is deferred to plan.derive_keep_ranges for precise error hints.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TrimRange(BaseModel):
    """A single explicit time range in seconds.

    start_sec must be non-negative and strictly less than end_sec.
    The schema does not validate start_sec < end_sec because duration
    is unknown at schema-validation time; this constraint is enforced
    in plan.derive_keep_ranges (ADR-6).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    start_sec: float = Field(
        ge=0,
        description=(
            "Start of the range in seconds from the beginning of the media. "
            "Must be non-negative (ge=0)."
        ),
    )
    end_sec: float = Field(
        description=(
            "End of the range in seconds from the beginning of the media. "
            "Must be strictly greater than start_sec."
        ),
    )


class TrimOptions(BaseModel):
    """Options controlling which time ranges to keep or drop.

    Exactly one of keep or drop should be non-empty at processing time.
    Providing both non-empty raises INVALID_INPUT in plan.derive_keep_ranges.
    Providing neither (both empty) is a full-duration passthrough (FR-2).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    keep: list[TrimRange] = Field(
        default_factory=list,
        description=(
            "Ranges to retain, in enumeration order. "
            "Each range becomes one Clip in the V1 track of the output OTIO. "
            "Mutually exclusive with drop."
        ),
    )
    drop: list[TrimRange] = Field(
        default_factory=list,
        description=(
            "Ranges to remove; the complement becomes the set of kept ranges. "
            "Dropped ranges are sorted and merged before complementing. "
            "Mutually exclusive with keep."
        ),
    )
    padding_sec: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Non-negative padding in seconds applied to each range boundary. "
            "In keep mode: expands each range outward before clamping. "
            "In drop mode: shrinks each drop range inward (content-protective)."
        ),
    )
