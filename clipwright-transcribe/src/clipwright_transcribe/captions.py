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

from clipwright.errors import ClipwrightError, ErrorCode


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


# ---------------------------------------------------------------------------
# Word-level types and constants (s1-captions-impl — ADR-K8)
# ---------------------------------------------------------------------------

MAX_WORDS_TRANSCRIBE = 50_000
"""Maximum total word count accepted by extract_word_segments (CWE-400 / ADR-K8).

Headroom: a 30-minute short film contains roughly 5,400 words (at ~3 words/s),
giving approximately 10× safety margin.  Exceeding this limit raises INVALID_INPUT
before any large artifact is materialised.
"""


class WordTiming(TypedDict):
    """A single reconstructed word with its time boundaries.

    text has leading/trailing whitespace stripped.
    start_sec / end_sec are in seconds (float), derived from BPE token offsets.
    """

    text: str
    start_sec: float
    end_sec: float


class WordSegment(TypedDict):
    """A whisper segment annotated with per-word timing (word-level VTT / OTIO output).

    start_sec / end_sec match the segment-level offsets (same as Segment).
    text is the segment-level text, stripped.
    words contains the reconstructed word list for this segment.
    """

    start_sec: float
    end_sec: float
    text: str
    words: list[WordTiming]


class OtioWordEntry(TypedDict):
    """A single word entry in OTIO metadata (ADR-K1 schema).

    Keys use 'start'/'end' (not 'start_sec'/'end_sec') to match the OTIO convention
    established in ADR-K1.
    """

    text: str
    start: float
    end: float


# ---------------------------------------------------------------------------
# Word-level functions (s1-captions-impl)
# ---------------------------------------------------------------------------


def _check_word_limit(total_words: int) -> None:
    """Raise INVALID_INPUT when total_words exceeds MAX_WORDS_TRANSCRIBE (CWE-400).

    Must be called before the word is appended to memory, so that no more than
    MAX_WORDS_TRANSCRIBE words are ever stored (off-by-one guard / SR L-1).

    Args:
        total_words: Cumulative word count including the word about to be appended.
    """
    if total_words > MAX_WORDS_TRANSCRIBE:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Word count exceeds {MAX_WORDS_TRANSCRIBE}.",
            hint=(
                f"Split the audio into shorter segments "
                f"(limit: {MAX_WORDS_TRANSCRIBE} words)."
            ),
        )


def _extract_words_from_tokens(
    tokens: list[Any], total_words_before: int
) -> list[WordTiming]:
    """Reconstruct per-word timing from a single segment's BPE token list.

    Applies word-reconstruction rules confirmed in spike-whisper-word.md (ADR-K2):
    - Special tokens (text starting with ``[_`` or ``<|``) are excluded.
    - Tokens with degenerate intervals (offsets.from >= offsets.to) are excluded.
    - A token whose text starts with U+0020 (leading space) begins a new word; a
      token without a leading space is appended to the current word (BPE sub-word
      merge).
    - word.start_sec = first token's offsets.from / 1000.
    - word.end_sec   = last  token's offsets.to   / 1000.

    The cumulative word count is incremented and checked before each word is appended
    (CWE-400 / ADR-K8): increment → _check_word_limit → append.  This ensures that
    MAX_WORDS_TRANSCRIBE + 1 words are never held in memory simultaneously (SR L-1).

    Raises:
        ClipwrightError(INVALID_INPUT): total_words_before + words extracted here
            exceeds MAX_WORDS_TRANSCRIBE.

    Args:
        tokens: transcription[].tokens[] list from a whisper ``-ojf`` JSON entry.
        total_words_before: Cumulative word count from all preceding segments.

    Returns:
        List of WordTiming entries for this segment (may be empty).
    """
    words: list[WordTiming] = []
    running_total = total_words_before
    current_text: str | None = None
    current_start: float = 0.0
    current_end: float = 0.0

    for token in tokens:
        if not isinstance(token, dict):
            continue

        tok_text = str(token.get("text", ""))

        # Exclude special tokens before any other check.
        if tok_text.startswith("[_") or tok_text.startswith("<|"):
            continue

        tok_offsets = token.get("offsets")
        if not isinstance(tok_offsets, dict):
            continue

        try:
            from_ms = float(tok_offsets["from"])
            to_ms = float(tok_offsets["to"])
        except (KeyError, TypeError, ValueError):
            continue

        # Exclude degenerate intervals.
        if from_ms >= to_ms:
            continue

        if tok_text.startswith(" "):
            # Leading space = new word boundary.
            if current_text is not None:
                stripped = current_text.strip()
                if stripped:
                    running_total += 1
                    _check_word_limit(running_total)
                    words.append(
                        {
                            "text": stripped,
                            "start_sec": current_start,
                            "end_sec": current_end,
                        }
                    )
            current_text = tok_text
            current_start = from_ms / 1000.0
            current_end = to_ms / 1000.0
        else:
            # BPE sub-word: append to current word.
            if current_text is not None:
                current_text += tok_text
                current_end = to_ms / 1000.0
            else:
                # First valid token in the segment has no leading space (rare).
                current_text = tok_text
                current_start = from_ms / 1000.0
                current_end = to_ms / 1000.0

    # Finalise the last word in the segment.
    if current_text is not None:
        stripped = current_text.strip()
        if stripped:
            running_total += 1
            _check_word_limit(running_total)
            words.append(
                {
                    "text": stripped,
                    "start_sec": current_start,
                    "end_sec": current_end,
                }
            )

    return words


