"""captions.py — clipwright-transcribe pure logic layer (mirrors plan.py structure).

Converts whisper.cpp `-oj` JSON (transcription[].offsets.from/to in ms, text) into
normalised segments and generates SRT/VTT strings.

Design decisions:
- Pure functions; no external processes are executed (target: 100% contract coverage).
- SRT/VTT timecodes are derived from the same second value to guarantee consistency
  (DC-AS-005). Only the separator differs (SRT="HH:MM:SS,mmm" / VTT="HH:MM:SS.mmm").
- When segments is empty, to_srt returns an empty string and to_vtt returns only the
  "WEBVTT" header (DC-GP-002).
- Defensive handling of whisper output: removes entries with empty text, degenerate
  intervals (start>=end), or missing keys.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Segment(TypedDict):
    """Normalised caption segment.

    start_sec / end_sec are in seconds (float); text has leading/trailing whitespace
    stripped.
    """

    start_sec: float
    end_sec: float
    text: str


def normalize_segments(whisper_json: dict[str, Any]) -> list[Segment]:
    """Convert a whisper `-oj` JSON dict into normalised segments.

    Converts transcription[].offsets.from/to (milliseconds) to seconds and strips
    text whitespace.
    Defensive cleanup (DC-GP-002 supplement) removes entries where:
      - offsets / from / to / text keys are missing
      - text is empty or whitespace-only
      - the interval is degenerate (start_sec >= end_sec)

    Returns an empty list when the transcription key is absent or not a list.

    Args:
        whisper_json: dict loaded from a whisper.cpp `-oj` JSON file.

    Returns:
        List of normalised Segment objects.
    """
    transcription = whisper_json.get("transcription")
    if not isinstance(transcription, list):
        return []

    segments: list[Segment] = []
    for entry in transcription:
        if not isinstance(entry, dict):
            continue

        offsets = entry.get("offsets")
        if not isinstance(offsets, dict):
            continue
        if "from" not in offsets or "to" not in offsets:
            continue
        if "text" not in entry:
            continue

        try:
            start_ms = float(offsets["from"])
            end_ms = float(offsets["to"])
        except (TypeError, ValueError):
            continue

        text = str(entry["text"]).strip()
        if not text:
            continue

        start_sec = start_ms / 1000.0
        end_sec = end_ms / 1000.0
        # Remove degenerate intervals (start >= end).
        if start_sec >= end_sec:
            continue

        segments.append({"start_sec": start_sec, "end_sec": end_sec, "text": text})

    return segments


def _format_timecode(total_seconds: float, *, ms_separator: str) -> str:
    """Format seconds as "HH:MM:SS{sep}mmm" timecode.

    The ms_separator switches between SRT (",") and VTT (".").
    Both formats share the same second and millisecond values for consistency
    (DC-AS-005).
    Milliseconds are computed with round-half-up (round → int conversion).

    Args:
        total_seconds: Duration in seconds.
        ms_separator: Separator between seconds and milliseconds ("," or ".").

    Returns:
        Formatted timecode string.
    """
    total_ms = int(round(total_seconds * 1000.0))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    seconds, milliseconds = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{ms_separator}{milliseconds:03d}"


def to_srt(segments: list[Segment]) -> str:
    """Convert normalised segments to an SRT string.

    1-based index, "HH:MM:SS,mmm" timecodes, blank-line separator.
    Returns an empty string when segments is empty (DC-GP-002).

    Args:
        segments: List of normalised Segment objects.

    Returns:
        SRT-formatted string.
    """
    if not segments:
        return ""

    blocks: list[str] = []
    for index, seg in enumerate(segments, start=1):
        start_tc = _format_timecode(seg["start_sec"], ms_separator=",")
        end_tc = _format_timecode(seg["end_sec"], ms_separator=",")
        blocks.append(f"{index}\n{start_tc} --> {end_tc}\n{seg['text']}\n")

    return "\n".join(blocks)


def to_vtt(segments: list[Segment]) -> str:
    """Convert normalised segments to a WebVTT string.

    "WEBVTT" header, "HH:MM:SS.mmm" timecodes (dot separator).
    Returns only the "WEBVTT" header when segments is empty (DC-GP-002).

    Args:
        segments: List of normalised Segment objects.

    Returns:
        WebVTT-formatted string.
    """
    if not segments:
        return "WEBVTT\n"

    blocks: list[str] = ["WEBVTT\n"]
    for seg in segments:
        start_tc = _format_timecode(seg["start_sec"], ms_separator=".")
        end_tc = _format_timecode(seg["end_sec"], ms_separator=".")
        blocks.append(f"{start_tc} --> {end_tc}\n{seg['text']}\n")

    return "\n".join(blocks)
