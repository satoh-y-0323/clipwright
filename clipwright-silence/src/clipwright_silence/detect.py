"""detect.py — clipwright-silence orchestration layer.

Handles the full flow: input validation -> inspect_media -> silencedetect
execution/parsing -> KEEP derivation -> OTIO construction/save -> envelope return.

Design decisions:
- _detect_silence_intervals() encapsulates ffmpeg execution and stderr parsing,
  allowing future backend replacement (adapter abstraction, AD-1).
- _detect_vad_silence_intervals() handles spawning the VAD CLI as a separate
  process and inverting speech -> silence. Both return (silence interval list)
  with a common contract so derive_keep_ranges onward uses a shared flow.
- source_range rate is taken from inspect_media MediaInfo.duration.rate,
  and value = seconds * rate (DC-AS-003).
- output is only permitted in the same directory as media (DC-AS-001).
- Error messages do not expose full paths or raw ffmpeg stderr: FILE_NOT_FOUND
  uses basename only (M-1 / SR L-2). SUBPROCESS_FAILED/TIMEOUT from both the
  silencedetect run() seam and the VAD CLI spawn run() seam are replaced with
  SUBPROCESS_SAFE_MESSAGE from clipwright.process (SR L-1 [SR-R-001]), so core
  subprocess stderr never reaches the MCP error envelope on either path.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

import clipwright_silence
from clipwright_silence.plan import derive_keep_ranges
from clipwright_silence.schemas import DetectSilenceOptions

# Regex to extract silence_start / silence_end lines
# (DC-AM-003: line-start match, '.' fixed decimal)
_RE_SILENCE_START = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_RE_SILENCE_END = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


def _fmt_sec(sec: float) -> str:
    """Convert seconds to a human-readable minutes/seconds string (for summary).

    Format examples: 90.0 -> "1m30.0s", 45.5 -> "45.5s"
    """
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}m{s:.1f}s" if m > 0 else f"{s:.1f}s"


def _parse_silence_intervals(
    stderr: str,
    total_duration_sec: float,
) -> list[tuple[float, float]]:
    """Extract silence interval list from silencedetect stderr.

    Parses line by line using a line-start match regex with fixed '.'
    decimal (DC-AM-003). A trailing silence_start with no matching
    silence_end is completed using
    total_duration_sec (DC-AM-002).

    Args:
        stderr: ffmpeg standard error output string.
        total_duration_sec: Total duration of the source (seconds). Used for completion.

    Returns:
        List of silence intervals. Each element is a (start_sec, end_sec) tuple.
    """
    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None

    for line in stderr.splitlines():
        m_start = _RE_SILENCE_START.search(line)
        if m_start:
            pending_start = float(m_start.group(1))
            continue

        m_end = _RE_SILENCE_END.search(line)
        # An isolated silence_end does not occur in normal silencedetect output
        # (start->end are always paired). If encountered, skip as abnormal output.
        # This is an intentional ignore per silencedetect spec, not suppression.
        if m_end and pending_start is not None:
            end = float(m_end.group(1))
            # SR L-3: skip abnormal intervals where end < start
            # (defensive check for future backend replacement)
            if end < pending_start:
                pending_start = None
                continue
            intervals.append((pending_start, end))
            pending_start = None

    # Trailing silence_end missing -> complete with total_duration (DC-AM-002)
    if pending_start is not None:
        intervals.append((pending_start, total_duration_sec))

    return intervals


def _detect_silence_intervals(
    ffmpeg: str,
    source: str,
    options: DetectSilenceOptions,
    total_duration_sec: float,
) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return a list of silence intervals.

    Adapter abstraction (AD-1).

    When replacing the backend in the future, only this function needs to be swapped.

    Args:
        ffmpeg: Path to ffmpeg executable.
        source: Input media file path.
        options: DetectSilenceOptions.
        total_duration_sec: Total duration of source (seconds).
            Used for trailing completion.

    Returns:
        List of silence intervals. Each element is (start_sec, end_sec).

    Raises:
        ClipwrightError: SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT (raised by run).
    """
    # Filter string uses explicit format to be locale-independent (DC-AM-003)
    filter_str = (
        f"silencedetect=noise={options.silence_threshold_db:.3f}dB"
        f":d={options.min_silence_duration:.6f}"
    )
    timeout = max(60, math.ceil(total_duration_sec * 2))

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        source,
        "-af",
        filter_str,
        "-f",
        "null",
        "-",
    ]
    try:
        result = run(cmd, timeout=float(timeout))
    except ClipwrightError as exc:
        # SR L-1 [SR-R-001]: core run() builds SUBPROCESS_FAILED/TIMEOUT messages
        # from raw ffmpeg stderr, which can embed absolute input paths. Replace
        # with a generic message before it reaches the MCP error envelope,
        # mirroring the VAD branch (vad_cli.py:265). exc.code and exc.hint are
        # preserved so callers retain the failure category and remediation hint.
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            raise ClipwrightError(
                code=exc.code,
                message=safe_subprocess_message(exc),
                hint=exc.hint,
            ) from exc
        raise
    return _parse_silence_intervals(result.stderr, total_duration_sec)


