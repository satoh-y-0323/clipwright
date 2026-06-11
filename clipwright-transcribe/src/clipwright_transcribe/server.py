"""server.py — clipwright-transcribe MCP server + CLI entry point.

Thin wrapper that delegates business logic to transcribe.py.
ClipwrightError conversion is handled in transcribe.py; no double conversion here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import transcribe_media

# FastMCP instance (server name)
mcp = FastMCP("clipwright-transcribe")


# ===========================================================================
# clipwright_transcribe MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_transcribe(
    media: Annotated[
        str,
        Field(
            description="Input media file path (must contain audio; video is optional)."
        ),
    ],
    output: Annotated[
        str,
        Field(description="Output OTIO timeline file path (.otio extension required)."),
    ],
    options: Annotated[
        TranscribeOptions | None,
        Field(
            description=(
                "Transcription options (language / model_path / initial_prompt). "
                "When omitted, all fields use their defaults "
                "(auto language detection, model from env)."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """MCP tool: transcribe audio and produce SRT/VTT captions and an OTIO timeline.

    Non-destructive (readOnly): the input media file is never modified.
    Outputs are newly created files; their paths are returned in artifacts.

    Business logic is delegated to transcribe.transcribe_media.
    When options is None, default TranscribeOptions() is used.
    """
    resolved_options = options if options is not None else TranscribeOptions()
    return transcribe_media(
        media=media,
        output=output,
        options=resolved_options,
    )


# ===========================================================================
# Entry point (MCP stdio)
# ===========================================================================


def main() -> None:
    """CLI entry point. Starts the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-transcribe = "clipwright_transcribe.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
