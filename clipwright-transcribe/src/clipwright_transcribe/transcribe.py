"""transcribe.py — clipwright-transcribe orchestration layer (mirrors detect.py).

Flow: input validation -> inspect_media -> model resolution -> ffmpeg WAV extraction ->
whisper-cli execution -> SRT/VTT generation via captions -> OTIO construction/save ->
envelope return.

Design decisions:
- _run_whisper() is the single adapter function (TR-AD-01) that encapsulates ffmpeg WAV
  extraction, whisper-cli invocation, and JSON loading. To swap backends
  (e.g. faster-whisper), replace only this function.
- The whisper binary name and language auto-detect flag are isolated as module constants
  (spike-whisper confirmed values, replaceable via e2e; DC-AS-003/DC-AM-002).
- Model resolution uses os.path.isfile rather than resolve_tool (the model is not an
  executable; DC-AS-003). Resolution order: options.model_path -> env
  CLIPWRIGHT_WHISPER_MODEL.
- marker.marked_range uses whisper second values (media coordinates) directly.
  Coordinates match because the clip is full-length with source_range.start_time=0
  (DC-AM-001).
- SRT/VTT timecodes and marker second values share the same origin (DC-AS-005).
- Error messages expose only basename; raw whisper/ffmpeg stderr fragments are replaced
  with a sanitised generic message (TR-AD-09, following VAD M-1 precedent).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, add_marker, new_timeline, save_timeline
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

import clipwright_transcribe
from clipwright_transcribe.captions import Segment, normalize_segments, to_srt, to_vtt
from clipwright_transcribe.schemas import TranscribeOptions

# whisper.cpp executable name (spike-whisper confirmed; latest = whisper-cli,
# legacy = main).
# Must match the binary name pointed to by env CLIPWRIGHT_WHISPER (DC-AS-003-R).
# Verified by e2e test.
WHISPER_BINARY_NAME = "whisper-cli"
# Language auto-detect flag (spike confirmed; replaceable via e2e as a list; DC-AM-002)
LANG_AUTO_FLAG: list[str] = ["-l", "auto"]
# Maximum display length for marker name (excess is truncated; full text kept in
# metadata.text; DC-GP-003).
_MARKER_NAME_MAX = 40
# Hint shown when the whisper model cannot be resolved (actionable; TR-AD-05).
_MODEL_MISSING_HINT = (
    "Specify the ggml model file path via options.model_path or "
    "set the CLIPWRIGHT_WHISPER_MODEL environment variable. "
    "Download the model from the whisper.cpp distribution (e.g. ggml-base.bin)."
)


def _fmt_sec(sec: float) -> str:
    """Convert seconds to a human-readable "Xm Ys" string (used in summary)."""
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}m{s:.1f}s" if m > 0 else f"{s:.1f}s"


def _truncate_name(text: str) -> str:
    """Truncate text to the first _MARKER_NAME_MAX characters for use as a marker name
    (DC-GP-003).

    Appends an ellipsis "…" when truncated. The full text is preserved in metadata.text.
    """
    if len(text) <= _MARKER_NAME_MAX:
        return text
    return text[:_MARKER_NAME_MAX] + "…"


def _sanitize_subprocess_error(exc: ClipwrightError) -> ClipwrightError:
    """Replace SUBPROCESS_FAILED/TIMEOUT message with a generic string (TR-AD-09).

    run() messages may contain stderr fragments and executable paths; this function
    substitutes a fixed string to prevent leakage into MCP responses. hint is
    preserved. Other error codes are returned unchanged.
    """
    if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
        return ClipwrightError(
            code=exc.code,
            message=safe_subprocess_message(exc),
            hint=exc.hint,
        )
    return exc


def _resolve_model_path(options: TranscribeOptions) -> str:
    """Resolve the whisper model file path (DC-AS-003).

    Resolution order: options.model_path -> env CLIPWRIGHT_WHISPER_MODEL.
    Uses os.path.isfile rather than resolve_tool (the model is not an executable).
    Raises DEPENDENCY_MISSING when neither candidate exists.

    Args:
        options: TranscribeOptions (model_path field is inspected).

    Returns:
        Absolute or relative path to an existing model file.

    Raises:
        ClipwrightError: When the model file cannot be found (DEPENDENCY_MISSING).
    """
    candidates: list[str] = []
    if options.model_path is not None:
        candidates.append(options.model_path)
    env_model = os.environ.get("CLIPWRIGHT_WHISPER_MODEL")
    if env_model is not None:
        candidates.append(env_model)

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    raise ClipwrightError(
        code=ErrorCode.DEPENDENCY_MISSING,
        message="whisper model file not found",
        hint=_MODEL_MISSING_HINT,
    )


def _extract_wav(ffmpeg: str, media: str, output_path: str, timeout: float) -> None:
    """Extract a 16 kHz mono s16le WAV to a temporary file using ffmpeg (TR-AD-01).

    whisper.cpp requires 16 kHz mono WAV; this conversion satisfies that requirement.
    Executed with shell=False and an argument list (subprocess discipline).
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        media,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-y",
        output_path,
    ]
    run(cmd, timeout=timeout)


