"""envelope.py — Return value envelope construction helpers.

Thin helpers that normalise all tool return values to the standard format (§6.3 / §6.4).
Returns dict representations of ToolResult / ToolErrorResult, which are directly
compatible with FastMCP JSON serialisation.
"""

from __future__ import annotations

from typing import Any


def ok_result(
    summary: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: list[Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a success envelope dict (§6.3 ToolResult form).

    summary must include key points an AI needs to decide the next action
    (counts, durations, maxima, etc.). Do not make it minimal.

    Args:
        summary: Key points of the result (required).
        data: Supplementary information (optional).
        artifacts: List of references to output files (optional).
        warnings: List of warning messages (optional).

    Returns:
        A dict of the form { ok: True, summary, data, artifacts, warnings }.
    """
    return {
        "ok": True,
        "summary": summary,
        "data": data if data is not None else {},
        "artifacts": artifacts if artifacts is not None else [],
        "warnings": warnings if warnings is not None else [],
    }


def error_result(code: str, message: str, hint: str) -> dict[str, Any]:
    """Build a failure envelope dict (§6.4 ToolErrorResult form).

    message describes what happened; hint describes the concrete next step.
    An empty hint violates the error contract (§6).

    Args:
        code: String representation of an ErrorCode value.
        message: What happened.
        hint: Concrete, actionable next step.

    Returns:
        A dict of the form { ok: False, error: { code, message, hint } }.
    """
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        },
    }
