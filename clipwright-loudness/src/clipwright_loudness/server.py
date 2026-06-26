"""server.py — clipwright-loudness MCP server + CLI entry point.

Thin wrapper that delegates business logic to loudness.py.
ClipwrightError conversion is handled in loudness.py; no double conversion here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_loudness.loudness import detect_loudness
from clipwright_loudness.schemas import DetectLoudnessOptions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-loudness")


# ===========================================================================
# clipwright_detect_loudness MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_loudness(
    media: Annotated[
        str,
        Field(description="Input media file path (must contain both video and audio)."),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension, create type). "
                "Output may be placed in any directory (parent dir must exist); "
                "must not equal the media or timeline path."
            )
        ),
    ],
    options: Annotated[
        DetectLoudnessOptions | None,
        Field(
            description=(
                "Loudness detection options (mode / scope / target, etc.). "
                "Defaults to mode=loudnorm / scope=track /"
                " I=-14 / TP=-1 / LRA=11 when omitted."
            )
        ),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "Existing OTIO timeline file path. "
                "When specified, the loudness directive is appended to that timeline. "
                "A new timeline is created when omitted."
            )
        ),
    ] = None,
) -> ToolResult:
    """Analyze audio loudness and generate an OTIO timeline with a loudness directive.

    The input media file is never modified (non-destructive, readOnly).
    readOnlyHint=True means "the input media is not changed"; the .otio file
    specified in output is newly created (outside the readOnly scope).
    Measures loudness with ffmpeg loudnorm/volumedetect and writes the loudness
    directive to timeline-level metadata["clipwright"]["loudness"].
    Returns the path of the resulting timeline.otio in artifacts.

    Delegates business logic to loudness.detect_loudness.
    Uses default DetectLoudnessOptions() when options is None.
    """
    resolved_options = options if options is not None else DetectLoudnessOptions()
    return detect_loudness(
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
    clipwright-loudness = "clipwright_loudness.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
