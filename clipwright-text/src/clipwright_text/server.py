"""server.py — MCP server for clipwright-text text overlay annotation.

Exposes a single MCP tool: clipwright_add_text.
Delegates all business logic to text.add_text; no logic here.
"""

from __future__ import annotations

from typing import Annotated

from clipwright.envelope import error_result
from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_text.schemas import AddTextOptions
from clipwright_text.text import add_text

mcp = FastMCP("clipwright-text")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_add_text(
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
        AddTextOptions | None,
        Field(
            description=(
                "Text overlay options. text, start_sec, and duration_sec are "
                "required; all other fields have sensible defaults."
            )
        ),
    ] = None,
) -> ToolResult:
    """Add a text_overlay marker to an OTIO timeline.

    Later rendering by clipwright-render converts the marker to a drawtext filter.
    Writes a new OTIO timeline to output; the input timeline is never modified.
    Idempotent: calling with identical options on an already-annotated timeline
    produces applied=0 with a warning rather than duplicating the marker.
    Multiple distinct calls accumulate markers (text_0, text_1, ...) which
    clipwright-render reads to apply drawtext filters.
    """
    if options is None:
        return error_result(
            "INVALID_INPUT",
            "options is required but was not provided.",
            (
                "Pass options with at least text, start_sec, and duration_sec "
                '(e.g., {"text": "Hello", "start_sec": 1.0, "duration_sec": 3.0}).'
            ),
        )
    return add_text(timeline=timeline, output=output, options=options)


def main() -> None:
    """Entry point for the clipwright-text MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
