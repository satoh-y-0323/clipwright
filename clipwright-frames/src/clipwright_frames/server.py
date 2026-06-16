"""server.py — MCP server for clipwright-frames still-frame extraction.

Exposes a single MCP tool: clipwright_extract_frames.
Delegates all business logic to extract.extract_frames; no logic here.
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_frames.extract import extract_frames
from clipwright_frames.schemas import ExtractFramesOptions

mcp = FastMCP("clipwright-frames")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_extract_frames(
    media: Annotated[str, Field(description="Input video file path.")],
    output_dir: Annotated[
        str,
        Field(
            description=(
                "Existing output directory path where frames and artifacts are written."
            )
        ),
    ],
    options: Annotated[
        ExtractFramesOptions | None,
        Field(description="Extraction options (mode, format, quality, etc.)."),
    ] = None,
) -> ToolResult:
    """Extract still frames from a video file; write an OTIO timeline + JSON manifest.

    Writes frames and artifact files to output_dir; input media is never modified.
    """
    resolved_options = options if options is not None else ExtractFramesOptions()
    return extract_frames(
        media=media,
        output_dir=output_dir,
        options=resolved_options,
    )


def main() -> None:
    """Entry point for the clipwright-frames MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
