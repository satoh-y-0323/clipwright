"""server.py — clipwright-wrap MCP server + CLI entry point.

A thin wrapper that delegates business logic to wrap.py.
ClipwrightError conversion and language validation are handled by wrap.py / schemas.py,
so this module does not perform double conversion (DC-GP-001).

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_wrap.schemas import WrapCaptionsOptions
from clipwright_wrap.wrap import wrap_captions

# FastMCP instance (server name)
mcp = FastMCP("clipwright-wrap")


# ===========================================================================
# clipwright_wrap_captions MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_wrap_captions(
    input: Annotated[
        str,
        Field(description="Input subtitle file path (.srt or .vtt)."),
    ],
    output: Annotated[
        str,
        Field(description="Output subtitle file path (same extension as input)."),
    ],
    options: Annotated[
        WrapCaptionsOptions | None,
        Field(
            description=(
                "Phrase-boundary line-break options"
                " (language / max_chars / max_lines). "
                "When omitted, all defaults are used"
                " (language='ja' / max_chars=16 / max_lines=2)."
            )
        ),
    ] = None,
) -> ToolResult:
    """MCP tool: insert phrase-boundary line breaks into a subtitle file.

    The input subtitle file is never modified (non-destructive; readOnly).
    The output is the path of the newly generated SRT/VTT, returned in artifacts.

    Business logic is delegated to wrap.wrap_captions.
    When options is None, the default WrapCaptionsOptions() is used.
    """
    resolved_options = options if options is not None else WrapCaptionsOptions()
    return wrap_captions(
        input=input,
        output=output,
        options=resolved_options,
    )


# ===========================================================================
# Entry point (MCP stdio launch)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches the MCP server over stdio.

    Registered in pyproject.toml [project.scripts] as
    clipwright-wrap = "clipwright_wrap.server:main".
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
