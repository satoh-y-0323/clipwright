"""server.py — MCP server for clipwright-speed timeline speed changes.

Exposes a single MCP tool: clipwright_set_speed.
Delegates all business logic to speed.set_speed; no logic here.
"""

from __future__ import annotations

from typing import Annotated

from clipwright.envelope import error_result
from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_speed.schemas import SetSpeedOptions
from clipwright_speed.speed import set_speed

mcp = FastMCP("clipwright-speed")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_set_speed(
    timeline: Annotated[str, Field(description="Input OTIO timeline file path.")],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO file path (transform I/O contract: new file, "
                "input unchanged). May be in any directory whose parent exists. "
                "Must end in .otio and must not resolve to the same file as timeline."
            )
        ),
    ],
    options: Annotated[
        SetSpeedOptions | None,
        Field(
            description=(
                "Speed options. speed is required; clip_index is optional "
                "(None applies to all clips)."
            )
        ),
    ] = None,
) -> ToolResult:
    """Apply a LinearTimeWarp speed change to clips in an OTIO timeline.

    Writes a new OTIO timeline to output; the input timeline is never modified.
    Idempotent: applying twice with the same speed replaces rather than stacks
    the clipwright warp on each clip.
    """
    if options is None:
        # speed is required in SetSpeedOptions, so None options cannot resolve to
        # a valid default. Return an error envelope.
        return error_result(
            "INVALID_INPUT",
            "options.speed is required but options was not provided.",
            "Pass options with a speed value in the range 0.25-8.0.",
        )
    return set_speed(timeline=timeline, output=output, options=options)


def main() -> None:
    """Entry point for the clipwright-speed MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
