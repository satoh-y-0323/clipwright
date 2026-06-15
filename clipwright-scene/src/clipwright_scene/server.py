"""server.py — MCP server for clipwright-scene shot boundary detection.

Exposes a single MCP tool: clipwright_detect_scenes.
Delegates all business logic to detect.detect_scenes; no logic here.
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_scene.detect import detect_scenes
from clipwright_scene.schemas import DetectScenesOptions

mcp = FastMCP("clipwright-scene")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_scenes(
    media: Annotated[str, Field(description="Input video/audio file path.")],
    output: Annotated[
        str, Field(description="Output OTIO timeline file path (.otio).")
    ],
    options: Annotated[
        DetectScenesOptions | None,
        Field(description="Detection options."),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(description="Existing OTIO timeline path to augment."),
    ] = None,
) -> ToolResult:
    """Detect shot boundaries and record as OTIO markers.

    Writes a new OTIO timeline to output; input media is never modified.
    """
    resolved_options = options if options is not None else DetectScenesOptions()
    return detect_scenes(
        media=media,
        output=output,
        options=resolved_options,
        timeline=timeline,
    )


def main() -> None:
    """Entry point for the clipwright-scene MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
