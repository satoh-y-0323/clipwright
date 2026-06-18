"""server.py — clipwright-color MCP server + CLI entry point.

Thin wrapper that delegates business logic to color.py.
ClipwrightError conversion is handled in color.py; no double conversion here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_color.color import detect_color
from clipwright_color.schemas import DetectColorOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-color")


# ===========================================================================
# clipwright_detect_color MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_color(
    media: Annotated[
        str,
        Field(description="Input video file path (video stream required)."),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension)."
                " Must be placed in the same directory as the media file."
            )
        ),
    ],
    options: Annotated[
        DetectColorOptions | None,
        Field(
            description=(
                "Color detection options (target_luma / sample_interval_sec)."
                " Defaults to target_luma=128 / sample_interval_sec=1.0 when omitted."
            )
        ),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "Existing OTIO timeline file path."
                " When specified, the color directive is appended to that timeline."
                " A new timeline is created when omitted."
            )
        ),
    ] = None,
) -> ToolResult:
    """Analyze video brightness and generate an OTIO timeline with a color directive.

    The input media file is never modified (non-destructive, readOnly).
    Measures average luma with ffmpeg signalstats and writes a color directive
    to timeline-level metadata["clipwright"]["color"].
    Returns the path of the resulting timeline.otio in artifacts.

    Delegates business logic to color.detect_color.
    Uses default DetectColorOptions() when options is None.
    """
    resolved_options = options if options is not None else DetectColorOptions()
    return detect_color(
        media=media,
        output=output,
        options=resolved_options,
        timeline=timeline,
    )


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-color = "clipwright_color.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
