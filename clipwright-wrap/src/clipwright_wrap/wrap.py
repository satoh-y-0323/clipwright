"""wrap.py — clipwright-wrap orchestration layer.

Output validation → input existence check → subtitle parsing →
segmentation (CJK: wrap_cli subprocess; Latin: in-process split) →
greedy line-filling, front-merge convergence, and re-serialisation via captions →
output write → envelope return.

Design decisions:
- Language routing: CJK/Thai (ja/zh-hans/zh-hant/th) → wrap_cli subprocess (WR-AD-01).
  Latin (en/es/fr/de/it/pt/nl) → in-process text.split() (no subprocess).
  Unsupported languages raise ClipwrightError(INVALID_INPUT) with a prescriptive hint.
- wrap_cli error detection is based on the "error" key in stdout JSON (DC-AS-007).
- subprocess failure/timeout uses the sanitised message in SUBPROCESS_SAFE_MESSAGE.
- FILE_NOT_FOUND message contains only the basename (no full path exposure; WR-AD-09).
- Line-count excess is resolved by front-merge (_merge_to_max_lines) before overflow
  detection; line-count overflow is no longer an overflow condition (ADR-W2 / W1).
- Overflow detection covers only line-width excess after merge (WR-AD-15(1) revised).
  Merge-induced width overflow surfaces here as intended (DC-AS-005).
- Warnings use a single aggregated sentence + index arrays in data
  (WR-AD-13(2); DC-AM-002).
- artifacts are dicts (Artifact model not instantiated; DC-AS-005).
- OTIO is neither generated nor used (WR-AD-06).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.pathpolicy import validate_source_file
from clipwright.process import SUBPROCESS_SAFE_MESSAGE
from clipwright.schemas import ToolResult

from clipwright_wrap.captions import (
    _merge_to_max_lines,
    check_overflow,
    parse_captions,
    serialize_captions,
    wrap_cue_lines,
)
from clipwright_wrap.languages import is_cjk, is_space_delimited
from clipwright_wrap.schemas import WrapCaptionsOptions

# Timeout coefficient proportional to cue count (WR-AD-11/WR-AD-15(2))
_TIMEOUT_COEFFICIENT = 0.05
_TIMEOUT_MIN = 30


def _compute_timeout(cue_count: int) -> float:
    """Calculate the cue-count-proportional timeout.

    Returns max(30, ceil(cue_count * 0.05)).
    """
    return float(max(_TIMEOUT_MIN, math.ceil(cue_count * _TIMEOUT_COEFFICIENT)))


def wrap_captions(
    input: str,
    output: str,
    options: WrapCaptionsOptions,
) -> ToolResult:
    """Insert phrase-boundary line breaks into a subtitle file (WR-AD-04).

    Non-destructive: the input subtitle file is never modified.
    The output is the path of the newly generated SRT/VTT, returned in artifacts.

    Args:
        input: Input subtitle file path (.srt or .vtt).
        output: Output subtitle file path (same extension as input).
        options: WrapCaptionsOptions (language/max_chars/max_lines).

    Returns:
        Envelope dict as ok_result or error_result.
    """
    try:
        return _wrap_inner(input, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        # SR-R-001 / F-1: catch unexpected exceptions (e.g. OSError from
        # read_text/write_text, UnicodeDecodeError) with fixed wording to
        # prevent internal path exposure via FastMCP's str(exc) (CWE-209).
        return error_result(
            ErrorCode.INTERNAL,
            "Caption wrapping failed due to an internal error.",
            "Retry after verifying that the input/output paths are accessible.",
        )


def _wrap_inner(
    input: str,
    output: str,
    options: WrapCaptionsOptions,
) -> ToolResult:
    """Internal implementation of wrap_captions. Raises ClipwrightError directly."""
    input_path = Path(input)
    output_path = Path(output)

    # --- 1. Output validation (WR-AD-07/08) ---

    # Verify that extensions are srt/vtt
    input_ext = input_path.suffix.lower()
    output_ext = output_path.suffix.lower()

    if input_ext not in (".srt", ".vtt"):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            # Fixed message (SR-R-001 / CWE-209): caller-supplied extension is
            # not echoed back into the error text.
            message="Unsupported input subtitle extension (expected .srt or .vtt).",
            hint="Set the input file extension to .srt or .vtt.",
        )

    if output_ext not in (".srt", ".vtt"):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Unsupported output subtitle extension (expected .srt or .vtt).",
            hint="Set the output file extension to .srt or .vtt.",
        )

    # Verify extensions match (SRT↔VTT cross-conversion is out of scope)
    if input_ext != output_ext:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Input and output subtitle extensions do not match.",
            hint="Specify an output path with the same extension as the input.",
        )

    # Verify that the output parent directory exists
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output directory does not exist.",
            hint="Create the output directory first, then run again.",
        )

    # Prohibit output == input
    try:
        if output_path.resolve() == input_path.resolve():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Output path and input path are the same.",
                hint="Change the output file path to a path different from the input.",
            )
    except OSError:  # pragma: no cover
        if str(output_path) == str(input_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="Output path and input path are the same.",
                hint="Change the output file path to a path different from the input.",
            ) from None

    # --- 2. Input existence check (WR-AD-09; FILE_NOT_FOUND uses basename only) ---
    # Delegates to the shared core guard (validate_source_file) so symlinked
    # inputs are rejected with PATH_NOT_ALLOWED (ADR-PP-2 / CWE-59), instead of
    # re-implementing the symlink check locally.

    try:
        validate_source_file(str(input_path))
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            # Re-wrap to keep wrap's basename-only message contract (WR-AD-09):
            # core's FILE_NOT_FOUND message embeds the full caller-supplied path,
            # which would leak directory structure (CWE-209). __cause__ is
            # dropped via `from None` so the core message never surfaces.
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {input_path.name}",
                hint="Check that the input file path is correct.",
            ) from None
        # PATH_NOT_ALLOWED (symlink) and any other core error propagate as-is;
        # core's message/hint are not overridden here.
        raise

    # --- 3. Read input ---

    raw_text = input_path.read_text(encoding="utf-8")
    fmt = input_ext.lstrip(".")  # "srt" or "vtt"

    # --- 4. captions.parse_captions (invalid timecode → INVALID_INPUT + hint) ---

    try:
        cues = parse_captions(raw_text, fmt)
    except ValueError:
        # Convert ValueError to INVALID_INPUT; fixed message (not str(exc)); CWE-209
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Failed to parse subtitle file (timecode format error).",
            hint=(
                "Check the format of the timecode line"
                " (e.g. 00:00:00,000 --> 00:00:01,000)."
            ),
        ) from None

    # --- 5. Segmentation: select strategy by language class ---

    language = options.language

    # Defensive guard. The Pydantic pattern at the MCP boundary already restricts
    # language to the allowlist, so this is reached only on direct/bypass calls.
    if not (is_cjk(language) or is_space_delimited(language)):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Unsupported language for caption wrapping.",
            hint=(
                "Specify one of: ja, zh-hans, zh-hant, th (CJK/Thai, budoux); "
                "or en, es, fr, de, it, pt, nl (space-delimited Latin)."
            ),
        )

    # joiner: "" preserves CJK byte-equivalence; " " inserts one space between words.
    joiner = " " if is_space_delimited(language) else ""

    cue_count = len(cues)
    segments: list[list[str]]

    if cue_count == 0:
        segments = []
    elif is_space_delimited(language):
        # In-process whitespace split. No subprocess, no budoux (FR-5/NFR-2).
        # str.split() with no argument collapses consecutive whitespace and strips
        # leading/trailing whitespace — empty cues produce [] (no empty tokens).
        segments = [cue.text.split() for cue in cues]
    else:
        # CJK/Thai: existing budoux subprocess path (WR-AD-01/WR-AD-02/DC-AS-007).
        stdin_payload = json.dumps(
            {
                "language": language,
                "texts": [cue.text for cue in cues],
            },
            ensure_ascii=False,
        )
        timeout = _compute_timeout(cue_count)

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "clipwright_wrap.wrap_cli"],
                input=stdin_payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message=f"{SUBPROCESS_SAFE_MESSAGE} (timeout)",
                hint=(
                    "The subtitle file may contain too many cues. "
                    "Try again or reduce the number of cues."
                ),
            ) from None
        except OSError:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=SUBPROCESS_SAFE_MESSAGE,
                hint=(
                    "Failed to launch wrap_cli. "
                    "Check that clipwright-wrap is correctly installed."
                ),
            ) from None

        # wrap_cli returns 0; errors detected via "error" key in stdout JSON (DC-AS-007)
        try:
            parsed: dict[str, Any] = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=SUBPROCESS_SAFE_MESSAGE,
                hint="Failed to parse wrap_cli output JSON. Please run again.",
            ) from None

        if "error" in parsed:
            err = parsed["error"]
            code_str: str = err.get("code", str(ErrorCode.INTERNAL))
            wrap_msg: str = err.get("message", "An error occurred in wrap_cli")
            wrap_hint: str = err.get("hint", "Please report with reproduction steps.")
            # wrap_cli runs in a separate process and only emits fixed, path-free
            # error message/hint strings (verified against wrap_cli.py). These are
            # transcribed as-is into the envelope. As a defensive cap against a
            # future/compromised wrap_cli, message and hint are bounded in length
            # (SR-M-2). This also bounds the language hint enumeration leak (SR-L-2,
            # resolved by the same bound).
            # bound chosen above fixed-string length to avoid truncating known messages
            wrap_msg = wrap_msg[:500]
            wrap_hint = wrap_hint[:500]
            # Convert to ErrorCode (DEPENDENCY_MISSING propagated as-is)
            try:
                err_code = ErrorCode(code_str)
            except ValueError:
                err_code = ErrorCode.INTERNAL
            raise ClipwrightError(code=err_code, message=wrap_msg, hint=wrap_hint)

        segments = parsed.get("segments", [])

    # --- 6. Apply wrap_cue_lines → front-merge → overflow detection pipeline ---

    merged_cue_indices: list[int] = []
    overflow_width_cue_indices: list[int] = []
    wrapped_count = 0

    for i, cue in enumerate(cues):
        seg = segments[i] if i < len(segments) else [cue.text]
        lines = wrap_cue_lines(seg, options.max_chars, joiner=joiner)

        # Front-merge: collapse lines to at most max_lines (ADR-W1)
        lines, merged = _merge_to_max_lines(lines, options.max_lines, joiner=joiner)
        if merged:
            merged_cue_indices.append(i)

        # Width overflow detection applied after merge (WR-AD-15(1) revised; DC-AS-005:
        # merge-induced width overflow is intentional — detection remains post-merge)
        if check_overflow(lines, options.max_chars):
            overflow_width_cue_indices.append(i)

        # Increment wrapped_count when the text has changed (line break inserted)
        new_text = "\n".join(lines)
        if new_text != cue.text:
            wrapped_count += 1

        # Update cue.text to the formatted text (no truncation; full text preserved)
        cue.text = new_text

    # --- 7. captions.serialize_captions → write output ---

    serialized = serialize_captions(cues, fmt)
    output_path.write_text(serialized, encoding="utf-8")

    # --- 8. Build envelope (WR-AD-13) ---

    warnings: list[str] = []

    # Line-width overflow warnings (single aggregated sentence; omitted when 0 entries)
    if overflow_width_cue_indices:
        warnings.append(
            f"{len(overflow_width_cue_indices)} cue(s) exceeded max_chars"
            f" ({options.max_chars})"
            " (see data.overflow_width_cue_indices for indices)."
            " Output without truncation to avoid information loss."
        )

    merged_count = len(merged_cue_indices)
    width_overflow_count = len(overflow_width_cue_indices)
    summary = (
        f"Phrase-boundary line breaks applied to {cue_count} cue(s)"
        f" ({merged_count} cue(s) collapsed to max_lines;"
        f" {width_overflow_count} cue(s) exceeded max_chars)."
        f" Language: {options.language}."
        f" Generated {output_path.name}."
    )

    # artifacts[path] returns absolute path so agents can chain tools;
    # consistent with silence/render (SR-L-3, accepted design).
    artifacts = [
        {"role": "captions", "path": str(output_path), "format": fmt},
    ]

    return ok_result(
        summary,
        data={
            "cue_count": cue_count,
            "wrapped_count": wrapped_count,
            "merged_cue_indices": merged_cue_indices,
            "overflow_width_cue_indices": overflow_width_cue_indices,
            "language": options.language,
        },
        artifacts=artifacts,
        warnings=warnings,
    )
