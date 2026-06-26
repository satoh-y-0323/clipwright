"""server.py — MCP server entry point for clipwright-render.

Thin wrapper that delegates all business logic to render.py.
ClipwrightError conversion is done on the render.py side; double conversion is
not performed here.

Default transport is stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-render")


# ===========================================================================
# clipwright_render MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_render(
    timeline: Annotated[
        str,
        Field(
            description=(
                "Input OTIO timeline file path. The timeline directory is used"
                " as the base for resolving relative media references; absolute"
                " paths to existing real files are accepted regardless of location"
                " (ADR-PP-1)."
            )
        ),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output video file path (.mp4/.mkv/.mov/.webm). Must differ from"
                " all source media paths (non-destructive; DC-AM-002)."
            )
        ),
    ],
    options: Annotated[
        RenderOptions | None,
        Field(
            description=(
                "Rendering options (codec/resolution/fps/crf/overwrite)."
                " When omitted, all settings inherit from the source (ffmpeg defaults)."
            )
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(description="When True, returns the plan only without executing ffmpeg."),
    ] = False,
) -> ToolResult:
    """MCP tool that materialises an OTIO timeline with FFmpeg.

    Non-destructive: the input timeline file and source media are never modified.
    The output is a newly generated video file whose path is returned in artifacts.

    I/O contract:
    - Input: OTIO timeline (.otio). Source media, subtitle, and image overlay
      references embedded in the timeline are resolved at materialise time.
      Absolute refs to existing real files are accepted anywhere (ADR-PP-1).
      Relative refs must resolve within the timeline directory (CWE-22 guard).
    - Output: encoded video file at the specified output path. The timeline and
      source files are never modified.

    Business logic is delegated to render.render_timeline.
    When options is None, default RenderOptions() is used.
    render_timeline always returns a ToolResult; no conversion is needed here.

    Workflow note: when burning captions onto silence-cut footage, the recommended
    order is to render the cut first, then transcribe the rendered video, then render
    again with subtitles — rather than transcribing the original source and relying
    on retime_markers="auto". retime_markers can re-time cues to program coordinates
    (only .srt subtitles are re-timed; .vtt and .ass are skipped), but cuts that fall
    mid-phrase still produce split or clipped captions and trigger a warning containing
    "fragmented by cuts". See README "Recommended Workflows".
    """
    resolved_options = options if options is not None else RenderOptions()
    return render_timeline(
        timeline=timeline,
        output=output,
        options=resolved_options,
        dry_run=dry_run,
    )


# ===========================================================================
# Entry point (MCP stdio)
# ===========================================================================


def main() -> None:
    """Entry point. Starts the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()  # pragma: no cover