def extract_word_segments(whisper_json: dict[str, Any]) -> list[WordSegment]:
    """Create word-level segments from a whisper.cpp ``-ojf`` (full JSON) dict.

    Delegates per-segment token processing to ``_extract_words_from_tokens``, which
    applies the BPE word-reconstruction rules (ADR-K2 / spike-whisper-word.md).
    The total word count is accumulated across segments; ``_check_word_limit`` is
    called incrementally inside the helper so the limit is enforced before any
    word that would breach it is stored in memory (CWE-400 / ADR-K8).

    Raises:
        ClipwrightError(INVALID_INPUT): total word count across all segments exceeds
            MAX_WORDS_TRANSCRIBE.

    Args:
        whisper_json: dict loaded from a whisper.cpp ``-ojf`` JSON file.

    Returns:
        List of WordSegment objects, one per non-degenerate transcription segment.
    """
    transcription = whisper_json.get("transcription")
    if not isinstance(transcription, list):
        return []

    result: list[WordSegment] = []
    total_words = 0

    for entry in transcription:
        if not isinstance(entry, dict):
            continue

        offsets = entry.get("offsets")
        if not isinstance(offsets, dict):
            continue

        try:
            seg_start_sec = float(offsets["from"]) / 1000.0
            seg_end_sec = float(offsets["to"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue

        # Skip degenerate segments (mirrors normalize_segments behaviour).
        if seg_start_sec >= seg_end_sec:
            continue

        seg_text = str(entry.get("text", "")).strip()

        tokens = entry.get("tokens")
        if not isinstance(tokens, list):
            continue

        words = _extract_words_from_tokens(tokens, total_words)
        total_words += len(words)

        result.append(
            {
                "start_sec": seg_start_sec,
                "end_sec": seg_end_sec,
                "text": seg_text,
                "words": words,
            }
        )

    return result


def to_word_vtt(word_segments: list[WordSegment]) -> str:
    """Convert word segments to a word-level WebVTT string with inline timestamps.

    Each cue spans the full segment range (start_sec → end_sec).  Within the cue
    body, every word is preceded by its start time as an inline WebVTT timestamp
    ``<HH:MM:SS.mmm>``.  The ``<`` and ``>`` characters in word text are stripped
    to prevent malformed inline timestamp tags (SEC-04 analogue).

    Inline timestamps are monotonically non-decreasing within and across cues
    because word.start_sec values are derived from sequential token offsets.

    Returns only the ``WEBVTT`` header when word_segments is empty (DC-GP-002 parity).

    Args:
        word_segments: List of WordSegment objects (output of extract_word_segments).

    Returns:
        WebVTT-formatted string with inline word timestamps.
    """
    if not word_segments:
        return "WEBVTT\n"

    blocks: list[str] = ["WEBVTT\n"]
    for seg in word_segments:
        start_tc = _format_timecode(seg["start_sec"], ms_separator=".")
        end_tc = _format_timecode(seg["end_sec"], ms_separator=".")
        parts: list[str] = []
        for word in seg["words"]:
            ts = _format_timecode(word["start_sec"], ms_separator=".")
            # Strip < and > from word text to avoid corrupting inline timestamp tags.
            safe_text = word["text"].replace("<", "").replace(">", "")
            parts.append(f"<{ts}>{safe_text}")
        content = " ".join(parts)
        blocks.append(f"{start_tc} --> {end_tc}\n{content}\n")

    return "\n".join(blocks)


def words_for_otio(word_segments: list[WordSegment]) -> list[OtioWordEntry]:
    """Flatten word segments into the OTIO metadata words schema.

    Produces a single list of ``{text, start, end}`` dicts suitable for storage in
    ``metadata["clipwright"]["words"]``.  The keys ``start`` / ``end`` (not
    ``start_sec`` / ``end_sec``) match the OTIO convention established in ADR-K1.

    Args:
        word_segments: List of WordSegment objects.

    Returns:
        Flat list of OtioWordEntry dicts with keys ``text`` (str), ``start`` (float
        seconds), ``end`` (float seconds).
    """
    result: list[OtioWordEntry] = []
    for seg in word_segments:
        for word in seg["words"]:
            result.append(
                {
                    "text": word["text"],
                    "start": word["start_sec"],
                    "end": word["end_sec"],
                }
            )
    return result
