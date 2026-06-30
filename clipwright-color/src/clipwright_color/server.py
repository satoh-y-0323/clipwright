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
                "Output OTIO timeline file path (.otio extension, create type)."
                " Output may be placed in any directory (parent dir must exist);"
                " must not equal the media or timeline path."
            )
        ),
    ],
    options: Annotated[
        DetectColorOptions | None,
        Field(
            description=(
                "Color detection options. Defaults applied when omitted."
                " target_luma (0-255, default 128): target average luma."
                " sample_interval_sec (default 1.0): ffmpeg frame-sampling interval."
                " saturation/contrast/gamma: eq override in caller-supplied range."
                " temperature/tint: white-balance override as normalised [-1,1] axes"
                " (NOT Kelvin); temperature +1=warm/red, -1=cool/blue;"
                " tint +1=magenta, -1=green."
                " lut: path to a .cube 3D-LUT file to embed in the directive."
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
    Measures average luma (and chroma when available) with ffmpeg signalstats
    and writes a color directive to timeline-level metadata["clipwright"]["color"].
    Returns the path of the resulting timeline.otio in artifacts.

    White-balance override: temperature and tint are normalised [-1, 1] axes
    (mapped to per-channel gain, NOT a colour-temperature scale).
    temperature +1 = warm/red, -1 = cool/blue.
    tint +1 = magenta, -1 = green.
    When neither is supplied, white balance is derived automatically from
    measured chroma (gray-world correction, §4.2).

    eq options (saturation/contrast/gamma) populate the ffmpeg eq filter
    directive. lut specifies a .cube 3D-LUT file to apply at render time.

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