def _build_whisper_cmd(
    whisper: str,
    model_path: str,
    wav_path: str,
    prefix: str,
    options: TranscribeOptions,
) -> list[str]:
    """Build the whisper-cli argument list (TR-AD-01, DC-AM-002/003).

    `-oj` writes JSON to `<prefix>.json`. Language None uses auto-detection
    (LANG_AUTO_FLAG); an explicit code uses `-l <code>`. initial_prompt is passed
    via `--prompt`.
    """
    cmd = [whisper, "-m", model_path, "-f", wav_path, "-oj", "-of", prefix]
    if options.language is None:
        cmd.extend(LANG_AUTO_FLAG)
    else:
        cmd.extend(["-l", options.language])
    if options.initial_prompt is not None:
        cmd.extend(["--prompt", options.initial_prompt])
    return cmd


def _run_whisper(
    media: str,
    options: TranscribeOptions,
    total_duration_sec: float,
    model_path: str,
) -> tuple[list[Segment], str | None]:
    """Single adapter: ffmpeg WAV extraction -> whisper-cli -> JSON normalisation
    (TR-AD-01).

    Replace only this function to swap the backend (e.g. faster-whisper).
    WAV and JSON are written to a temporary directory so the source media directory
    is not polluted.

    Args:
        media: Absolute path to the input media file.
        options: TranscribeOptions.
        total_duration_sec: Total duration of the media (seconds); used to compute
            timeouts.
        model_path: Resolved model file path.

    Returns:
        Tuple of (normalised Segment list, detected language code or None).

    Raises:
        ClipwrightError: DEPENDENCY_MISSING (missing tool), sanitised
            SUBPROCESS_FAILED/SUBPROCESS_TIMEOUT, or SUBPROCESS_FAILED on JSON parse
            failure.
    """
    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    whisper = resolve_tool(WHISPER_BINARY_NAME, "CLIPWRIGHT_WHISPER")

    # Timeouts scale with duration; whisper is computationally expensive (TR-AD-10).
    ffmpeg_timeout = float(max(60, math.ceil(total_duration_sec * 2)))
    whisper_timeout = float(max(300, math.ceil(total_duration_sec * 30)))

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")
        prefix = os.path.join(tmpdir, "transcript")

        try:
            _extract_wav(ffmpeg, media, wav_path, ffmpeg_timeout)
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from None

        cmd = _build_whisper_cmd(whisper, model_path, wav_path, prefix, options)
        try:
            run(cmd, timeout=whisper_timeout)
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from None

        # whisper `-oj -of <prefix>` produces <prefix>.json (DC-AM-003).
        json_path = prefix + ".json"
        try:
            with open(json_path, encoding="utf-8") as f:
                whisper_json: dict[str, Any] = json.load(f)
        except (OSError, json.JSONDecodeError):
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="failed to read whisper output JSON",
                hint=(
                    "Check the whisper.cpp version and arguments. "
                    "Please report with reproduction steps."
                ),
            ) from None

        # Complete JSON loading and normalisation inside the with block so that the
        # temporary directory still exists while data is accessed (CR M-2).
        segments = normalize_segments(whisper_json)
        result = whisper_json.get("result")
        language = result.get("language") if isinstance(result, dict) else None

    return segments, language


