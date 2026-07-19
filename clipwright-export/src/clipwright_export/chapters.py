"""chapters.py — clipwright-export chapter/marker export orchestration.

transform tool: derives chapter data from an OTIO timeline's clipwright
markers and writes a text sidecar (YouTube description list or FFmpeg
metadata file). The input OTIO and media are never modified.

Design decisions (architecture-report §2.2 / §7):
- _export_chapters_inner() is the raising implementation; export_chapters()
  is the boundary that converts ClipwrightError -> error_result and any
  other exception -> INTERNAL (CWE-209: no path leaks in error messages).
- Chapter is a lightweight in-module dataclass (not a shared schema); it is
  never exposed on the MCP surface, so it stays out of schemas.py.
- Title sanitizing and the "chapter_{n}" fallback happen after the ascending
  time sort so the fallback index matches the emitted order (§7.3).
- YouTube time formatting is unified per timeline: if any chapter reaches one
  hour the whole list uses H:MM:SS, otherwise MM:SS (§7.2). Markers are never
  fabricated or padded; constraint violations are reported as warnings only
  (§7.6).
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import get_markers, load_timeline
from clipwright.pathpolicy import check_output_not_source, validate_source_or_basename
from clipwright.schemas import ToolResult

from clipwright_export.schemas import ExportChaptersOptions

# Allowed output extensions per chapter format (ADR-EX-3, §3.3). Lowercase
# comparison. Mismatch -> INVALID_INPUT with the offending suffix kept out of
# the message (SR L-1 / CWE-209).
_ALLOWED_SUFFIXES: dict[str, frozenset[str]] = {
    "youtube": frozenset({".txt"}),
    "ffmetadata": frozenset({".txt", ".ffmeta", ".ffmetadata"}),
}

# YouTube chapter constraints (§7.6 / AC-6 / AC-7).
_YOUTUBE_MIN_CHAPTERS = 3
_YOUTUBE_MIN_INTERVAL_SEC = 10.0
_HOUR_SEC = 3600

_MSG_FIRST_NOT_ZERO = (
    "YouTube requires the first chapter to start at 00:00. hint: add a "
    "scene_boundary marker at the timeline start, or edit the first line "
    "to 00:00 before pasting."
)
_MSG_TOO_FEW = (
    "YouTube requires at least 3 chapters to show a chapter list. hint: "
    "detect more scene boundaries with clipwright-scene."
)


@dataclass
class Chapter:
    """A single chapter boundary: a start time (seconds) and a display title."""

    start_sec: float
    title: str


def _sanitize_title(raw: str) -> str:
    """Replace newlines/control chars with single spaces and strip (§7.3).

    Each disallowed character becomes one space; surrounding whitespace is
    then stripped. Disallowed characters are: newline, carriage return, C0
    controls \\x00-\\x1f, DEL \\x7f, and any character in Unicode category
    Cf (format, including bidirectional-control characters that could spoof
    display order — "Trojan Source"), Zl (line separator), or Zp (paragraph
    separator) (SR-V-001). Returns "" when nothing printable remains,
    letting the caller apply the "chapter_{n}" fallback with a post-sort
    index.
    """
    cleaned = "".join(
        " "
        if (
            ch == "\n"
            or ch == "\r"
            or ch < "\x20"
            or ch == "\x7f"
            or unicodedata.category(ch) in {"Cf", "Zl", "Zp"}
        )
        else ch
        for ch in raw
    )
    return cleaned.strip()


def _collect_chapters(
    tl: otio.schema.Timeline,
    marker_kind: str,
) -> tuple[list[Chapter], float]:
    """Collect chapters from *marker_kind* markers, sorted by start time.

    get_markers does not sort (caller responsibility), so markers are ordered
    here by their marked_range start time ascending; ties keep collection
    order (stable sort). Titles are sanitized (§7.3) and empty results fall
    back to "chapter_{n}" using the 1-based post-sort index. The second tuple
    element is the timeline duration in seconds (§7.5), computed even when no
    markers match (AC-6/AC-9).
    """
    markers = get_markers(tl, kind=marker_kind)
    ordered = sorted(
        markers,
        key=lambda m: m.marked_range.start_time.to_seconds(),
    )

    chapters: list[Chapter] = []
    for index, marker in enumerate(ordered, start=1):
        start_sec = marker.marked_range.start_time.to_seconds()
        title = _sanitize_title(marker.name)
        if not title:
            title = f"chapter_{index}"
        chapters.append(Chapter(start_sec=start_sec, title=title))

    return chapters, tl.duration().to_seconds()


def _format_youtube_time(start_sec: float, *, use_hours: bool) -> str:
    """Format one YouTube timestamp, flooring sub-second precision (§7.2)."""
    total = int(math.floor(start_sec))
    hours, remainder = divmod(total, _HOUR_SEC)
    minutes, seconds = divmod(remainder, 60)
    if use_hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _youtube_warnings(chapters: list[Chapter]) -> list[str]:
    """Evaluate the three YouTube constraints independently, in order (§7.6).

    Never fabricates chapters. Constraint 2 (count < 3) fires even with zero
    chapters; constraints 1 and 3 have no first element/interval to evaluate
    when the list is empty and so stay silent there.
    """
    warnings: list[str] = []

    # Constraint 1: first chapter must display at 00:00.
    if chapters and int(math.floor(chapters[0].start_sec)) != 0:
        warnings.append(_MSG_FIRST_NOT_ZERO)

    # Constraint 2: at least 3 chapters.
    if len(chapters) < _YOUTUBE_MIN_CHAPTERS:
        warnings.append(_MSG_TOO_FEW)

    # Constraint 3: every adjacent interval must be >= 10 seconds.
    short_intervals = sum(
        1
        for earlier, later in zip(chapters, chapters[1:], strict=False)
        if later.start_sec - earlier.start_sec < _YOUTUBE_MIN_INTERVAL_SEC
    )
    if short_intervals:
        warnings.append(
            f"YouTube requires each chapter to be at least 10 seconds long; "
            f"{short_intervals} interval(s) are shorter. hint: merge or remove "
            f"close markers."
        )

    return warnings


def serialize_youtube(chapters: list[Chapter]) -> tuple[str, list[str]]:
    """Render a YouTube description chapter list plus constraint warnings.

    Time formatting is unified across the list: if any chapter reaches one
    hour, every line uses H:MM:SS, otherwise MM:SS (§7.2). Sub-second
    precision is floor-truncated. Returns (text, warnings); text is "" for an
    empty list (no header). Warnings follow §7.6 and never fabricate markers.
    """
    use_hours = any(int(math.floor(c.start_sec)) >= _HOUR_SEC for c in chapters)
    lines = [
        f"{_format_youtube_time(c.start_sec, use_hours=use_hours)} {c.title}"
        for c in chapters
    ]
    return "\n".join(lines), _youtube_warnings(chapters)


def _escape_ffmeta(s: str) -> str:
    """Escape a value for an FFmpeg metadata file (§7.4).

    ffmpeg treats ``= ; # \\`` and newlines specially in metadata values;
    each is prefixed with a backslash. Backslash is escaped first so that
    backslashes introduced by the later substitutions are not re-escaped.
    Newline handling is defensive — §7.3 sanitizing should remove newlines
    before this function is reached.
    """
    s = s.replace("\\", "\\\\")
    s = s.replace("=", "\\=")
    s = s.replace(";", "\\;")
    s = s.replace("#", "\\#")
    s = s.replace("\n", "\\\n")
    return s


def serialize_ffmetadata(chapters: list[Chapter], total_duration_ms: int) -> str:
    """Render an FFmpeg metadata file with one [CHAPTER] block per chapter.

    TIMEBASE is 1/1000 (milliseconds). START = round(start_sec * 1000). END
    of a non-last chapter is the next chapter's START; the last chapter's END
    is total_duration_ms (§7.5). When a computed END is <= START (last
    chapter start exceeds the duration, or two adjacent chapters share a
    start), END falls back to START + 1000 because ffmpeg rejects
    START >= END chapters. An empty list yields the header only.
    """
    starts = [round(c.start_sec * 1000) for c in chapters]

    lines = [";FFMETADATA1"]
    for i, chapter in enumerate(chapters):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(chapters) else total_duration_ms
        if end <= start:
            end = start + 1000
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start}")
        lines.append(f"END={end}")
        lines.append(f"title={_escape_ffmeta(chapter.title)}")

    return "\n".join(lines) + "\n"


def _export_chapters_inner(
    timeline: str,
    output: str,
    options: ExportChaptersOptions,
) -> ToolResult:
    """Internal implementation of export_chapters. Raises ClipwrightError.

    Validation order (architecture-report §2.2, adjusted to match
    timeline_export.py's precedent — see that module's docstring for why
    the same-file guard precedes the suffix check):
      1. output != timeline (PATH_NOT_ALLOWED via check_output_not_source).
         NB: this precedes the suffix check on purpose. When output ==
         timeline the paths share the .otio extension which never matches a
         chapter format suffix, so running the suffix check first would
         return INVALID_INPUT instead of the PATH_NOT_ALLOWED the shared
         collision guard is meant to produce.
      2. output suffix matches the format's allowlist (§3.3, ADR-EX-3)
      3. output parent directory exists
      4. source timeline exists (validate_source_or_basename, FILE_NOT_FOUND)
      5. load_timeline (FILE_NOT_FOUND / OTIO_ERROR propagate; errors are
         converted to ClipwrightError by core load_timeline >= 0.7.1;
         unexpected exceptions reach the outer boundary unconverted)

    Then collect markers, serialize the requested format, write the sidecar,
    and return an ok envelope. Zero matching markers is a success with a
    warning (AC-9). Annotation of the source OTIO is out of scope here (the
    server layer owns it).
    """
    out = Path(output)
    inp = Path(timeline)

    # --- Step 1: output must not resolve to the same file as the timeline ---
    check_output_not_source(out, [timeline])

    # --- Step 2: output suffix must match the selected format (SR L-1: no raw
    # suffix in the message). options.format is already Literal-validated. ---
    if out.suffix.lower() not in _ALLOWED_SUFFIXES[options.format]:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output path extension does not match the chapter format.",
            hint=(
                "Use .txt for the youtube format, or .txt/.ffmeta/.ffmetadata "
                "for the ffmetadata format."
            ),
        )

    # --- Step 3: output parent exists (SR M-1: no path in message or hint) ---
    if not out.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="Output directory does not exist.",
            hint="Create the output directory before exporting chapters.",
        )

    # --- Step 4: source timeline exists (FILE_NOT_FOUND default, matching
    # the transform classification and timeline_export.py's precedent) ---
    validate_source_or_basename(
        timeline,
        message=f"Timeline file not found: {inp.name}",
        hint="Verify the timeline path and ensure the file exists.",
    )

    # --- Step 5: load timeline. Load failures (missing file, malformed JSON,
    # OTIOError) are converted to ClipwrightError by core load_timeline
    # (clipwright >= 0.7.1); unexpected exceptions reach the outer INTERNAL
    # boundary (below) unconverted. ---
    timeline_obj = load_timeline(timeline)

    # --- Collect markers -> chapters, then serialize the requested format ---
    chapters, duration_sec = _collect_chapters(timeline_obj, options.marker_kind)

    warnings: list[str] = []
    if not chapters:
        warnings.append(
            f"No '{options.marker_kind}' markers found in the timeline; wrote "
            "an empty chapter file. hint: run clipwright-scene to detect scene "
            "boundaries first."
        )

    if options.format == "youtube":
        text, format_warnings = serialize_youtube(chapters)
        warnings.extend(format_warnings)
    else:
        total_duration_ms = round(duration_sec * 1000)
        text = serialize_ffmetadata(chapters, total_duration_ms)

    # --- Write the sidecar (non-destructive: input OTIO/media untouched) ---
    out.write_text(text, encoding="utf-8")

    out_resolved = out.resolve()
    summary = (
        f"Wrote {len(chapters)} chapter(s) in {options.format} format to {out.name}."
    )
    if warnings:
        summary += f" {len(warnings)} warning(s) — review before use."

    return ok_result(
        summary=summary,
        data={
            "chapter_count": len(chapters),
            "format": options.format,
            "marker_kind": options.marker_kind,
        },
        artifacts=[
            {
                "role": "chapters",
                "path": str(out_resolved),
                "format": options.format,
            }
        ],
        warnings=warnings,
    )


def export_chapters(
    timeline: str,
    output: str,
    options: ExportChaptersOptions,
) -> ToolResult:
    """Export chapter data from an OTIO timeline to a text sidecar file.

    transform tool: reads clipwright markers of options.marker_kind from
    the timeline and writes either a YouTube description chapter list or an
    FFmpeg metadata file. Non-destructive: the input timeline and its media
    are never modified. Zero matching markers is a success with a warning.

    Args:
        timeline: Input OTIO timeline file path.
        output: Output sidecar path; its extension must match options.format
            (.txt for youtube; .txt/.ffmeta/.ffmetadata for ffmetadata) and
            must differ from timeline.
        options: ExportChaptersOptions with required format and marker_kind.

    Returns:
        ToolResult from ok_result or error_result.
    """
    try:
        return _export_chapters_inner(timeline, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # CWE-209: fixed wording so no internal path/detail reaches the
        # MCP error envelope.
        return error_result(
            ErrorCode.INTERNAL,
            "Exporting chapters failed due to an internal error.",
            "Retry after verifying that the output directory is writable.",
        )