def _detect_vad_silence_intervals(
    source: str,
    options: DetectSilenceOptions,
    total_duration_sec: float,
) -> tuple[list[tuple[float, float]], int]:
    """Spawn VAD CLI as a separate process; return silence intervals and speech_count.

    VAD-AD-02/04: Inverts the speech intervals returned by the VAD CLI against
    total_duration_sec to produce silence intervals. speech_count is used only
    for VAD summary generation and is not passed to the common flow (§7.5).

    Args:
        source: Absolute path to the input media file.
        options: DetectSilenceOptions (references vad_* fields).
        total_duration_sec: Total duration of source (seconds). Used for inversion.

    Returns:
        Tuple of (silence interval list, speech_count).

    Raises:
        ClipwrightError: Maps VAD CLI error JSON to corresponding ErrorCode.
                         SUBPROCESS_FAILED if run() exits non-zero.
    """
    timeout = float(max(60, math.ceil(total_duration_sec * 4)))
    cmd = [
        sys.executable,
        "-m",
        "clipwright_silence.vad_cli",
        "--media",
        source,
        "--threshold",
        f"{options.vad_threshold}",
        "--min-speech",
        f"{options.vad_min_speech_duration}",
        "--min-silence",
        f"{options.vad_min_silence_duration}",
        "--media-duration",
        # vad_cli uses ceil(total*2); float precision has no practical impact (NF-L-1)
        f"{total_duration_sec}",
    ]
    try:
        result = run(cmd, timeout=timeout)
    except ClipwrightError as exc:
        # SR L-1 [SR-R-001] symmetry: the VAD CLI spawn run() can raise
        # SUBPROCESS_FAILED/TIMEOUT on catastrophic spawn failure (before
        # vad_cli.main() starts). Sanitise with the shared helper so raw
        # subprocess stderr never reaches the MCP error envelope, mirroring
        # the silencedetect seam above. exc.code and exc.hint are preserved.
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            raise ClipwrightError(
                code=exc.code,
                message=safe_subprocess_message(exc),
                hint=exc.hint,
            ) from exc
        raise

    try:
        payload: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message="VAD CLI returned invalid JSON output",
            hint=(
                "VAD CLI did not return the expected JSON. "
                "If the error persists, please report with reproduction steps."
            ),
        ) from exc

    # If error JSON, map to ErrorCode and raise ClipwrightError (§7.1)
    if "error" in payload:
        err = payload["error"]
        raw_code: str = err.get("code", "INTERNAL")
        message: str = err.get("message", "An error occurred in VAD CLI")
        hint: str = err.get("hint", "Please report with reproduction steps.")

        # Map to known ErrorCode; fall back to SUBPROCESS_FAILED for unknown codes
        try:
            error_code = ErrorCode(raw_code)
        except ValueError:
            error_code = ErrorCode.SUBPROCESS_FAILED

        raise ClipwrightError(code=error_code, message=message, hint=hint)

    # Pre-process speech intervals (§7.4): clip and remove degenerate intervals
    raw_segments: list[dict[str, Any]] = payload.get("speech_segments", [])
    total = total_duration_sec
    speech_segments: list[tuple[float, float]] = []
    for seg in raw_segments:
        try:
            # Accept both dict {"start": ..., "end": ...} and list [start, end] formats
            if isinstance(seg, (list, tuple)):
                start, end = float(seg[0]), float(seg[1])
            else:
                start, end = float(seg["start"]), float(seg["end"])
        except (TypeError, KeyError, ValueError, IndexError):
            # Skip malformed elements (null/string/empty dict, etc.) — SR L-3
            continue
        # Clip start < 0 to 0, clip end > total to total
        start = max(0.0, start)
        end = min(total, end)
        # Remove degenerate intervals (start >= end)
        if start >= end:
            continue
        speech_segments.append((start, end))

    speech_count = len(speech_segments)

    # Invert speech intervals -> silence intervals (VAD-AD-04)
    # Sort speech intervals ascending and take the complement of [0, total]
    sorted_speech = sorted(speech_segments, key=lambda iv: iv[0])
    silence_intervals: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted_speech:
        if s_start > cursor:
            silence_intervals.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < total:
        silence_intervals.append((cursor, total))

    return silence_intervals, speech_count


