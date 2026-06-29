"""captions.py — clipwright-wrap pure-logic layer.

Handles SRT/VTT parsing, greedy line-filling of phrase-boundary token sequences
with max_chars, SRT/VTT re-serialisation, and overflow detection.
Pure functions with no budoux import (contract coverage target: ~100%).

Design decisions:
- Timecode strings are preserved as-is without float conversion (WR-AD-06).
- SRT/VTT byte structure conforms to the WR-AD-12 specification.
- No delimiter is inserted when joining phrase-boundary tokens (WR-AD-14).
- Line-count excess is resolved by greedy front-merge (_merge_to_max_lines)
  rather than detected as an overflow condition.  Overflow detection covers
  only line-width excess (ADR-W2 / WR-AD-15(1) revised).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clipwright.errors import ClipwrightError, ErrorCode

# Regex matching a VTT timeline line: "HH:MM:SS.mmm --> HH:MM:SS.mmm [settings]"
_VTT_TIMELINE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})(.*)"
)

# Regex matching an SRT timeline line: "HH:MM:SS,mmm --> HH:MM:SS,mmm"
# Fixed-width HH:MM:SS,mmm digits; conforms to WR-AD-12
# (transcribe to_srt guarantees fixed width)
_SRT_TIMELINE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})\s*$"
)

# Detection of VTT inline tags (<c>, <b>, <i>, <v>, <ruby>, etc.)
# The [^>]{0,200} upper bound mitigates ReDoS (CWE-1333)
_VTT_INLINE_TAG_RE = re.compile(r"<[a-zA-Z/][^>]{0,200}>")


@dataclass
class Cue:
    """Normalised representation of a single subtitle cue.

    index is the sequence number (1-based).
    start / end are timecode strings (not converted to float; WR-AD-06).
    text is the cue body text (line breaks represented as '\\n').
    VTT cue settings are appended to the end field
    (e.g. "00:00:01.000 line:90% position:50%").
    """

    index: int
    start: str
    end: str
    text: str


def _parse_srt(text: str) -> list[Cue]:
    """Convert an SRT text string into a list of Cues.

    Conforms to the WR-AD-12(1)(2) byte-structure specification:
    - Blank-line delimited (robust to consecutive / trailing blank lines)
    - Does not miss the last cue when the trailing cue has no blank line
      (single newline EOF)
    - 0 entries (empty string or newlines only) → []
    - Multi-line text within a cue is joined without a delimiter
      (no space inserted; WR-AD-14)
    - Invalid timecode line → raises ValueError
      (caller wrap.py converts this to INVALID_INPUT)
    """
    if not text.strip():
        return []

    # Normalise consecutive blank lines to a single blank line before splitting
    normalized = re.sub(r"\n{2,}", "\n\n", text.strip())
    blocks = normalized.split("\n\n")

    cues: list[Cue] = []
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:  # pragma: no cover
            # Unreachable: normalisation never produces an empty block (defensive guard)
            continue

        # Line 1: index number
        try:
            index = int(lines[0].strip())
        except ValueError:
            # Block does not start with an index line; skip (empty block, etc.)
            continue

        if len(lines) < 2:
            continue

        # Line 2: timeline line
        timeline_line = lines[1].strip()
        m = _SRT_TIMELINE_RE.match(timeline_line)
        if m is None:
            # Invalid timecode line: raise ValueError (test contract WR-AD-09)
            raise ValueError(
                f"Invalid SRT timecode line: {timeline_line!r}"
                f" (expected format: 'HH:MM:SS,mmm --> HH:MM:SS,mmm')"
            )

        start = m.group(1)
        end = m.group(2)

        # Value range check: MM/SS must be within 0–59 (WR-AD-12 / SRT spec)
        for tc in (start, end):
            mm, ss = int(tc[3:5]), int(tc[6:8])
            if mm > 59 or ss > 59:
                raise ValueError(
                    f"SRT timecode value out of range: {tc!r}"
                    f" (minutes and seconds must be within 0–59)"
                )

        # Line 3 onwards: text (multiple lines joined without delimiter; no space)
        text_lines = lines[2:] if len(lines) > 2 else []
        joined_text = "".join(text_lines)

        cues.append(Cue(index=index, start=start, end=end, text=joined_text))

    return cues


def _parse_vtt(text: str) -> list[Cue]:
    """Convert a VTT text string into a list of Cues.

    Conforms to the WR-AD-12(1)(2)(3) byte-structure specification
    and all 5 VTT edge-case behaviours:
    - Skip the blank line immediately after the WEBVTT header
    - 0 entries ("WEBVTT\\n" only) → []
    - NOTE/STYLE blocks: preserved as-is (not treated as cues)
    - cue id line: preserved; only the text lines are formatting targets
    - cue settings (trailing part of the timeline line): appended to the end field
    - cues containing inline tags: text preserved as-is (tags included, single line)
    - Multi-line text within a cue is joined without a delimiter (WR-AD-14)
    """
    lines = text.splitlines()

    # Verify and skip the WEBVTT header
    if not lines or not lines[0].startswith("WEBVTT"):
        return []

    # Process lines after the header
    pos = 1
    total = len(lines)

    # Skip blank lines immediately after the header
    while pos < total and lines[pos].strip() == "":
        pos += 1

    cues: list[Cue] = []
    cue_index = 1

    while pos < total:
        # Skip blank lines (cue separator)
        if lines[pos].strip() == "":
            pos += 1
            continue

        # NOTE block: skip until the next blank line or EOF
        if lines[pos].startswith("NOTE"):
            pos += 1
            while pos < total and lines[pos].strip() != "":
                pos += 1
            continue

        # STYLE block: skip until the next blank line or EOF
        if lines[pos].startswith("STYLE"):
            pos += 1
            while pos < total and lines[pos].strip() != "":
                pos += 1
            continue

        # Check for a cue id line (non-empty line that is not a timeline line)
        if not _VTT_TIMELINE_RE.match(lines[pos]):
            # cue id line: identifier before the timeline — skip (preserved implicitly)
            pos += 1
            if pos >= total:
                break

        # Timeline line
        if pos >= total or lines[pos].strip() == "":
            pos += 1
            continue

        m = _VTT_TIMELINE_RE.match(lines[pos])
        if m is None:  # pragma: no cover
            # Unreachable for well-formed VTT input (fallback defensive guard)
            pos += 1
            continue

        start = m.group(1)
        # Append settings to end field for preservation (WR-AD-12(3)(d))
        end_raw = m.group(2)
        settings = m.group(3).strip()
        end = f"{end_raw} {settings}" if settings else end_raw

        pos += 1

        # Collect text lines until the next blank line or EOF
        text_lines: list[str] = []
        while pos < total and lines[pos].strip() != "":
            text_lines.append(lines[pos])
            pos += 1

        # Join text without a delimiter (no space inserted; WR-AD-14)
        joined_text = "".join(text_lines)

        cues.append(Cue(index=cue_index, start=start, end=end, text=joined_text))
        cue_index += 1

    return cues


def parse_captions(text: str, fmt: str) -> list[Cue]:
    """Convert an SRT or VTT text string into a list of Cues.

    fmt must be "srt" or "vtt".
    Timecode strings are preserved as-is (WR-AD-06).
    Multi-line text within a cue is joined without a delimiter (WR-AD-14).
    An invalid timecode line causes _parse_srt to raise ValueError,
    which wrap.py converts to ClipwrightError(INVALID_INPUT) (WR-AD-09).

    Args:
        text: SRT or VTT format string.
        fmt: "srt" or "vtt".

    Returns:
        List of Cues. Returns an empty list when there are 0 entries.
    """
    if fmt == "srt":
        return _parse_srt(text)
    elif fmt == "vtt":
        return _parse_vtt(text)
    else:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Unsupported subtitle format: {fmt!r}",
            hint="Specify 'srt' or 'vtt' for fmt.",
        )


def wrap_cue_lines(segments: list[str], max_chars: int, joiner: str = "") -> list[str]:
    """Return lines formed by greedily packing phrase-boundary tokens up to max_chars.

    Conforms to WR-AD-04/WR-AD-14:
    - Segments are appended to a line; a line break is inserted just before the
      limit is exceeded (greedy fill).
    - If a single segment exceeds max_chars on its own, it is placed on its own
      line without splitting.
    - '\\n' is not included in len() of each line (WR-AD-14(ii)).
    - Full-width and half-width characters are each counted as 1
      (WR-AD-14(iii); uniform len() check).

    Args:
        segments: List of phrase-boundary tokens.
        max_chars: Maximum number of characters per line (gt=0).
        joiner: String inserted between adjacent tokens on the same line.
            ``joiner=""`` (default) concatenates tokens directly, preserving
            CJK byte-equivalence (WR-AD-14(i)). ``joiner=" "`` inserts one
            space between words, suitable for space-delimited (Latin) languages.

    Returns:
        List of lines (no '\\n' within any line). Returns [] for empty segments.
    """
    if not segments:
        return []

    lines: list[str] = []
    current_line = ""

    for seg in segments:
        if not current_line:
            # Start of a line: place segment even if exceeds max_chars (no splitting)
            current_line = seg
        elif len(current_line) + len(joiner) + len(seg) <= max_chars:
            # Adding the segment (with joiner) stays within max_chars → append
            current_line += joiner + seg
        else:
            # Would exceed the limit → insert a line break
            lines.append(current_line)
            current_line = seg

    if current_line:
        lines.append(current_line)

    return lines


def _merge_to_max_lines(
    lines: list[str], max_lines: int, joiner: str = ""
) -> tuple[list[str], bool]:
    """Reduce *lines* to at most *max_lines* by greedy front-merge.

    Adjacent lines are concatenated from the front (index 0 + index 1 → index 0)
    with *joiner* as separator until ``len(lines) <= max_lines``.

    The algorithm is deterministic: identical inputs always produce identical
    outputs.  When ``max_lines == 1`` every line is folded into a single string.

    Precondition: max_lines >= 1. The MCP boundary enforces this via the
    Pydantic ``gt=0`` constraint on WrapCaptionsOptions.max_lines, so this
    function is only reached with max_lines >= 1. Calling it directly with
    max_lines < 1 is undefined behaviour (the while-loop would not terminate
    for a non-empty input).

    Args:
        lines: Lines to merge.  May be empty.
        max_lines: Target upper bound on the number of lines (gt=0).
        joiner: String inserted between the two lines being merged.
            ``joiner=""`` (default) concatenates directly, preserving CJK
            byte-equivalence under WR-AD-14. ``joiner=" "`` inserts one space,
            suitable for space-delimited (Latin) languages.

    Returns:
        A 2-tuple ``(merged_lines, merged)`` where:

        - ``merged_lines``: Result list with ``len(merged_lines) <= max_lines``.
        - ``merged`` (DC-AM-001 predicate): ``True`` when at least one adjacent-line
          concatenation occurred, ``False`` otherwise (i.e. when
          ``len(lines) <= max_lines`` on entry, including the empty-list case).
    """
    if len(lines) <= max_lines:
        return (lines, False)

    result = list(lines)
    while len(result) > max_lines:
        result[0] = result[0] + joiner + result[1]
        del result[1]

    return (result, True)


def _serialize_srt(cues: list[Cue]) -> str:
    """Convert a list of Cues into an SRT string.

    Byte-structure specification (WR-AD-12(1)):
    - Each block = "index\\nstart --> end\\ntext\\n"
    - One blank line between cues (trailing \\n of the block + the join \\n)
    - Single newline after the last cue (no trailing blank line)
    - 0 entries → ""
    """
    if not cues:
        return ""

    blocks: list[str] = []
    for cue in cues:
        blocks.append(f"{cue.index}\n{cue.start} --> {cue.end}\n{cue.text}\n")

    return "\n".join(blocks)


def _serialize_vtt(cues: list[Cue]) -> str:
    """Convert a list of Cues into a VTT string.

    Byte-structure specification (WR-AD-12(1)):
    - "WEBVTT\\n" + "\\n" + cue1 + "\\n" + cue2 + ...
    - Each cue block = "start --> end\\ntext\\n"
    - One blank line between cues; single newline after the last cue
      (no trailing blank line)
    - 0 entries → "WEBVTT\\n"
    """
    if not cues:
        return "WEBVTT\n"

    blocks: list[str] = ["WEBVTT\n"]
    for cue in cues:
        blocks.append(f"{cue.start} --> {cue.end}\n{cue.text}\n")

    return "\n".join(blocks)


def serialize_captions(cues: list[Cue], fmt: str) -> str:
    """Convert a list of Cues into an SRT or VTT string.

    Timecode strings are written back unchanged (WR-AD-06).
    For 0 entries: SRT → "" / VTT → "WEBVTT\\n" (round-trip identity; WR-AD-12(2)).

    Args:
        cues: List of Cues.
        fmt: "srt" or "vtt".

    Returns:
        SRT or VTT format string.
    """
    if fmt == "srt":
        return _serialize_srt(cues)
    elif fmt == "vtt":
        return _serialize_vtt(cues)
    else:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Unsupported subtitle format: {fmt!r}",
            hint="Specify 'srt' or 'vtt' for fmt.",
        )


def check_overflow(lines: list[str], max_chars: int) -> bool:
    """Return True when any line exceeds *max_chars* characters (width overflow).

    This function detects line-width excess only (ADR-W2 / WR-AD-15(1) revised).
    Line-count excess is no longer an overflow condition; it is resolved upstream
    by :func:`_merge_to_max_lines` before this check is applied.

    Because the check is applied after merge, a queue that becomes width-excess
    as a result of front-merging is also reported here (DC-AS-005 — intended
    behaviour: merged lines that exceed max_chars surface as width overflow).

    Args:
        lines: List of lines to inspect (each line must not contain '\\n').
        max_chars: Maximum number of characters per line.

    Returns:
        True if at least one line has ``len(line) > max_chars``, False otherwise.
    """
    return any(len(line) > max_chars for line in lines)
