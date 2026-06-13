"""envelope.py — Return value envelope construction helpers.

Thin helpers that normalise all tool return values to the standard ToolResult
envelope (§6.3 / §6.4). Functions return typed ToolResult instances, which
FastMCP serialises to structuredContent with a typed outputSchema.
"""

from __future__ import annotations

from typing import Any

from clipwright.schemas import Artifact, ToolError, ToolResult


def ok_result(
    summary: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: list[Any] | None = None,
    warnings: list[str] | None = None,
) -> ToolResult:
    """Build a success ToolResult (§6.3).

    summary must include key points an AI needs to decide the next action
    (counts, durations, maxima, etc.). Do not make it minimal.

    artifacts may be passed as ``list[Artifact]`` or ``list[dict[str, Any]]``.
    Dicts are coerced to Artifact via Pydantic validation; extra keys are
    silently ignored (M-002, Artifact.model_config extra="ignore").

    Args:
        summary: Key points of the result (required).
        data: Supplementary information (optional).
        artifacts: List of references to output files (optional).
        warnings: List of warning messages (optional).

    Returns:
        A ToolResult with ok=True and error=None.
    """
    coerced_artifacts: list[Artifact] = []
    if artifacts is not None:
        for a in artifacts:
            if isinstance(a, Artifact):
                coerced_artifacts.append(a)
            else:
                coerced_artifacts.append(Artifact.model_validate(a))

    return ToolResult(
        ok=True,
        summary=summary,
        data=data if data is not None else {},
        artifacts=coerced_artifacts,
        warnings=warnings if warnings is not None else [],
    )


def error_result(code: str, message: str, hint: str) -> ToolResult:
    """Build a failure ToolResult (§6.4).

    message describes what happened; hint describes the concrete next step.
    An empty hint violates the error contract (§6).

    Args:
        code: String representation of an ErrorCode value.
        message: What happened.
        hint: Concrete, actionable next step.

    Returns:
        A ToolResult with ok=False and error populated.
    """
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message=message, hint=hint),
    )


def to_tool_result(d: dict[str, Any]) -> ToolResult:
    """Convert a raw dict to a typed ToolResult (M-001).

    Used at satellite tool boundaries where the satellite returns a dict
    (legacy or cross-process) that must be lifted into the typed envelope.

    Input contract:
    - ``ok`` key must be present (bool); absence is an internal bug and raises.
    - Success form: ``{ok: True, summary, data?, artifacts?, warnings?}``.
      Artifact dicts in ``artifacts`` are coerced via Pydantic (extra keys ignored).
    - Failure form: ``{ok: False, error: {code, message, hint}}``.
    - Extra top-level keys are ignored (model_validate with extra="ignore" semantics
      deferred to future Pydantic config if needed; currently raises for unknown keys).

    Args:
        d: Raw dict conforming to the ToolResult envelope shape.

    Returns:
        A ToolResult instance.

    Raises:
        ValidationError: If ``d`` does not match the ToolResult schema.
    """
    return ToolResult.model_validate(d)