def detect_silence(
    media: str,
    output: str,
    options: DetectSilenceOptions,
) -> dict[str, Any]:
    """Detect silence intervals and generate a KEEP interval OTIO timeline (AD-2/AD-5).

    Non-destructive: does not modify the input media file in any way.
    Output returns the path of the newly created timeline.otio in artifacts.

    Flow:
      1. Output validation (extension, parent directory, output==media, same directory)
      2. inspect_media -> verify audio/video streams and duration
      3. Run ffmpeg silencedetect and parse stderr
      4. Derive KEEP intervals with derive_keep_ranges
      5. Build and save OTIO timeline
      6. Return envelope

    Args:
        media: Input media file path.
        output: Output timeline.otio file path (must be in the same directory as media).
        options: DetectSilenceOptions.

    Returns:
        Envelope dict from ok_result or error_result.
    """
    try:
        return _detect_inner(media, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint).model_dump()


def _detect_inner(
    media: str,
    output: str,
    options: DetectSilenceOptions,
) -> dict[str, Any]:
    """Internal implementation of detect_silence. Raises ClipwrightError directly."""
    output_path = Path(output)
    media_path = Path(media)

    # --- 1. Output validation ---

    # Extension must be .otio (AD-5)
    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Invalid output file extension: {output_path.suffix!r}. "
                "Only .otio is allowed."
            ),
            hint="Change the output file path extension to .otio.",
        )

    # Verify parent directory exists (no auto-creation, AD-5)
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "The output directory does not exist. "
                "Check the parent directory of the specified output path."
            ),
            hint="Create the output directory first, then re-run.",
        )

    # Prevent output == media (avoid overwriting the same path)
    try:
        if output_path.resolve() == media_path.resolve():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="The output path and input media path are identical.",
                hint=(
                    "Change the output file path to a path"
                    " different from the input media."
                ),
            )
    except OSError as exc:
        if str(output_path) == str(media_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="The output path and input media path are identical.",
                hint=(
                    "Change the output file path to a path"
                    " different from the input media."
                ),
            ) from exc

    # --- 2. inspect_media -> verify streams and duration ---

    # inspect_media raises FILE_NOT_FOUND (incl. symlink rejection) / PROBE_FAILED, etc.
    # SR L-2: Replace FILE_NOT_FOUND message with basename only
    # (prevents full path exposure; same policy as render._probe M-1).
    try:
        media_info = inspect_media(media)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {media_path.name}",
                hint=exc.hint,
            ) from exc
        raise

    # Verify output is in the same directory as media (DC-AS-001)
    # Done after inspect_media so the path has been confirmed to exist before resolve()
    try:
        media_dir = media_path.resolve().parent
        output_dir = output_path.parent.resolve()
        if media_dir != output_dir:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "The output timeline must be placed in the same"
                    f" directory as the input media (input: {media_path.name})."
                ),
                hint=(
                    "Change the output path to be in the same directory"
                    " as the media file."
                    " (e.g., output = same directory as media / timeline.otio)"
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve failure (network paths, etc.) is skipped on best-effort basis
        pass

    # Verify video stream (DC-AS-002)
    has_video = any(s.codec_type == "video" for s in media_info.streams)
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No video stream found: {media_path.name}",
            hint=(
                "This tool targets media with both video and audio streams. "
                "Specify a media file that contains video."
            ),
        )

    if not has_audio:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No audio stream found: {media_path.name}",
            hint=(
                "Silence detection requires an audio stream. "
                "Specify a media file that contains audio."
            ),
        )

    # Verify duration (DC-AS-004)
    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message=f"Could not retrieve media duration: {media_path.name}",
            hint=(
                "Check that the media file is not corrupted. "
                "You can also verify manually with ffprobe."
            ),
        )

    total_duration_sec = media_info.duration.value / media_info.duration.rate
    rate = media_info.duration.rate

    # --- 3. Run detection (branch by backend) ---

    abs_media = str(media_path.resolve())

    # speech_count is for VAD summary only; silence interval list is shared
    speech_count: int | None = None

    if options.backend == "vad":
        # VAD path: spawn as separate process via sys.executable -m
        # resolve_tool is not used (sys.executable -m ensures same venv, VAD-AD-02)
        silence_intervals, speech_count = _detect_vad_silence_intervals(
            abs_media, options, total_duration_sec
        )
    else:
        # silencedetect path (existing; backend="silencedetect")
        ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
        silence_intervals = _detect_silence_intervals(
            ffmpeg, abs_media, options, total_duration_sec
        )

    # --- 4. Derive KEEP intervals ---

    keep_ranges = derive_keep_ranges(total_duration_sec, silence_intervals, options)

    # --- 5. Build and save OTIO timeline ---

    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]  # V1 (Video) track

    for start_sec, end_sec in keep_ranges:
        start_value = start_sec * rate
        dur_value = (end_sec - start_sec) * rate
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_value, rate=rate),
            duration=RationalTimeModel(value=dur_value, rate=rate),
        )
        media_ref = MediaRef(target_url=abs_media)
        add_clip(
            v1,
            media_ref,
            source_range,
            name="keep",
            metadata={
                "tool": "clipwright-silence",
                "version": clipwright_silence.__version__,
                "kind": "keep",
                "backend": options.backend,  # VAD-AD-07
            },
        )

    save_timeline(timeline, output)

    # --- 6. Return envelope ---

    silence_count = len(silence_intervals)
    keep_count = len(keep_ranges)
    total_silence_seconds = sum(e - s for s, e in silence_intervals)
    total_keep_seconds = sum(e - s for s, e in keep_ranges)

    # Differentiate summary by backend (VAD-AD-08, §7.5)
    # In the VAD path, _detect_vad_silence_intervals always returns an int,
    # so speech_count is guaranteed to be int (assert to satisfy mypy)
    if options.backend == "vad":
        assert speech_count is not None  # Always set on the VAD path
        _silence_fmt = _fmt_sec(total_silence_seconds)
        _keep_fmt = _fmt_sec(total_keep_seconds)
        summary = (
            f"Detected {speech_count} speech interval(s). "
            f"Removed {silence_count} non-speech interval(s) (total {_silence_fmt}). "
            f"Generated {output_path.name} with {keep_count} interval(s) to keep"
            f" (total {_keep_fmt})."
        )
    else:
        _silence_fmt = _fmt_sec(total_silence_seconds)
        _keep_fmt = _fmt_sec(total_keep_seconds)
        summary = (
            f"Detected {silence_count} silence interval(s) (total {_silence_fmt})"
            f" from source with duration {_fmt_sec(total_duration_sec)}. "
            f"Generated {output_path.name} with {keep_count} interval(s) to keep"
            f" (total {_keep_fmt})."
        )

    warnings: list[str] = []
    if keep_count == 0:
        warnings.append(
            "No intervals to keep (all intervals classified as silence). "
            "The V1 track of the generated timeline.otio is empty. "
            "Passing it to render will result in INVALID_INPUT."
        )

    return ok_result(
        summary,
        data={
            "silence_count": silence_count,
            "total_silence_seconds": total_silence_seconds,
            "keep_count": keep_count,
            "total_keep_seconds": total_keep_seconds,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=warnings,
    ).model_dump()
