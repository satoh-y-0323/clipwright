"""server.py — clipwright-sequence MCP server entry point.

Thin wrapper that delegates all business logic to sequence.build_sequence.
No error conversion is performed here; build_sequence is the sole boundary.

Transport: stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

# FastMCP instance (server name matches package name)
mcp = FastMCP("clipwright-sequence")


# ===========================================================================
# clipwright_build_sequence MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_build_sequence(
    clips: Annotated[
        list[SequenceClip],
        Field(
            description=(
                "Ordered list of clip specifications to assemble into a sequence. "
                "Each entry identifies a source media file and an optional sub-range. "
                "Maximum 1000 clips per call (DC-GP-003)."
            ),
            max_length=1000,
        ),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension required). "
                "Must be in the same directory as the source media files. "
                "The file is created or overwritten atomically."
            )
        ),
    ],
) -> ToolResult:
    """MCP tool: assemble an ordered list of clips into a single-track OTIO timeline.

    Non-destructive: does not modify the input media files.
    Produces a single V1 video track OTIO timeline consumed by clipwright-render.
    total_duration_sec in the result data is an approx estimate based on input
    clip ranges; the rendered output duration may differ after normalization.
    Symlink sources are unsupported; resolve symlinks before passing to this tool.
    Delegates all logic to sequence.build_sequence.
    """
    return build_sequence(clips=clips, output=output)


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-sequence = "clipwright_sequence.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
