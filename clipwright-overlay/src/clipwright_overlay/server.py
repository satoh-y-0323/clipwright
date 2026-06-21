"""server.py — MCP server for clipwright-overlay image overlay annotation.

Exposes a single MCP tool: clipwright_add_overlay.
Delegates all business logic to overlay.add_overlay; no logic here.
"""

from __future__ import annotations

from typing import Annotated

from clipwright.envelope import error_result
from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_overlay.overlay import add_overlay
from clipwright_overlay.schemas import AddOverlayOptions

mcp = FastMCP("clipwright-overlay")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_add_overlay(
    timeline: Annotated[str, Field(description="Input OTIO timeline file path.")],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO file path where the annotated timeline is written. "
                "Must end in .otio and differ from the input timeline path."
            )
        ),
    ],
    options: Annotated[
        AddOverlayOptions | None,
        Field(
            description=(
                "Image overlay options. image_path, start_sec, and duration_sec are "
                "required; all other fields have sensible defaults."
            )
        ),
    ] = None,
) -> ToolResult:
    """Add an image_overlay marker to an OTIO timeline.

    readOnlyHint=True: this tool writes only a new .otio output file; the input
    media and the input timeline are never modified. The new-file write is outside
    the readOnly scope per the MCP annotation contract — readOnly refers to
    existing resources, and the output is a freshly created file.

    Later rendering by clipwright-render materializes image_overlay markers as
    ffmpeg overlay filters. Idempotent: calling with identical options on an
    already-annotated timeline produces applied=0 with a warning rather than
    duplicating the marker. Multiple distinct calls accumulate image_0/image_1/...
    markers which clipwright-render reads to apply overlay filters.
    """
    if options is None:
        return error_result(
            "INVALID_INPUT",
            "options is required but was not provided.",
            (
                "Pass options with at least image_path, start_sec, and duration_sec "
                '(e.g., {"image_path": "/path/to/logo.png", "start_sec": 1.0, '
                '"duration_sec": 3.0}).'
            ),
        )
    result = add_overlay(timeline=timeline, output=output, options=options)
    if isinstance(result, ToolResult):
        return result
    return ToolResult.model_validate(result)


def main() -> None:
    """Entry point for the clipwright-overlay MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
