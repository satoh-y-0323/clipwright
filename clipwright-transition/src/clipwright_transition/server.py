"""server.py — clipwright-transition MCP server entry point.

Thin wrapper that delegates all business logic to transition.add_transition.
No error conversion is performed here; add_transition is the sole boundary
(ADR-T-1).

Transport: stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_transition.schemas import AddTransitionOptions
from clipwright_transition.transition import add_transition

# FastMCP instance (server name matches package name)
mcp = FastMCP("clipwright-transition")


# ===========================================================================
# clipwright_add_transition MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        # readOnlyHint=True: OTIO-only output; input media and source OTIO unchanged.
        # Convention for non-render tools (render uses readOnlyHint=False).
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_add_transition(
    timeline: Annotated[
        str,
        Field(
            description=(
                "Input OTIO timeline file path (.otio extension required). "
                "Must contain exactly one video track with at least two Clips "
                "and no pre-existing OTIO Transition objects."
            )
        ),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension required). "
                "Must differ from the input timeline path. "
                "The file is created atomically; the input OTIO is not modified."
            )
        ),
    ],
    options: Annotated[
        AddTransitionOptions,
        Field(
            description=(
                "Transition configuration. "
                "Provide exactly one of 'uniform' (same transition at every boundary) "
                "or 'per_boundary' (non-empty list of per-boundary specs)."
            )
        ),
    ],
) -> ToolResult:
    """MCP tool: apply transition directives to an OTIO timeline and write a new file.

    Non-destructive: the input timeline file is never modified.
    Produces a new OTIO file with transition metadata consumed by clipwright-render.
    Delegates all logic to transition.add_transition.
    """
    return ToolResult.model_validate(
        add_transition(timeline=timeline, output=output, options=options)
    )


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-transition = "clipwright_transition.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
