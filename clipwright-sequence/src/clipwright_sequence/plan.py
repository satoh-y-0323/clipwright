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

from dataclasses import dataclass, field

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo

from clipwright_sequence.schemas import SequenceClip

# Floating-point epsilon for near-zero comparisons (same convention as trim/silence).
_EPSILON = 1e-9


@dataclass(frozen=True)
class SourceProbe:
    """Probe result for a single source media file.

    abs_path is the resolved absolute path used as the probes dict key.
    rate is frames-per-second (video). Audio-only sources never reach plan.py —
    they are rejected by sequence.py before resolve_clip_specs is called.
    The sentinel check (rate >= 1000.0) is performed by sequence.py.

    duration_value carries the source probe's original RationalTime value
    (frame count at `rate`) as reported by inspect_media, alongside the
    derived duration_sec (seconds, used for start/end arithmetic in this
    module). sequence.py's available_range construction must read
    available_duration_value rather than recomputing the value as
    `duration_sec * rate`, which would round-trip through a division and
    reintroduce floating-point error (CR-NEW low, precision).
    duration_value defaults to None (callers that only exercise
    resolve_clip_specs, e.g. tests, need not supply it); available_duration_value
    falls back to duration_sec * rate in that case, preserving the original
    round-trip behaviour for callers that don't care about the
    available_range precision edge case.

    media_info carries the full MediaInfo returned by inspect_media so that
    sequence.py can build the target_url -> MediaInfo map consumed by
    clipwright.nle_interop.conform_timeline_for_nle (start-timecode shift and
    Resolve audio-layout mirroring, ADR-NI-8). It defaults to None so callers
    that only exercise resolve_clip_specs (e.g. plan-only tests) need not
    supply it; conform simply skips any source whose media_info is absent.
    """

    abs_path: str
    duration_sec: float
    rate: float
    has_video: bool
    duration_value: float | None = field(default=None)
    media_info: MediaInfo | None = field(default=None)

    @property
    def available_duration_value(self) -> float:
        """Resolved duration value (frame count at `rate`) for available_range.

        Prefers the raw probe value (duration_value) to avoid a
        divide-then-multiply round-trip through duration_sec; falls back to
        duration_sec * rate when duration_value wasn't supplied.
        """
        if self.duration_value is not None:
            return self.duration_value
        return self.duration_sec * self.rate


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
                message="A clip's end_sec exceeds the source duration.",
                hint=(
                    f"Clip at index {index}: end_sec={end:.3f}s exceeds "
                    f"source duration={duration:.3f}s (tolerance={tolerance:.6f}s). "
                    "Set end_sec within the source duration, or omit end_sec."
                ),
            )

        # Inversion check: start must be strictly less than end (gap > _EPSILON).
        if start >= end - _EPSILON:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="A clip's start_sec is greater than or equal to its end_sec.",
                hint=(
                    f"Clip at index {index}: start_sec={start:.3f}s >= "
                    f"end_sec={end:.3f}s — zero-length or inverted range. "
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
