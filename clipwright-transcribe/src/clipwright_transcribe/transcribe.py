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
import time
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, add_marker, new_timeline, save_timeline
from clipwright.process import resolve_tool, run, safe_subprocess_message
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel, ToolResult

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

# ---------------------------------------------------------------------------
# Backend detection constants (ADR-1' v2 AUTHORITY)
# ---------------------------------------------------------------------------

# Authoritative device signal = whisper.cpp backend-init RESULT lines in stderr.
# Verified against whisper.cpp v1.8.6 (CPU build, Windows):
#   CPU emits "whisper_backend_init_gpu: no GPU found" /
#             "whisper_backend_init_gpu: device 0: CPU (type: 0)".
# CUDA/Metal patterns are best-effort: not observable on a CPU build, sourced from
# whisper.cpp known init messages (ggml_cuda_init / ggml_metal_init).
# Adjust these constants (here only, detection logic unchanged) if a real GPU build
# shows different strings. Device falls back to "unknown" if none match.
#
# WARNING: "use gpu = 1" / "gpu_device = 0" are REQUEST parameters printed even on
# CPU builds ("whisper_init_with_params_no_state: use gpu = 1" etc.).
# They are excluded before matching via _STDERR_EXCLUDE_TOKENS — naive "gpu"/"cuda"
# substring match would misdetect a CPU-only run as GPU (F3, real whisper.cpp v1.8.6).
_BACKEND_STDERR_PATTERNS: dict[str, list[str]] = {
    "cuda": ["ggml_cuda_init", "using cuda"],
    "metal": ["ggml_metal_init", "using metal"],
    "cpu": ["no gpu found", "device 0: cpu", "init_cpu"],
}
# Lines containing any of these tokens are excluded before pattern matching (F3).
_STDERR_EXCLUDE_TOKENS: list[str] = ["use gpu", "gpu_device"]
# Fixed device-label strings used as the base for BackendInfo.detail (ADR-2'/DC-GP-003).
# Using fixed labels prevents raw stderr content (e.g. model absolute paths) from
# leaking into data/summary (CWE-209 / F4).
_DEVICE_DETAIL_LABELS: dict[str, str] = {
    "cuda": "CUDA",
    "metal": "Metal",
    "cpu": "cpu",
    "unknown": "",
}
# Maximum length for the sanitised detail string (CWE-209 / ADR-2').
_DETAIL_MAX_LEN = 80


# ---------------------------------------------------------------------------
# Backend TypedDict / WhisperRun NamedTuple (ADR-3' v2 AUTHORITY)
# ---------------------------------------------------------------------------


class BackendInfo(TypedDict):
    """Detected or inferred whisper backend device and a sanitised label."""

    device: str
    detail: str


class WhisperRun(NamedTuple):
    """Result of a single whisper-cli invocation.

    segments: Normalised caption segments from the transcription.
    language: Detected language code, or None when absent in the JSON result.
    backend: BackendInfo dict with 'device' and 'detail' fields.
    wall_seconds: Monotonic-clock duration of the whisper subprocess only
        (ffmpeg WAV extraction and JSON parse are excluded). Used to derive
        realtime_factor in _transcribe_inner.
    """

    segments: list[Segment]
    language: str | None
    backend: BackendInfo
    wall_seconds: float


# ---------------------------------------------------------------------------
# Backend detection helpers
# ---------------------------------------------------------------------------