def transcribe_media(
    media: str,
    output: str,
    options: TranscribeOptions,
) -> dict[str, Any]:
    """Transcribe audio and produce SRT/VTT captions and an OTIO timeline (TR-AD-04).

    Non-destructive: the input media file is never modified.
    Outputs are newly created files; their paths are returned in artifacts.

    Args:
        media: Input media file path (audio required; video optional).
        output: Output timeline.otio file path (must be in the same directory as media).
        options: TranscribeOptions.

    Returns:
        ok_result or error_result envelope dict.
    """
    try:
        return _transcribe_inner(media, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _transcribe_inner(
    media: str,
    output: str,
    options: TranscribeOptions,
) -> dict[str, Any]:
    """Internal implementation of transcribe_media. Raises ClipwrightError directly."""
    output_path = Path(output)
    media_path = Path(media)

    # --- 1. Output validation ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"Invalid output file extension: {output_path.suffix!r}. "
                "Only .otio is allowed."
            ),
            hint="Change the output file path extension to .otio.",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "Output directory does not exist. "
                "Check the parent directory of the specified output path."
            ),
            hint="Create the output directory before running again.",
        )

    try:
        if output_path.resolve() == media_path.resolve():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Output path and input media path are identical.",
                hint="Change the output file path to differ from the input media.",
            )
    except OSError as exc:
        if str(output_path) == str(media_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Output path and input media path are identical.",
                hint="Change the output file path to differ from the input media.",
            ) from exc

    # --- 2. inspect_media -> stream and duration check ---

    # Replace FILE_NOT_FOUND message with basename only (TR-AD-09; no full path
    # exposure).
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

    # Verify that output is in the same directory as media (TR-AD-08).
    # ClipwrightError propagates; OSError is skipped best-effort.
    try:
        media_dir = media_path.resolve().parent
        output_dir = output_path.parent.resolve()
        if media_dir != output_dir:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    "Output timeline must be placed in the same directory as "
                    f"the input media (input: {media_path.name})."
                ),
                hint=(
                    "Change the output path to a location inside the same directory as "
                    "the media file."
                ),
            )
    except OSError:
        # resolve() may fail for network paths; skip best-effort.
        pass

    # Audio stream check (TR-AD-03). Video is optional (audio-only sources accepted).
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)
    if not has_audio:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"No audio stream found: {media_path.name}",
            hint=(
                "Transcription requires an audio stream. "
                "Provide a media file that contains audio."
            ),
        )

    # Duration check
    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message=f"Could not retrieve media duration: {media_path.name}",
            hint=(
                "Check that the media file is not corrupted. "
                "You can also inspect it manually with ffprobe."
            ),
        )

    total_duration_sec = media_info.duration.value / media_info.duration.rate
    rate = media_info.duration.rate
    abs_media = str(media_path.resolve())

    # --- 3. Model resolution (DC-AS-003) ---

    model_path = _resolve_model_path(options)

    # --- 4. whisper execution (adapter) ---

    segments, detected_language = _run_whisper(
        abs_media, options, total_duration_sec, model_path
    )

    # Language priority: detected result > explicit option > unknown
    language = detected_language or options.language or "unknown"

    # --- 5. SRT/VTT generation and write (TR-AD-08) ---

    srt_path = output_path.with_suffix(".srt")
    vtt_path = output_path.with_suffix(".vtt")
    srt_path.write_text(to_srt(segments), encoding="utf-8")
    vtt_path.write_text(to_vtt(segments), encoding="utf-8")

    # --- 6. OTIO construction and save (TR-AD-04/DC-AM-001/DC-AM-101) ---

    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]  # V1 (Video) track

    # Full-length single clip (source_range.start_time=0)
    full_source_range = TimeRangeModel(
        start_time=RationalTimeModel(value=0.0, rate=rate),
        duration=RationalTimeModel(value=media_info.duration.value, rate=rate),
    )
    add_clip(
        v1,
        MediaRef(target_url=abs_media),
        full_source_range,
        name=media_path.name,
        metadata={
            "tool": "clipwright-transcribe",
            "version": clipwright_transcribe.__version__,
            "kind": "transcript-source",
        },
    )

    # Attach each segment as a marker on the V1 track (DC-AM-101).
    # marked_range uses whisper second values directly (media coord = track coord;
    # DC-AM-001).
    for seg in segments:
        start_value = seg["start_sec"] * rate
        dur_value = (seg["end_sec"] - seg["start_sec"]) * rate
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_value, rate=rate),
            duration=RationalTimeModel(value=dur_value, rate=rate),
        )
        add_marker(
            item=v1,
            marked_range=marked_range,
            name=_truncate_name(seg["text"]),
            metadata={
                "tool": "clipwright-transcribe",
                "version": clipwright_transcribe.__version__,
                "kind": "caption",
                "text": seg["text"],
                "language": language,
            },
        )

    save_timeline(timeline, output)

    # --- 7. Return envelope ---

    segment_count = len(segments)
    summary = (
        f"Language {language} · {segment_count} segment(s) · "
        f"total duration {_fmt_sec(total_duration_sec)} transcribed. "
        f"Generated {srt_path.name} / {vtt_path.name} / {output_path.name}."
    )

    warnings: list[str] = []
    if segment_count == 0:
        warnings.append(
            "No transcription segments found "
            "(possible silence or recognition failure). "
            "SRT is empty, VTT has header only, no markers were added. "
            "The timeline contains only the full-length clip."
        )

    return ok_result(
        summary,
        data={
            "segment_count": segment_count,
            "language": language,
            "total_duration_seconds": total_duration_sec,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
            {"role": "captions", "path": str(srt_path), "format": "srt"},
            {"role": "captions", "path": str(vtt_path), "format": "vtt"},
        ],
        warnings=warnings,
    )
