"""transition.py — Orchestration layer for clipwright-transition.

add_transition is the sole ClipwrightError -> error_result boundary (ADR-T-1).
No error conversion in server.py; no I/O in plan.py.

Design decisions:
- Fast-fail order before OTIO I/O: extension check, parent dir existence,
  output == input path comparison (sequence/sequence.py pattern, ADR-T-1).
- count_video_clips mirrors render's resolve_kept_ranges (846-897) but only
  counts Clips; no KeptRange construction (ADR-T-3).
- The duration-clamping responsibility belongs to render (ADR-T-2).
  transition validates range / duplicates / types only.
- Error messages use basename only, not full paths (CWE-209).
- Input OTIO is loaded into memory, modified in-place (in-memory object only),
  then saved to a different path (non-destructive: input file is never modified).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline, save_timeline
from clipwright.schemas import ToolResult

import clipwright_transition
from clipwright_transition.plan import ResolvedTransition, resolve_transitions
from clipwright_transition.schemas import AddTransitionOptions


def add_transition(
    timeline: str,
    output: str,
    options: AddTransitionOptions,
) -> dict[str, Any]:
    """Apply transition directives to a timeline and write to a new OTIO file.

    Sole ClipwrightError -> error_result boundary (ADR-T-1).
    Non-destructive: the input timeline file is never modified.

    Args:
        timeline: Input OTIO timeline file path (.otio).
        output: Output OTIO file path (.otio). Must differ from the input.
        options: Exactly one of uniform or per_boundary (non-empty).

    Returns:
        ToolResult dict (ok/summary/data/artifacts/warnings on success;
        ok/error on failure).
    """
    try:
        return _add_transition_inner(timeline, output, options).model_dump()
    except ClipwrightError as exc:
        return error_result(str(exc.code), exc.message, exc.hint).model_dump()
    except Exception:
        # SR L-1: catch non-ClipwrightError exceptions (e.g. OTIOError from
        # save_timeline) with fixed wording to prevent tmp path exposure (CWE-209).
        return error_result(
            str(ErrorCode.INTERNAL),
            "Failed to write the output timeline.",
            "Check that the output directory is writable and has free space.",
        ).model_dump()


def _add_transition_inner(
    timeline: str,
    output: str,
    options: AddTransitionOptions,
) -> ToolResult:
    """Internal implementation. Raises ClipwrightError on any failure.

    Flow (fast-fail order):
      1. Output extension .otio check.
      2. Output parent directory existence check.
      3. output == timeline path comparison (_check_output_not_input).
      3b. output within timeline directory boundary check.
      4. Load timeline (FILE_NOT_FOUND -> basename re-raise).
      5. count_video_clips (multiple tracks / existing Transition / Clip count).
      6. resolve_transitions (range / duplicate / mode validation in plan.py).
      7. Write transition directive to timeline metadata (in-memory only).
      8. save_timeline (atomic write to output path).
      9. Return ok_result.
    """
    output_path = Path(output)

    # ------------------------------------------------------------------
    # 1. Output extension must be .otio
    # ------------------------------------------------------------------
    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Invalid output extension. Only .otio is allowed.",
            hint="Change the output path extension to .otio.",
        )

    # ------------------------------------------------------------------
    # 2. Output parent directory must exist
    # ------------------------------------------------------------------
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    # ------------------------------------------------------------------
    # 3. Output must not be the same file as the input timeline
    # ------------------------------------------------------------------
    _check_output_not_input(output_path, timeline)

    # ------------------------------------------------------------------
    # 3b. Output must reside within the same directory tree as the timeline
    #     (SR L-3: mirrors sequence._resolve_and_check_colocation /
    #     trim PATH_NOT_ALLOWED pattern; resolve OSError fallback included).
    # ------------------------------------------------------------------
    _check_output_within_timeline_dir(output_path, Path(timeline).parent)

    # ------------------------------------------------------------------
    # 4. Load timeline (raises OTIO_ERROR on parse failure;
    #    FILE_NOT_FOUND is converted to basename-only for CWE-209)
    # ------------------------------------------------------------------
    # Pre-check for CWE-209: load_timeline may surface the full path in its
    # error message; intercept early and re-raise with basename only.
    if not Path(timeline).exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {Path(timeline).name}",
            hint="Check that the timeline path is correct and the file exists.",
        )

    tl = load_timeline(timeline)

    # ------------------------------------------------------------------
    # 5. Count video clips (raises INVALID_INPUT on multi-track /
    #    existing Transition / zero clips)
    # ------------------------------------------------------------------
    n_clips = count_video_clips(tl)

    # ------------------------------------------------------------------
    # 6. Resolve transitions (pure logic; raises INVALID_INPUT on range /
    #    duplicate violations or if n_clips < 2)
    # ------------------------------------------------------------------
    resolved: list[ResolvedTransition] = resolve_transitions(n_clips, options)

    # ------------------------------------------------------------------
    # 7. Write transition directive to timeline metadata (in-memory only;
    #    input file on disk is never touched).
    #
    #    ADR-T-4: normalised form is always the expanded per-boundary list,
    #    ascending by after_clip_index.  Existing directives are preserved;
    #    only the "transition" key is added / replaced.
    # ------------------------------------------------------------------
    transitions_payload: list[dict[str, Any]] = [
        {
            "after_clip_index": rt.after_clip_index,
            "type": rt.type,
            "duration_sec": rt.duration_sec,
        }
        for rt in resolved
    ]

    clipwright_meta = tl.metadata.get("clipwright", {})
    # AnyDictionary returned by OTIO is a Mapping; convert to plain dict to allow
    # setdefault / item assignment (AnyDictionary supports it but typing is opaque).
    if not isinstance(clipwright_meta, dict):
        clipwright_meta = dict(clipwright_meta)

    clipwright_meta["transition"] = {
        "tool": "clipwright_add_transition",
        "version": clipwright_transition.__version__,
        "kind": "transition",
        "transitions": transitions_payload,
    }
    tl.metadata["clipwright"] = clipwright_meta

    # ------------------------------------------------------------------
    # 8. Atomic save to output path
    # ------------------------------------------------------------------
    save_timeline(tl, output)

    # ------------------------------------------------------------------
    # 9. Build ok_result (ADR-T-6: summary includes boundary count and mode)
    # ------------------------------------------------------------------
    mode = "uniform" if options.uniform is not None else "per_boundary"
    boundary_count = len(resolved)
    summary = (
        f"Applied {boundary_count} transition(s) in {mode} mode "
        f"to '{Path(output).name}'."
    )

    return ok_result(
        summary,
        data={
            "boundary_count": boundary_count,
            "mode": mode,
            "output": output,
        },
        artifacts=[{"role": "timeline", "path": output, "format": "otio"}],
    )


def _check_output_not_input(output_path: Path, input_timeline: str) -> None:
    """Raise INVALID_INPUT if output and input resolve to the same path.

    Mirrors sequence._check_output_not_source (sequence.py 401-426).
    Both paths are resolved with fallback to absolute() on OSError.

    Args:
        output_path: Proposed output path.
        input_timeline: Input timeline path string.

    Raises:
        ClipwrightError(INVALID_INPUT): when the resolved paths are equal.
    """
    try:
        out_resolved = str(output_path.resolve())
    except OSError:
        try:
            out_resolved = str(output_path.absolute())
        except OSError:
            out_resolved = str(output_path)

    try:
        in_resolved = str(Path(input_timeline).resolve())
    except OSError:
        try:
            in_resolved = str(Path(input_timeline).absolute())
        except OSError:
            in_resolved = str(Path(input_timeline))

    if out_resolved == in_resolved:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input timeline path are the same.",
            hint=(
                "Use a different output path "
                "(tool chaining requires a distinct OTIO file)."
            ),
        )


def _check_output_within_timeline_dir(output_path: Path, timeline_dir: Path) -> None:
    """Raise PATH_NOT_ALLOWED if output is outside the timeline directory tree.

    Mirrors sequence._resolve_and_check_colocation (keep in sync).
    Falls back to absolute()-based comparison when resolve() raises OSError.
    Error message uses fixed wording without exposing the full path (CWE-209).

    Args:
        output_path: Proposed output OTIO file path.
        timeline_dir: Parent directory of the input timeline file.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when output is outside the boundary.
    """
    try:
        base = timeline_dir.resolve()
        target = output_path.resolve()
        base_str = str(base)
        target_str = str(target)
        if not (
            target_str == base_str
            or target_str.startswith(base_str + "/")
            or target_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Output file points outside the project boundary.",
                hint=(
                    "Use an output path located under the same directory"
                    " as the input OTIO timeline."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve() failure (network paths, extremely long paths, symlink loops):
        # fall back to absolute()-based best-effort comparison.
        try:
            base_abs = str(timeline_dir.absolute())
            target_abs = str(output_path.absolute())
            if not (
                target_abs == base_abs
                or target_abs.startswith(base_abs + "/")
                or target_abs.startswith(base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="Output file points outside the project boundary.",
                    hint=(
                        "Use an output path located under the same directory"
                        " as the input OTIO timeline."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            # Truly unresolvable: skip boundary check (best-effort, same as
            # sequence._resolve_and_check_colocation SR L-1 / DC-GP-002).
            pass


def count_video_clips(tl: otio.schema.Timeline) -> int:
    """Count the number of Clips in the single video track of a Timeline.

    Mirrors render's resolve_kept_ranges (plan.py 846-897) but only counts
    Clips; no KeptRange construction (ADR-T-3).

    Rules:
    - Multiple video tracks -> INVALID_INPUT (transition side; render uses
      UNSUPPORTED_OPERATION for the same condition).
    - Zero video tracks -> INVALID_INPUT.
    - Gaps are skipped.
    - Existing otio.schema.Transition items -> INVALID_INPUT.
    - Clips are counted.

    Args:
        tl: An OTIO Timeline.

    Returns:
        Number of Clip objects in the first video track.

    Raises:
        ClipwrightError(INVALID_INPUT): on multiple tracks, existing
            Transitions, or zero video tracks.
    """
    video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]

    if len(video_tracks) >= 2:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="The timeline contains two or more video tracks.",
            hint="Use a timeline with a single video track.",
        )

    if len(video_tracks) == 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="No video track found.",
            hint="Use an OTIO timeline that contains a video track.",
        )

    video_track = video_tracks[0]
    clip_count = 0

    for item in video_track:
        if isinstance(item, otio.schema.Gap):
            # Gaps represent removed regions; skip them.
            continue
        if isinstance(item, otio.schema.Transition):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="The timeline already contains a Transition.",
                hint=(
                    "Apply transitions to a hard-cut timeline "
                    "(no existing Transitions)."
                ),
            )
        if isinstance(item, otio.schema.Clip):
            clip_count += 1

    return clip_count