def _sanitize_detail(raw: str) -> str:
    """Sanitise a candidate detail string before storing in BackendInfo (CWE-209).

    Applied in sequence:
    1. Strip control characters (\\x00–\\x1f and \\x7f).
    2. Remove whitespace-delimited tokens that contain '/' or '\\' (path tokens).
    3. Truncate to _DETAIL_MAX_LEN characters.
    4. Strip leading/trailing whitespace.

    In production use, 'raw' is always a fixed label from _DEVICE_DETAIL_LABELS
    (which never contains control chars, path separators, or long strings), so this
    function is effectively a no-op on the normal path. It acts as a defense-in-depth
    layer for any future change that feeds raw stderr into 'detail'.
    """
    # 1. Control character strip
    cleaned = "".join(c for c in raw if ord(c) >= 0x20 and ord(c) != 0x7F)
    # 2. Remove path-like tokens (tokens containing '/' or '\\')
    tokens = [tok for tok in cleaned.split() if "/" not in tok and "\\" not in tok]
    cleaned = " ".join(tokens)
    # 3. Length cap
    cleaned = cleaned[:_DETAIL_MAX_LEN]
    # 4. Strip
    return cleaned.strip()


def _detect_backend(
    whisper_json: dict[str, Any], whisper_stderr: str | None
) -> BackendInfo:
    """Detect the whisper backend device from stderr (stderr-only; ADR-1' v2 AUTHORITY).

    Detection is stderr-only; systeminfo is not used for device determination (F1).
    Priority: CUDA -> Metal -> CPU -> unknown.

    CPU patterns are verified against real whisper.cpp v1.8.6 (CPU build, Windows).
    CUDA/Metal patterns are best-effort (see _BACKEND_STDERR_PATTERNS comment).

    Lines containing "use gpu" or "gpu_device" are excluded before matching to avoid
    the F3 trap (these request-parameter lines appear on CPU builds too).

    Any exception is caught and {"device": "unknown", "detail": ""} is returned
    (NFR-3: backend detection must never propagate exceptions to the caller).

    Args:
        whisper_json: Parsed whisper JSON output (not used for device determination;
            retained for future extension without a signature change).
        whisper_stderr: Captured stderr text from the whisper subprocess, or None.

    Returns:
        BackendInfo with 'device' in {"cuda", "metal", "cpu", "unknown"} and a
        sanitised 'detail' label.
    """
    try:
        if not isinstance(whisper_json, dict):
            return BackendInfo(device="unknown", detail="")
        if not isinstance(whisper_stderr, str):
            return BackendInfo(device="unknown", detail="")
        if not whisper_stderr:
            return BackendInfo(device="unknown", detail="")

        # Filter lines that contain exclude tokens (F3: request-parameter lines)
        lines = [
            line
            for line in whisper_stderr.splitlines()
            if not any(token in line.lower() for token in _STDERR_EXCLUDE_TOKENS)
        ]
        combined = "\n".join(lines).lower()

        # Determine device in priority order: CUDA -> Metal -> CPU -> unknown
        device = "unknown"
        for candidate in ("cuda", "metal", "cpu"):
            patterns = _BACKEND_STDERR_PATTERNS[candidate]
            if any(pat in combined for pat in patterns):
                device = candidate
                break

        detail = _sanitize_detail(_DEVICE_DETAIL_LABELS[device])
        return BackendInfo(device=device, detail=detail)
    except Exception:  # noqa: BLE001
        return BackendInfo(device="unknown", detail="")


