"""__TOOL__.py — clipwright-__TOOL__ orchestration layer.

Input/output validation → (if needed) OSS subprocess launch → result normalization →
artifact write → envelope return. This is the "adapter body" of "thin wrapper/thick adapter"
(spec §2.3). Keep MCP protocol face (server.py) thin.

CONVENTIONS MUST correspondence:
- M2 return value envelope: use ok_result / error_result from clipwright.envelope.
- M3 separation of detection and application: detect/inspect types don't modify media, just return annotations.
- M4 external OSS via subprocess: don't import in main, launch __TOOL___cli.py as separate process.
- M5 non-destructive: read input only, generate output freshly, reject output == input.

Path validation delegates to clipwright.pathpolicy (never re-implement).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.pathpolicy import check_output_not_source, validate_source_or_basename

from clipwright___TOOL__.schemas import __Action__Options

# Sanitized message on subprocess failure/timeout (prevents stderr path/secret leaks, CWE-209)
_SUBPROCESS_SAFE_MESSAGE = "internal subprocess failed"

# OSS invocation timeout (seconds). If linking to input size, calculate from cue count etc.
_TIMEOUT_SECONDS = 60.0


def __ACTION__(
    input: str,
    output: str,
    options: __Action__Options,
) -> dict[str, Any]:
    """(TODO: Describe in one sentence what this tool does. Example: Detect ~ and return JSON annotation.)

    Non-destructive: input file is read-only, not modified (M5).
    Output returns path of freshly generated artifact in artifacts.

    Args:
        input: Input file path (existing file).
        output: Output artifact path (newly generated, different from input).
        options: __Action__Options.

    Returns:
        ok_result or error_result envelope dict.
    """
    try:
        return ___ACTION___inner(input, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def ___ACTION___inner(
    input: str,
    output: str,
    options: __Action__Options,
) -> dict[str, Any]:
    """__ACTION__ internal implementation. Raises ClipwrightError as-is."""
    input_path = Path(input)
    output_path = Path(output)

    # --- 1. Output validation (M5) ---

    # Check output extension (match tool output format. Template is JSON).
    if output_path.suffix.lower() != ".json":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Unsupported output extension: {output_path.suffix!r}",
            hint="Change output file extension to .json.",
        )

    # Check output parent directory exists
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Output directory does not exist.",
            hint="Create output directory first, then retry.",
        )

    # Reject output == input (non-destructive, M5)
    check_output_not_source(output_path, [input])

    # --- 2. Input existence check (FILE_NOT_FOUND message basename only, no path exposure) ---

    validate_source_or_basename(
        input,
        message=f"File not found: {input_path.name}",
        hint="Verify input file path is correct.",
    )

    # --- 3. Detection/analysis body ---
    #
    # TODO: Perform actual processing here.
    #   - If using external OSS: launch via _run_cli() as separate process (M4).
    #     For pure Python processing without OSS, implement this block directly.
    #   - detect/inspect types don't modify media, only generate annotation data (M3).
    #
    # Template generates dummy result.
    result_data: dict[str, Any] = {
        "input": input_path.name,
        "threshold": options.example_threshold,
        "detections": [],  # TODO: Replace with actual detection results
    }

    # --- 4. Artifact write (large details go to file, not data, §2 SHOULD) ---

    output_path.write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- 5. Envelope construction (summary is 1-2 sentences sufficient for judgment, §2 SHOULD) ---

    detection_count = len(result_data["detections"])
    summary = (
        f"Analyzed {input_path.name} and detected {detection_count} items. "
        f"Result written to {output_path.name}."
    )

    artifacts = [
        {"role": "analysis", "path": str(output_path), "format": "json"},
    ]

    return ok_result(
        summary,
        data={"detection_count": detection_count},
        artifacts=artifacts,
        warnings=[],
    )


def _run_cli(payload: dict[str, Any]) -> dict[str, Any]:
    """Launch __TOOL___cli.py as separate process and return stdout JSON (M4, reference implementation).

    Only tools using OSS use this helper. Call from __ACTION___inner TODO.
    cli always returns 0; failures expressed as "error" key in stdout JSON.
    """
    stdin_payload = json.dumps(payload, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "clipwright___TOOL__.__TOOL___cli"],
            input=stdin_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"{_SUBPROCESS_SAFE_MESSAGE} (timeout)",
            hint="Input size may be too large. Try again.",
        ) from None
    except OSError:
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=_SUBPROCESS_SAFE_MESSAGE,
            hint="Failed to launch CLI shim. Verify installation.",
        ) from None

    try:
        parsed: dict[str, Any] = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=_SUBPROCESS_SAFE_MESSAGE,
            hint="Failed to parse CLI shim output JSON. Retry.",
        ) from None

    if "error" in parsed:
        err = parsed["error"]
        code_str: str = err.get("code", str(ErrorCode.INTERNAL))
        msg: str = err.get("message", "CLI shim encountered an error")
        hint: str = err.get("hint", "Report with reproduction steps.")
        try:
            code = ErrorCode(code_str)
        except ValueError:
            code = ErrorCode.INTERNAL
        raise ClipwrightError(code=code, message=msg, hint=hint)

    return parsed
