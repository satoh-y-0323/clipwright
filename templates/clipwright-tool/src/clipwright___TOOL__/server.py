"""server.py — clipwright-__TOOL__ MCP server + CLI entry point.

Delegates business logic to __TOOL__.py, a thin wrapper (spec §2.3).
Don't duplicate error conversion or input validation here (validation is responsibility of schemas / __TOOL__).

Transport is stdio by default (mcp.run(transport="stdio"), M absent SHOULD §6.7).
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright___TOOL__.__TOOL__ import __ACTION__
from clipwright___TOOL__.schemas import __Action__Options

# FastMCP instance (server name = clipwright-<tool>)
mcp = FastMCP("clipwright-__TOOL__")


# ===========================================================================
# clipwright___ACTION__ MCP Tool
# ===========================================================================
#
# Attach annotations honestly matching reality (§2 SHOULD).
#   - detect / inspect type (template default):
#       readOnlyHint=True / destructiveHint=False / idempotentHint=True
#   - render type (generates new file):
#       change readOnlyHint=False (keep destructive=False, input unchanged).
#   - openWorldHint: False for local deterministic, True if touching network/external APIs.


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright___ACTION__(
    input: Annotated[
        str,
        Field(description="Input file path (existing file)."),
    ],
    output: Annotated[
        str,
        Field(description="Output artifact path (newly generated, different from input)."),
    ],
    options: Annotated[
        __Action__Options | None,
        Field(description="Tool-specific options. Uses defaults if omitted."),
    ] = None,
) -> dict[str, Any]:
    """(TODO: Describe in 1-2 sentences tool purpose and input/output contract. Readable by AI.)

    Input file is not modified (non-destructive, readOnly).
    Delegates business logic to __TOOL__.__ACTION__.
    If options is None, uses default __Action__Options().
    """
    resolved_options = options if options is not None else __Action__Options()
    return __ACTION__(input=input, output=output, options=resolved_options)


# ===========================================================================
# Entry Point (MCP stdio startup)
# ===========================================================================


def main() -> None:
    """CLI entry point. Launches MCP server on stdio.

    Registered in pyproject.toml [project.scripts] as:
    clipwright-__TOOL__ = "clipwright___TOOL__.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
