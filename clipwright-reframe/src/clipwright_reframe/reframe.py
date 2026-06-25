"""reframe.py — clipwright-reframe orchestration layer (architecture-report §1.1).

Flow (3-layer: public API → _inner → error boundary):
  1. Output validation (extension, parent dir, output != media, output != timeline,
     output within media dir boundary)
  2. inspect_media: require video stream
  3. Timeline resolution (None -> create new / path -> load + validate)
  4. Annotate ReframeDirective to timeline metadata["clipwright"]["reframe"]
     For mode='track': spawn track_cli subprocess to obtain motion-centroid keyframes.
     On failure (numpy missing / subprocess error / run exception): constant-center
     fallback [{t_s:0, cx:0.5, cy:0.5}] is written; ok remains True.
  5. save_timeline (atomic)
  6. ok_result with summary / data / artifacts

Design decisions:
- Non-destructive: input media and OTIO are never modified.
- Directive dict shape is the only contract between this package and clipwright-render.
  render's reader-side _RenderReframe validates independently (defence-in-depth).
- 3-layer skeleton (public API / _inner / error boundary) keeps the MCP server
  startup clean; all ClipwrightError raised inside _inner are caught at the boundary.
- track_cli is spawned as a separate subprocess to keep numpy out of the server
  process (architecture-report §2.1 numpy isolation).
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import clipwright.process as _process
import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import (
    get_clipwright_metadata,
    load_timeline,
    new_timeline,
    save_timeline,
    set_clipwright_metadata,
)
from clipwright.process import safe_subprocess_message
from clipwright.schemas import ToolResult
from pydantic import ValidationError

import clipwright_reframe
from clipwright_reframe.schemas import CentreKeyframe, ReframeDirective, ReframeOptions

# Constant-center fallback track (architecture-report §5, DC-AM-002).
_CONSTANT_CENTER_TRACK: list[dict[str, float]] = [{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]

# Maximum keyframes passed to track_cli (SR-L-3 / N_max=80 adjudicated value).
# This value must match track_cli._DEFAULT_N_MAX and render's _N_MAX_TRACK.
# Kept as an independent copy per package (defence-in-depth); the equality is
# locked by test_reframe.py::TestNMaxSync.
_TRACK_MAX_KEYFRAMES = 80


def reframe(
    media: str,
    output: str,
    options: ReframeOptions,
    timeline: str | None,
) -> ToolResult:
    """Public API for reframe annotation. Converts ClipwrightError to ok=False envelope.

    Args:
        media: Input video file path (video stream required).
        output: Output OTIO timeline file path (.otio, same directory as media).
        options: ReframeOptions (target resolution, mode, anchor, pad_color).
        timeline: Existing timeline path (None = create new).

    Returns:
        ok_result or error_result ToolResult.
    """
    try:
        return _reframe_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _reframe_inner(
    media: str,
    output: str,
    options: ReframeOptions,
    timeline: str | None,
) -> ToolResult:
    """Internal implementation of reframe. Raises ClipwrightError directly."""
    # --- 0. Defensive re-validation of options ---
    # Catches model_construct-bypassed invalid values before any path I/O.
    try:
        ReframeOptions.model_validate(options.model_dump())
    except ValidationError:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Invalid reframe options.",
            hint=(
                "target_w/h must be even integers in 2..7680; mode must be"
                " crop/pad/blur_pad/track; anchor must be a valid 9-direction value."
            ),
        ) from None

    media_path = Path(media)
    output_path = Path(output)

    # --- 1. Output validation ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output file must use the .otio extension.",
            hint="Set the output file extension to .otio.",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output directory does not exist.",
            hint="Create the output directory first, then re-run.",
        )

    if _same_path(output_path, media_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input media path are the same.",
            hint="Change the output file path to differ from the input media.",
        )

    if timeline is not None and _same_path(output_path, Path(timeline)):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path and input timeline path are the same.",
            hint="Change the output file path to differ from the input timeline.",
        )

    _check_output_within_media_dir(media_path, output_path)

    # --- 2. inspect_media: video required ---

    if not media_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {media_path.name}",
            hint="Check that the input media file path is correct.",
        )

    media_info = inspect_media(media)
    has_video = any(s.codec_type == "video" for s in media_info.streams)
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint="Provide a media file that contains a video stream.",
        )

    duration_sec: float = 0.0
    if media_info.duration is not None:
        duration_sec = media_info.duration.value / media_info.duration.rate

    # --- 3. Timeline resolution ---

    if timeline is None:
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration, has_audio)
    else:
        # D1: timeline existence check before load (B-5).
        # FileNotFoundError from load_timeline must not escape as a raw exception.
        if not Path(timeline).exists():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=f"Timeline file not found: {Path(timeline).name}",
                hint=(
                    "Specify an existing .otio timeline file or omit"
                    " the timeline argument."
                ),
            )
        # D1: invalid .otio content must be wrapped as ClipwrightError (B-6).
        # load_timeline wraps OTIOError, but OTIO may raise ValueError for JSON parse
        # errors on some adapters; catch it here and convert to OTIO_ERROR.
        try:
            tl = load_timeline(timeline)
        except ClipwrightError:
            raise
        except (ValueError, OSError):
            raise ClipwrightError(
                code=ErrorCode.OTIO_ERROR,
                message=f"Failed to load OTIO file: {Path(timeline).name}",
                hint="Specify a valid .otio timeline file.",
            ) from None

    # --- 4. Annotate ReframeDirective ---

    warnings: list[str] = []
    track: list[CentreKeyframe] | None = None

    if options.mode == "track":
        track, track_warnings = _run_track_cli(media, duration_sec)
        warnings.extend(track_warnings)

    directive = ReframeDirective(
        tool="clipwright-reframe",
        version=clipwright_reframe.__version__,
        kind="reframe",
        target_w=options.target_w,
        target_h=options.target_h,
        mode=options.mode,
        anchor=options.anchor,
        pad_color=options.pad_color,
        track=track,
    )

    # Serialize directive to plain Python dict (via JSON round-trip) so that
    # OTIO stores primitive types (not Pydantic models or numpy scalars).
    # OTIO wraps nested lists/dicts in AnyVector/AnyDictionary; reading back
    # via dict(meta) only unwraps the top level.  JSON round-trip guarantees
    # that every nested value is a plain Python primitive that tests can compare
    # with == and isinstance(x, list/dict) checks.
    directive_dict: dict[str, Any] = json.loads(directive.model_dump_json())
    existing_meta = get_clipwright_metadata(tl)
    existing_meta["reframe"] = directive_dict
    set_clipwright_metadata(tl, existing_meta)

    # --- 5. save_timeline (atomic) ---

    save_timeline(tl, str(output_path))

    # --- 6. ok_result ---

    summary = (
        f"Reframe directive written for {media_path.name}."
        f" target={options.target_w}x{options.target_h}"
        f" mode={options.mode} anchor={options.anchor}."
        f" Directive saved to {output_path.name}."
    )

    return ok_result(
        summary,
        data={
            "target_w": options.target_w,
            "target_h": options.target_h,
            "mode": options.mode,
            "anchor": options.anchor,
            "pad_color": options.pad_color,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=warnings,
    )


def _run_track_cli(
    media: str,
    duration_sec: float,
) -> tuple[list[CentreKeyframe], list[str]]:
    """Spawn track_cli subprocess and return (track, warnings).

    On success: returns parsed CentreKeyframe list and empty warnings.
    On any failure (DEPENDENCY_MISSING / SUBPROCESS_FAILED / run exception / empty):
      returns constant-center fallback track and a descriptive warning.
      ok remains True — graceful degradation (architecture-report §5).

    CWE-209: warnings must not contain the full media path or stack traces.

    Args:
        media: Input video file path (passed to track_cli --media).
        duration_sec: Media duration in seconds (for track_cli timeout).

    Returns:
        (track_keyframes, warnings)
    """
    timeout = float(max(60, math.ceil(duration_sec * 4))) if duration_sec > 0 else 120.0

    cmd = [
        sys.executable,
        "-m",
        "clipwright_reframe.track_cli",
        "--media",
        media,
        "--media-duration",
        str(duration_sec),
        # Pass N_max explicitly so render-side and CLI-side stay in sync (SR-L-3).
        # reframe._TRACK_MAX_KEYFRAMES == track_cli._DEFAULT_N_MAX
        # == render._N_MAX_TRACK (all must be 80).
        "--max-keyframes",
        str(_TRACK_MAX_KEYFRAMES),
    ]

    try:
        result = _process.run(cmd, timeout=timeout)
        stdout = result.stdout.strip()
    except ClipwrightError as exc:
        safe_msg = safe_subprocess_message(exc)
        return (
            _make_constant_center_track(),
            [
                "Motion tracking failed during detection; wrote a static center track"
                f" instead. Reason: {safe_msg}."
            ],
        )
    except Exception:
        return (
            _make_constant_center_track(),
            [
                "Motion tracking failed due to an unexpected error;"
                " wrote a static center track instead."
            ],
        )

    # Parse stdout JSON.
    try:
        data: dict[str, Any] = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return (
            _make_constant_center_track(),
            [
                "Motion tracking produced invalid output;"
                " wrote a static center track instead."
            ],
        )

    if "error" in data:
        err = data["error"]
        code = str(err.get("code", "UNKNOWN"))
        if code == "DEPENDENCY_MISSING":
            return (
                _make_constant_center_track(),
                [
                    "Motion tracking disabled: numpy is not installed."
                    " Wrote a static center track instead."
                    " Install with: pip install 'clipwright-reframe[track]'."
                ],
            )
        return (
            _make_constant_center_track(),
            [
                f"Motion tracking failed during detection; wrote a static center track"
                f" instead. Reason: {code}."
            ],
        )

    raw_track = data.get("track", [])
    if not raw_track:
        return (
            _make_constant_center_track(),
            [
                "Motion tracking returned no keyframes;"
                " wrote a static center track instead."
            ],
        )

    # Validate and convert to CentreKeyframe objects.
    try:
        track = [CentreKeyframe(**kf) for kf in raw_track]
    except Exception:
        return (
            _make_constant_center_track(),
            [
                "Motion tracking returned invalid keyframe data;"
                " wrote a static center track instead."
            ],
        )

    return track, []


def _make_constant_center_track() -> list[CentreKeyframe]:
    """Return the constant-center fallback track [{t_s:0, cx:0.5, cy:0.5}]."""
    return [CentreKeyframe(t_s=0.0, cx=0.5, cy=0.5)]


def _same_path(a: Path, b: Path) -> bool:
    """Return True if both paths refer to the same entity.

    Falls back to string comparison on OSError.
    """
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _check_output_within_media_dir(media_path: Path, output_path: Path) -> None:
    """Verify that output_path resolves within the same directory as media_path.

    Prevents path-traversal when AI-provided output paths contain '..' components
    (CWE-22 prevention, mirrors frames/_check_within_boundary contract).

    Args:
        media_path: Input media file path (directory is the allowed base).
        output_path: Output .otio file path to validate.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when output is outside the media directory.
    """
    try:
        media_dir = media_path.resolve().parent
        output_dir = output_path.resolve().parent
        media_dir_str = str(media_dir)
        output_dir_str = str(output_dir)
        allowed = (
            output_dir_str == media_dir_str
            or output_dir_str.startswith(media_dir_str + "/")
            or output_dir_str.startswith(media_dir_str + os.sep)
        )
    except OSError:
        media_dir = media_path.absolute().parent
        output_dir = output_path.absolute().parent
        media_dir_str = str(media_dir)
        output_dir_str = str(output_dir)
        allowed = (
            output_dir_str == media_dir_str
            or output_dir_str.startswith(media_dir_str + "/")
            or output_dir_str.startswith(media_dir_str + os.sep)
        )

    if not allowed:
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message="Output path is outside the media directory.",
            hint=(
                "Place the output .otio file in the same directory as the input media."
            ),
        )


def _add_full_clip(
    tl: otio.schema.Timeline,
    media_path: Path,
    duration_sec: float,
    duration_rt: object | None,
    has_audio: bool,
) -> None:
    """Add one full-length clip to V1 (always) and A1 (only when has_audio) tracks.

    Prevents spurious audio tracks when the source media has no audio stream,
    which would cause clipwright-render to incorrectly treat the timeline as
    having audio (has_audio=True on the render side).

    Args:
        tl: Target timeline (new creation; must have V1/A1 tracks per §13.5 DC-AS-001).
        media_path: Source media file path (used for target_url and clip name).
        duration_sec: Duration in seconds derived from inspect_media.
        duration_rt: RationalTimeModel from clipwright.schemas (provides .rate).
        has_audio: True when the source media has at least one audio stream.
    """
    try:
        target_url = str(media_path.resolve())
    except OSError:
        target_url = str(media_path.absolute())

    # duration_rt is a RationalTimeModel from clipwright.schemas (has .rate attribute)
    rate = getattr(duration_rt, "rate", None) or 1000.0

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    ref = otio.schema.ExternalReference(target_url=target_url)

    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Video or (
            track.kind == otio.schema.TrackKind.Audio and has_audio
        ):
            clip = otio.schema.Clip(
                name=media_path.name,
                media_reference=ref,
                source_range=source_range,
            )
            track.append(clip)
