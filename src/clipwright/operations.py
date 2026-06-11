"""operations.py — Declarative edit operation types and apply logic.

Operations are typed via a Pydantic discriminated union;
apply_operations applies them to the timeline in an all-or-nothing transaction.

This vocabulary forms the common interface for detect-family tools.
(Spec §4.2 dogfooding premise)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import opentimelineio as otio
from pydantic import BaseModel, Field

from clipwright.errors import ErrorCode
from clipwright.otio_utils import add_clip, add_gap, add_marker
from clipwright.schemas import (
    MediaRef,
    OperationError,
    RationalTimeModel,
    TimeRangeModel,
    ValidationReport,
)

# ===========================================================================
# Operation types (discriminated union members)
# ===========================================================================


class AddClipOp(BaseModel):
    """Operation to append a clip to a track.

    track is a flat index (0=V1, 1=A1).
    metadata uses Any because OTIO metadata is an arbitrary key-value dict.
    Size and nesting limits are left for a future task.
    """

    op: Literal["add_clip"]
    track: int = 0
    media: MediaRef
    source_range: TimeRangeModel
    name: str | None = None
    metadata: dict[str, Any] | None = None


class AddGapOp(BaseModel):
    """Operation to append a gap to a track.

    track is a flat index (0=V1, 1=A1).
    """

    op: Literal["add_gap"]
    track: int = 0
    duration: RationalTimeModel


class AddMarkerOp(BaseModel):
    """Operation to attach a marker to the track itself (§13.5 DC-GP-001 re).

    track is a flat index (0=V1, 1=A1).
    No clip needs to exist; an empty track is valid.
    metadata uses Any because OTIO metadata is an arbitrary key-value dict.
    Size and nesting limits are left for a future task.
    """

    op: Literal["add_marker"]
    track: int = 0
    marked_range: TimeRangeModel
    name: str
    color: str | None = None
    metadata: dict[str, Any] | None = None


# Discriminated union (discriminator="op")
Operation = Annotated[
    AddClipOp | AddGapOp | AddMarkerOp,
    Field(discriminator="op"),
]

# ===========================================================================
# apply_operations — all-or-nothing (§13.1 DC-AM-004)
# ===========================================================================


def apply_operations(
    timeline: otio.schema.Timeline,
    ops: list[AddClipOp | AddGapOp | AddMarkerOp],
    *,
    validate_only: bool,
) -> ValidationReport:
    """Apply ops to timeline (all-or-nothing).

    Validates all operations first. If any are invalid, nothing is applied and
    ValidationReport(valid=False, applied_count=0, errors=[...]) is returned.
    All operations are applied only when every one is valid; applied_count=len(ops).

    validate_only=True: validates only; does not apply or save (applied_count=0).
    track is resolved by flat index (0-based). Out-of-range raises TRACK_NOT_FOUND.
    """
    operation_count = len(ops)
    errors: list[OperationError] = []
    track_count = len(timeline.tracks)

    # --- Validation phase ---
    for i, op in enumerate(ops):
        if op.track < 0 or op.track >= track_count:
            errors.append(
                OperationError(
                    index=i,
                    code=ErrorCode.TRACK_NOT_FOUND,
                    message=(
                        f"track {op.track} does not exist."
                        f" The timeline has {track_count} track(s)."
                        f" Specify track in the range 0..{track_count - 1}"
                    ),
                )
            )

    if errors:
        return ValidationReport(
            valid=False,
            operation_count=operation_count,
            applied_count=0,
            errors=errors,
        )

    # Return early when validate_only is set
    if validate_only:
        return ValidationReport(
            valid=True,
            operation_count=operation_count,
            applied_count=0,
            errors=[],
        )

    # --- Apply phase ---
    for op in ops:
        track = timeline.tracks[op.track]
        if isinstance(op, AddClipOp):
            add_clip(
                track,
                op.media,
                op.source_range,
                name=op.name,
                metadata=op.metadata,
            )
        elif isinstance(op, AddGapOp):
            add_gap(track, op.duration)
        elif isinstance(op, AddMarkerOp):
            add_marker(
                track,
                op.marked_range,
                op.name,
                color=op.color,
                metadata=op.metadata,
            )

    return ValidationReport(
        valid=True,
        operation_count=operation_count,
        applied_count=operation_count,
        errors=[],
    )
