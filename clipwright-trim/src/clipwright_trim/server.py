"""server.py — clipwright-trim MCP server entry point.

Thin wrapper that delegates all business logic to trim.trim_media.
No error conversion is performed here; trim_media is the sole boundary.

Transport: stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_trim.schemas import TrimOptions
from clipwright_trim.trim import trim_media

# FastMCP instance (server name matches package name)
mcp = FastMCP("clipwright-trim")


# ===========================================================================
# clipwright_trim MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_trim(
    media: Annotated[
        str,
        Field(
            description=(
                "Input media file path. Must be an existing regular file. "
                "ffprobe is used to obtain the duration and frame rate."
            )
        ),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension required). "
                "May be placed in any directory whose parent already exists; "
                "the output must not resolve to the same path as the input media. "
                "I/O contract: create (new OTIO timeline is always generated). "
                "The file is created or overwritten atomically."
            )
        ),
    ],
    options: Annotated[
        TrimOptions | None,
        Field(
            description=(
                "Keep/drop range specification and padding. "
                "keep: list of ranges to retain (enumeration order preserved). "
                "drop: list of ranges to remove (complement becomes keep). "
                "padding_sec: non-negative seconds applied to each boundary. "
                "When omitted or both keep/drop are empty, the full media duration "
                "is kept as a single clip (passthrough)."
            )
        ),
    ] = None,
) -> ToolResult:
    """MCP tool: generate a kept-range OTIO timeline from explicit time ranges.

    Non-destructive: does not modify the input media file.
    Produces a single-track (V1) OTIO timeline compatible with clipwright-render.
    Delegates all logic to trim.trim_media.
    """
    resolved = options if options is not None else TrimOptions()
    return trim_media(media=media, output=output, options=resolved)


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-trim = "clipwright_trim.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
