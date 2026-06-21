"""plan.py — Pure clip-resolution logic for clipwright-sequence.

No I/O, no subprocess, no OTIO.  All operations work on float-second values.

Design decisions (ADR-SEQ-3):
- Empty clips list -> INVALID_INPUT (defensive guard).
- start_sec=None defaults to 0.0; end_sec=None defaults to probe.duration_sec.
- end_sec within one frame of duration is accepted and clipped to duration
  (DC-AS-003 tolerance: absorbs probe-measurement error, no warning emitted).
- start_sec >= end_sec - _EPSILON -> INVALID_INPUT (inversion check).
- Clips are returned in enumeration order with zero-based index (DC-GP-003).
- Probe keys are already resolved absolute paths (DC-AM-002 / §V2.6):
  plan.py does NOT call Path().resolve().
"""

from __future__ import annotations

from dataclasses import dataclass

from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_sequence.schemas import SequenceClip

# Floating-point epsilon for near-zero comparisons (same convention as trim/silence).
_EPSILON = 1e-9

# Sentinel frame rate used when a source has no video stream.
# Keep in sync with clipwright.media inspect_media sentinel (1000.0).
_SENTINEL_RATE = 1000.0


@dataclass(frozen=True)
class SourceProbe:
    """Probe result for a single source media file.

    abs_path is the resolved absolute path used as the probes dict key.
    rate is frames-per-second (video) or _SENTINEL_RATE for audio-only sources.
    """

    abs_path: str
    duration_sec: float
    rate: float
    has_video: bool


@dataclass(frozen=True)
class ResolvedClip:
    """A fully resolved clip ready for OTIO construction.

    start_sec and end_sec are concrete float seconds (no None values).
    index is the zero-based position in the input clips list.
    """

    source: str
    start_sec: float
    end_sec: float
    rate: float
    index: int


def resolve_clip_specs(
    probes: dict[str, SourceProbe],
    clips: list[SequenceClip],
) -> tuple[list[ResolvedClip], list[str]]:
    """Resolve SequenceClip specs against probed source metadata.

    Args:
        probes: Mapping from resolved absolute path to SourceProbe.
                Keys must already be resolved paths (DC-AM-002 / §V2.6).
        clips:  Ordered list of SequenceClip specs from the MCP caller.

    Returns:
        A 2-tuple of (resolved_clips, warnings).
        resolved_clips is in the same enumeration order as the input clips.
        warnings is an empty list in v0.1.0 (no clamping produces warnings).

    Raises:
        ClipwrightError(INVALID_INPUT) for:
          - Empty clips list (defensive guard, ADR-SEQ-3 §1).
          - end_sec exceeds duration beyond one-frame tolerance (DC-AS-003).
          - start_sec >= end_sec after defaulting (inversion, ADR-SEQ-3).
    """
    if not clips:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="No clips were provided.",
            hint="Provide at least one SequenceClip in the clips list.",
        )

    resolved: list[ResolvedClip] = []
    warnings: list[str] = []

    for index, clip in enumerate(clips):
        probe = probes[clip.media]
        duration = probe.duration_sec
        rate = probe.rate

        # Default None values to full-length range.
        start = clip.start_sec if clip.start_sec is not None else 0.0
        end = clip.end_sec if clip.end_sec is not None else duration

        # DC-AS-003 tolerance: end within one frame beyond duration is accepted
        # and silently clipped to duration (no warning — this absorbs probe error).
        tolerance = max(_EPSILON, 1.0 / rate)
        if end > duration and end <= duration + tolerance:
            end = duration

        # Out-of-range check: end still exceeds duration + tolerance.
        if end > duration + tolerance:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"end_sec ({end:.6f}) exceeds source duration "
                    f"({duration:.6f}) beyond the one-frame tolerance "
                    f"({tolerance:.6f}) for clip index {index}."
                ),
                hint=(
                    "Set end_sec within the source duration, or omit end_sec "
                    "to use the full duration."
                ),
            )

        # Inversion check: start must be strictly less than end (gap > _EPSILON).
        if start >= end - _EPSILON:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"start_sec ({start:.6f}) >= end_sec ({end:.6f}) "
                    f"for clip index {index}: zero-length or inverted range."
                ),
                hint=(
                    "Ensure start_sec < end_sec for every clip. "
                    "Omit end_sec to use the full source duration."
                ),
            )

        resolved.append(
            ResolvedClip(
                source=probe.abs_path,
                start_sec=start,
                end_sec=end,
                rate=rate,
                index=index,
            )
        )

    return resolved, warnings
