"""server.py — clipwright-bgm MCP server + CLI entry point.

Thin wrapper that delegates business logic to bgm.py.
ClipwrightError conversion is handled in bgm.py, so no double-conversion is done here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_bgm.bgm import add_bgm
from clipwright_bgm.schemas import BgmOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-bgm")


# ===========================================================================
# clipwright_add_bgm MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_add_bgm(
    timeline: Annotated[
        str,
        Field(description="Input OTIO timeline file path (.otio)."),
    ],
    bgm: Annotated[
        str,
        Field(
            description=(
                "BGM file path (audio or video). "
                "Allowed extensions: mp3, wav, m4a, aac, flac, ogg, opus,"
                " mp4, mkv, mov, webm. "
                "May reside in any directory (external files accepted)."
            )
        ),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension). "
                "Must differ from both the input timeline and the bgm file "
                "(non-destructive accumulate contract, M5). "
                "May reside in any directory whose parent already exists."
            )
        ),
    ],
    options: Annotated[
        BgmOptions | None,
        Field(
            description=(
                "BGM options (volume_db / fade_in_sec / fade_out_sec / ducking). "
                "When omitted, the default values inside add_bgm are used."
            )
        ),
    ] = None,
) -> ToolResult:
    """MCP tool to add a BGM clip to an OTIO timeline.

    Not read-only because a new output OTIO file is created, but the input OTIO
    and media files are unchanged (non-destructive).
    Symmetric design with clipwright-render's readOnlyHint=False
    (new file generation, CR M-4).
    Fetches BGM duration via core inspect_media and adds a BGM clip
    to the A2 Audio track.
    Writes BgmDirective (volume_db/fade/ducking) into the BGM clip metadata.
    The actual mix is performed by clipwright-render.

    Delegates business logic to bgm.add_bgm.
    """
    return add_bgm(
        timeline=timeline,
        bgm=bgm,
        output=output,
        options=options,
    )


# ===========================================================================
# Entry point (MCP stdio)
# ===========================================================================


def main() -> None:
    """CLI entry point. Starts the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-bgm = "clipwright_bgm.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
