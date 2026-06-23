"""plan.py — Pure resolution logic for clipwright-transition.

Converts AddTransitionOptions into a sorted list of ResolvedTransition
objects (frozen dataclass), performing all boundary validation.

This module has no I/O, no OTIO dependency, and no subprocess calls;
it is intentionally a pure computation layer (ADR-T-4, ADR-T-7).
"""

from __future__ import annotations

from dataclasses import dataclass

from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_transition.schemas import AddTransitionOptions


@dataclass(frozen=True)
class ResolvedTransition:
    """A resolved transition to be applied at a single clip boundary.

    after_clip_index: 0-based index of the clip after which the transition
        is inserted.
    type: transition effect type (one of the four supported kinds).
    duration_sec: duration of the transition in seconds.
    """

    after_clip_index: int
    type: str
    duration_sec: float


def resolve_transitions(
    n_clips: int,
    options: AddTransitionOptions,
) -> list[ResolvedTransition]:
    """Resolve AddTransitionOptions into an ascending list of ResolvedTransitions.

    Parameters
    ----------
    n_clips:
        Total number of video clips in the timeline. Must be >= 2.
    options:
        Validated AddTransitionOptions (either uniform or per_boundary mode).

    Returns
    -------
    list[ResolvedTransition]
        Sorted ascending by after_clip_index, covering the requested boundaries.

    Raises
    ------
    ClipwrightError(INVALID_INPUT)
        - n_clips < 2: timeline too short for any transition.
        - per_boundary: any after_clip_index > n_clips-2 (out of range).
        - per_boundary: duplicate after_clip_index values.
    """
    if n_clips < 2:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The timeline has fewer than two clips.",
            hint=(
                "Build a multi-clip timeline with clipwright-sequence or "
                "clipwright-trim first, then apply transitions."
            ),
        )

    max_index = n_clips - 2  # inclusive upper bound for after_clip_index

    if options.uniform is not None:
        # Uniform mode: expand to every internal boundary [0, n_clips-2].
        spec = options.uniform
        return [
            ResolvedTransition(
                after_clip_index=i,
                type=spec.type,
                duration_sec=spec.duration_sec,
            )
            for i in range(n_clips - 1)
        ]

    # per_boundary mode (options.per_boundary is a non-empty list by schema validation).
    per_boundary = options.per_boundary
    if per_boundary is None:
        # Unreachable in practice: schema validators enforce that per_boundary is set
        # when uniform is None. Guard is retained for defensive programming only.
        raise ClipwrightError(
            code=ErrorCode.INTERNAL,
            message="Transition options validation failed (internal error).",
            hint="Provide a non-empty per_boundary list or use the uniform field.",
        )

    # Validate for duplicate indices before range check (collect all errors
    # in one pass: range check first so index information is sanitised).
    seen: set[int] = set()
    for entry in per_boundary:
        idx = entry.after_clip_index
        if idx > max_index:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="A per-boundary transition index is out of range.",
                hint=(
                    "Valid after_clip_index values for this timeline are"
                    f" [0, {max_index}]."
                ),
            )
        if idx in seen:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Duplicate after_clip_index in per_boundary list.",
                hint=(
                    "Each clip boundary can only have one transition. "
                    "Remove or merge duplicate entries."
                ),
            )
        seen.add(idx)

    # Build and sort ascending (directive ascending canonical form, ADR-T-4).
    resolved = [
        ResolvedTransition(
            after_clip_index=entry.after_clip_index,
            type=entry.type,
            duration_sec=entry.duration_sec,
        )
        for entry in per_boundary
    ]
    resolved.sort(key=lambda rt: rt.after_clip_index)
    return resolved
