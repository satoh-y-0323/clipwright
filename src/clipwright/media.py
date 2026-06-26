"""media.py — ffprobe wrapper.

Probes a media file with ffprobe and returns a structured MediaInfo.
Delegates ffprobe invocation to process.run, following subprocess discipline (§6.5).
"""

from __future__ import annotations

import json

import clipwright.process as _process_module
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.pathpolicy import validate_source_file as _validate_source_file
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo


def inspect_media(path: str) -> MediaInfo:
    """Probe a media file with ffprobe and return a MediaInfo.

    Execution order: validate input → resolve ffprobe → run subprocess → parse JSON.
    ffprobe is located via the CLIPWRIGHT_FFPROBE env var, then shutil.which (ADR-3).

    Args:
        path: Path to the media file to probe.

    Returns:
        Parsed MediaInfo instance.

    Raises:
        ClipwrightError: File not found (FILE_NOT_FOUND), ffprobe not found
            (DEPENDENCY_MISSING), JSON parse failure (PROBE_FAILED),
            or subprocess failure (SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT).
    """
    _validate_existing_file(path)
    ffprobe = _process_module.resolve_tool("ffprobe", "CLIPWRIGHT_FFPROBE")

    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    result = _process_module.run(cmd, timeout=30.0)
    return _parse_ffprobe_json(path, result.stdout)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_optional_int(val: object) -> int | None:
    """Convert an arbitrary value to int, returning None if conversion is not possible.

    Helper for safely converting field values after JSON parsing (int / float /
    numeric string / None, etc.) to int (L-2: CR-Q-002 / SR-V-001).
    Float strings such as "1.5" are treated as non-convertible and return None.
    bool is a subclass of int, so True→1 / False→0 (existing behaviour).

    Args:
        val: Value to convert.

    Returns:
        Converted int, or None if conversion is not possible.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return int(val)
        except (ValueError, OverflowError):
            return None
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return None
    return None


def _validate_existing_file(path: str) -> None:
    """Verify that a file exists at the given path.

    Thin wrapper over pathpolicy.validate_source_file for backward
    compatibility with existing callers.  Symlinks are rejected across all
    path components (ADR-PP-2).  Raises FILE_NOT_FOUND when the path does
    not point to an existing regular file, PATH_NOT_ALLOWED when any path
    component is a symlink (F-04: SR-V-002).
    """
    _validate_source_file(path)


def _parse_avg_frame_rate(avg_frame_rate: str) -> float:
    """Convert an ffprobe avg_frame_rate string (e.g. "30/1", "24000/1001") to float.

    Returns 0.0 for malformed input so the caller does not treat it as a video stream.
    """
    if "/" in avg_frame_rate:
        parts = avg_frame_rate.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
            if den == 0.0:
                return 0.0
            return num / den
        except ValueError:
            return 0.0
    try:
        return float(avg_frame_rate)
    except ValueError:
        return 0.0


def _parse_ffprobe_json(path: str, stdout: str) -> MediaInfo:
    """Parse ffprobe JSON output into a structured MediaInfo.

    Raises PROBE_FAILED on JSON parse errors or missing required fields.
    Rate determination rules (§13.3 DC-AS-006):
      - If a video stream exists, use avg_frame_rate of the first video stream as rate.
      - Audio-only sources use rate = 1000.0.
    duration.value holds the frame count computed as seconds × rate.

    Args:
        path: Original input file path (stored as MediaInfo.path).
        stdout: JSON string output by ffprobe.

    Returns:
        Parsed MediaInfo instance.

    Raises:
        ClipwrightError: JSON parse failure or missing required fields (PROBE_FAILED).
    """
    if not stdout:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message="ffprobe returned empty output",
            hint="Check that the input file is a valid media file.",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        # Do not expose parser-internal error strings; use a generic message (R3-L-02).
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message="ffprobe output is not valid JSON.",
            hint="Check that the input file is a valid media file.",
        ) from exc

    # Verify required fields
    if "streams" not in data or "format" not in data:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message="ffprobe JSON is missing required fields (streams / format)",
            hint="Check that the input file is a valid media file.",
        )

    raw_streams: list[dict[str, object]] = data["streams"]
    raw_format: dict[str, object] = data["format"]

    # Populate stream info
    streams: list[StreamInfo] = []
    for s in raw_streams:
        codec_name_raw = s.get("codec_name")
        index_raw = s.get("index", 0)

        streams.append(
            StreamInfo(
                index=_to_optional_int(index_raw) or 0,
                codec_type=str(s.get("codec_type", "")),
                codec_name=str(codec_name_raw) if codec_name_raw is not None else None,
                width=_to_optional_int(s.get("width")),
                height=_to_optional_int(s.get("height")),
                sample_rate=_to_optional_int(s.get("sample_rate")),
                channels=_to_optional_int(s.get("channels")),
            )
        )

    # Rate determination (§13.3 DC-AS-006): use avg_frame_rate of first video stream;
    # audio-only defaults to 1000.0.
    rate = 1000.0
    for s in raw_streams:
        if str(s.get("codec_type", "")) == "video":
            avg_frame_rate_raw = s.get("avg_frame_rate", "")
            if avg_frame_rate_raw:
                parsed_rate = _parse_avg_frame_rate(str(avg_frame_rate_raw))
                if parsed_rate > 0.0:
                    rate = parsed_rate
                    break

    # Represent duration as RationalTimeModel
    duration: RationalTimeModel | None = None
    duration_raw = raw_format.get("duration")
    if duration_raw is not None:
        try:
            duration_sec = float(str(duration_raw))
            # value = seconds × rate (frame count equivalent)
            duration = RationalTimeModel(value=duration_sec * rate, rate=rate)
        except (ValueError, TypeError):
            pass

    container = str(raw_format.get("format_name", "")) or None

    return MediaInfo(
        path=path,
        container=container,
        duration=duration,
        streams=streams,
        bit_rate=_to_optional_int(raw_format.get("bit_rate")),
    )