def _compute_realtime_factor(
    total_duration_sec: float, wall_seconds: float
) -> float | None:
    """Compute the realtime factor: media container duration / whisper wall time.

    A value > 1 means transcription was faster than real time (typical for GPU).
    Returns None when wall_seconds is non-positive (clock resolution below measurement
    threshold, or invalid measurement) — NOT 0.0, to avoid an AI reading "0x realtime"
    as "effectively not running" (DC-AM-001).

    The numerator is the container duration as reported by ffprobe, which can differ
    slightly from the actual WAV length fed to whisper (DC-AS-005).
    None means "not measurable" (not slow).

    Args:
        total_duration_sec: Media container duration in seconds (ffprobe-derived).
        wall_seconds: Monotonic-clock duration of the whisper subprocess only.

    Returns:
        Realtime factor rounded to 2 decimal places, or None if wall_seconds <= 0.
    """
    if wall_seconds <= 0:
        return None
    return round(total_duration_sec / wall_seconds, 2)


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
) -> WhisperRun:
    """Single adapter: ffmpeg WAV extraction -> whisper-cli -> JSON normalisation
    (TR-AD-01).

    Replace only this function to swap the backend (e.g. faster-whisper).
    WAV and JSON are written to a temporary directory so the source media directory
    is not polluted.

    The whisper subprocess invocation is timed with time.monotonic(); ffmpeg WAV
    extraction and JSON parse/normalise are excluded from the measured interval
    (DC-AS-001/DC-AS-003).

    Args:
        media: Absolute path to the input media file.
        options: TranscribeOptions.
        total_duration_sec: Total duration of the media (seconds); used to compute
            timeouts.
        model_path: Resolved model file path.

    Returns:
        WhisperRun with normalised Segment list, detected language code or None,
        BackendInfo (device/detail), and the whisper subprocess wall time in seconds.

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

        # ffmpeg WAV extraction is outside the timing interval (DC-AS-001/003).
        try:
            _extract_wav(ffmpeg, media, wav_path, ffmpeg_timeout)
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from None

        cmd = _build_whisper_cmd(whisper, model_path, wav_path, prefix, options)
        # Time only the whisper subprocess (not ffmpeg or JSON parse; DC-AS-001/003).
        start = time.monotonic()
        try:
            whisper_result = run(cmd, timeout=whisper_timeout)
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from None
        wall_seconds = time.monotonic() - start

        # whisper `-oj -of <prefix>` produces <prefix>.json (DC-AM-003).
        # JSON read and normalise are outside the timing interval (DC-AS-003).
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

        # Backend detection uses whisper stderr (stderr-only; ADR-1' v2 AUTHORITY).
        backend = _detect_backend(whisper_json, whisper_result.stderr)

    return WhisperRun(segments, language, backend, wall_seconds)


def transcribe_media(
    media: str,
    output: str,
    options: TranscribeOptions,
) -> ToolResult:
    """Transcribe audio and produce SRT/VTT captions and an OTIO timeline (TR-AD-04).

    Non-destructive: the input media file is never modified.
    Outputs are newly created files; their paths are returned in artifacts.

    Args:
        media: Input media file path (audio required; video optional).
        output: Output timeline.otio file path (must be in the same directory as media).
        options: TranscribeOptions.

    Returns:
        ToolResult envelope (ok_result or error_result).
    """
    try:
        return _transcribe_inner(media, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _transcribe_inner(
    media: str,
    output: str,
    options: TranscribeOptions,
) -> ToolResult:
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

    whisper_run = _run_whisper(abs_media, options, total_duration_sec, model_path)

    # Language priority: detected result > explicit option > unknown
    language = whisper_run.language or options.language or "unknown"

    segments = whisper_run.segments

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
    realtime_factor = _compute_realtime_factor(
        total_duration_sec, whisper_run.wall_seconds
    )
    device = whisper_run.backend["device"]

    summary = (
        f"Language {language} · {segment_count} segment(s) · "
        f"total duration {_fmt_sec(total_duration_sec)} transcribed. "
        f"Generated {srt_path.name} / {vtt_path.name} / {output_path.name}."
    )
    # Append backend info; realtime_factor=None means wall time was unmeasurable
    # (DC-AM-001 / DC-AM-004: leading space + period-terminated).
    if realtime_factor is not None:
        summary += f" Backend: {device} ({realtime_factor}x realtime)."
    else:
        summary += f" Backend: {device}."

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
            # Additive backend fields (NFR-2: existing fields unchanged)
            "backend": {
                "device": whisper_run.backend["device"],
                "detail": whisper_run.backend["detail"],
            },
            "whisper_wall_seconds": round(whisper_run.wall_seconds, 3),
            "realtime_factor": realtime_factor,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
            {"role": "captions", "path": str(srt_path), "format": "srt"},
            {"role": "captions", "path": str(vtt_path), "format": "vtt"},
        ],
        warnings=warnings,
    )
