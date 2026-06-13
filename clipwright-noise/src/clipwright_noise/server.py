"""server.py — clipwright-noise MCP server + CLI entry point.

A thin wrapper that delegates business logic to noise.py.
ClipwrightError conversion is handled in noise.py; no double conversion here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_noise.noise import detect_noise
from clipwright_noise.schemas import DetectNoiseOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-noise")


# ===========================================================================
# clipwright_detect_noise MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_noise(
    media: Annotated[
        str,
        Field(description="Input media file path (must contain video and audio)."),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension). "
                "Must be placed in the same directory as the media file."
            )
        ),
    ],
    options: Annotated[
        DetectNoiseOptions | None,
        Field(
            description=(
                "Noise detection options (backend / strength). "
                "Defaults to backend=afftdn / strength=medium when omitted."
            )
        ),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "Existing OTIO timeline file path. "
                "When specified, the denoise directive is appended to that timeline. "
                "A new timeline is generated when omitted."
            )
        ),
    ] = None,
) -> ToolResult:
    """MCP tool: analyzes audio noise and generates a denoise-annotated OTIO timeline.

    The input media file is never modified (non-destructive / readOnly).
    Measures the noise floor via ffmpeg astats, calculates backend-specific parameters,
    and writes them to timeline-level metadata["clipwright"]["denoise"].
    Returns the path to the annotated timeline.otio in artifacts.

    Business logic is delegated to noise.detect_noise.
    When options is None, the default DetectNoiseOptions() is used.
    """
    resolved_options = options if options is not None else DetectNoiseOptions()
    return detect_noise(
        media=media,
        output=output,
        options=resolved_options,
        timeline=timeline,
    )


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Starts the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-noise = "clipwright_noise.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
