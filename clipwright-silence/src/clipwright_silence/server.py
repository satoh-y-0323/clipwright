"""server.py — clipwright-silence MCP server + CLI entry point.

A thin wrapper that delegates business logic to detect.py.
ClipwrightError conversion is handled on the detect.py side;
no double conversion is done here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_silence.detect import detect_silence
from clipwright_silence.schemas import DetectSilenceOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-silence")


# ===========================================================================
# clipwright_detect_silence MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_silence(
    media: Annotated[
        str,
        Field(description="Input media file path (source containing video and audio)."),
    ],
    output: Annotated[
        str,
        Field(description="Output OTIO timeline file path (.otio extension)."),
    ],
    options: Annotated[
        DetectSilenceOptions | None,
        Field(
            description=(
                "Silence detection options (silence_threshold_db / min_silence_duration"
                " / padding / min_keep_duration). All values use defaults when omitted."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """MCP tool: detect silence intervals and generate a KEEP interval OTIO timeline.

    Does not modify the input media file (non-destructive, readOnly).
    Output returns the path of the newly created timeline.otio in artifacts.

    Delegates business logic to detect.detect_silence.
    Uses default DetectSilenceOptions() when options is None.
    """
    resolved_options = options if options is not None else DetectSilenceOptions()
    return detect_silence(
        media=media,
        output=output,
        options=resolved_options,
    )


# ===========================================================================
# Entry point (MCP stdio launch / DC-GP-002)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio (DC-GP-002).

    Registered in pyproject.toml [project.scripts] as:
    clipwright-silence = "clipwright_silence.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
