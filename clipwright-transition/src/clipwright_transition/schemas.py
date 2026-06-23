"""schemas.py — Pydantic types for clipwright-transition tool.

TransitionSpec: specifies a single transition effect and its duration.
BoundaryTransition: a per-boundary transition applied after a specific clip index.
AddTransitionOptions: top-level input schema for the add_transition operation.

Common types (MediaRef, Artifact, ToolResult) are imported from clipwright.schemas;
do NOT redefine them here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Transition type allowed values (ADR-T-5).
_TransitionType = Literal["fade", "dissolve", "fadeblack", "fadewhite"]


class TransitionSpec(BaseModel):
    """Specifies a single transition effect and its duration.

    type: one of the four supported transition kinds.
    duration_sec: transition length in seconds; must be strictly positive and
    at most 5.0 seconds (reasonable upper bound to avoid consuming entire clips).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    type: _TransitionType = Field(
        description=(
            "Transition effect type. "
            "One of 'fade', 'dissolve', 'fadeblack', 'fadewhite'."
        ),
    )
    duration_sec: float = Field(
        gt=0,
        le=5.0,
        description=(
            "Duration of the transition in seconds. "
            "Must be strictly positive (gt=0) and at most 5.0 seconds (le=5.0)."
        ),
    )


class BoundaryTransition(BaseModel):
    """A per-boundary transition applied after a specific clip index.

    after_clip_index: the 0-based index of the clip after which the transition
    is inserted.  Upper-bound validation (must be < n_clips - 1) is deferred
    to plan.py because n_clips is unknown at schema-validation time (ADR-T-7).

    type and duration_sec share the same constraints as TransitionSpec.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    after_clip_index: int = Field(
        ge=0,
        description=(
            "0-based index of the clip after which the transition is inserted. "
            "Must be non-negative (ge=0). "
            "Upper-bound check (< n_clips - 1) is performed by plan.py."
        ),
    )
    type: _TransitionType = Field(
        description=(
            "Transition effect type. "
            "One of 'fade', 'dissolve', 'fadeblack', 'fadewhite'."
        ),
    )
    duration_sec: float = Field(
        gt=0,
        le=5.0,
        description=(
            "Duration of the transition in seconds. "
            "Must be strictly positive (gt=0) and at most 5.0 seconds (le=5.0)."
        ),
    )


class AddTransitionOptions(BaseModel):
    """Top-level input schema for the add_transition operation.

    Exactly one of 'uniform' or 'per_boundary' (non-empty) must be provided.
    Providing both or neither raises a ValidationError.

    uniform: apply the same transition at every clip boundary.
    per_boundary: apply individual transitions at specific clip boundaries.
        An empty list is treated as unspecified (equivalent to None).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    uniform: TransitionSpec | None = Field(
        default=None,
        description=(
            "Apply the same transition spec at every clip boundary. "
            "Mutually exclusive with per_boundary."
        ),
    )
    per_boundary: list[BoundaryTransition] | None = Field(
        default=None,
        max_length=1000,
        description=(
            "Apply individual transitions at specific clip boundaries. "
            "An empty list is treated as unspecified. "
            "Mutually exclusive with uniform. "
            "Maximum 1000 entries."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> AddTransitionOptions:
        """Ensure exactly one of uniform or per_boundary (non-empty) is provided."""
        has_uniform = self.uniform is not None
        has_per = self.per_boundary is not None and len(self.per_boundary) > 0
        if has_uniform == has_per:
            raise ValueError(
                "Provide exactly one of 'uniform' or 'per_boundary' (non-empty)."
            )
        return self
