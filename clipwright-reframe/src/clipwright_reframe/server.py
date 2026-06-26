"""server.py — clipwright-reframe MCP server + CLI entry point.

Thin wrapper that delegates business logic to reframe.py.
ClipwrightError conversion is handled in reframe.py; no double conversion here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_reframe.reframe import reframe as _reframe
from clipwright_reframe.schemas import ReframeOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-reframe")


# ===========================================================================
# clipwright_reframe MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_reframe(
    media: Annotated[
        str,
        Field(description="Input video file path (video stream required)."),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension)."
                " The file is created (when timeline is None) or updated"
                " (when timeline is provided) at this path."
                " May be placed in any directory with an existing parent;"
                " no co-location with the media file is required."
            )
        ),
    ],
    options: Annotated[
        ReframeOptions,
        Field(
            description=(
                "Reframe options: target_w / target_h (required),"
                " mode ('crop'|'pad'|'blur_pad', default 'pad'),"
                " anchor (9-direction, default 'center'),"
                " pad_color (default 'black')."
            )
        ),
    ],
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "Existing OTIO timeline file path."
                " When specified, the reframe directive is appended to that timeline."
                " A new timeline is created when omitted."
            )
        ),
    ] = None,
) -> ToolResult:
    """Annotate a reframe directive on an OTIO timeline for clipwright-render.

    The input media file is never modified (non-destructive, readOnly).
    Writes a reframe directive to timeline-level metadata["clipwright"]["reframe"]
    specifying target resolution, fit mode, anchor, pad color, and — for
    mode='track' — a motion-centroid keyframe list.

    Modes:
      'crop'     — cover-then-crop (static, anchor-aligned).
      'pad'      — fit-then-letterbox/pillarbox (static, anchor-aligned).
      'blur_pad' — fit foreground over blurred background (static).
      'track'    — motion-centroid crop-from-source (requires [track] extra).
                   Spawns track_cli as a subprocess to detect motion centroids
                   using numpy.  When numpy is absent or detection fails, a
                   constant-center track is written as a graceful fallback;
                   ok remains True and a warning is included in the response.

    clipwright-render reads this directive and applies the corresponding ffmpeg
    filter during render.

    Returns the path of the resulting timeline.otio in artifacts.

    Delegates business logic to reframe._reframe_inner.
    """
    return _reframe(
        media=media,
        output=output,
        options=options,
        timeline=timeline,
    )


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-reframe = "clipwright_reframe.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
