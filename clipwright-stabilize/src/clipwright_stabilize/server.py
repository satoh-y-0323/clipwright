"""server.py — clipwright-stabilize MCP server + CLI entry point.

Thin wrapper that delegates business logic to stabilize.py.
ClipwrightError conversion is handled in stabilize.py; no double conversion here.

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from typing import Annotated

from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_stabilize.schemas import DetectShakeOptions
from clipwright_stabilize.stabilize import detect_shake

# FastMCP instance (server name)
mcp = FastMCP("clipwright-stabilize")


# ===========================================================================
# clipwright_detect_shake MCP tool
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,  # .trf binary + .otio are generated as side-products
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_shake(
    media: Annotated[
        str,
        Field(description="Input video file path (video stream required)."),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "Output OTIO timeline file path (.otio extension, create type)."
                " Output may be placed in any directory (parent dir must exist);"
                " must not equal the media or timeline path."
            )
        ),
    ],
    options: Annotated[
        DetectShakeOptions | None,
        Field(
            description=(
                "Shake detection options (shakiness / accuracy / smoothing)."
                " Defaults to shakiness=5 / accuracy=15 / smoothing=12 when omitted."
            )
        ),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "Existing OTIO timeline file path."
                " When specified, the stabilize directive is appended to that timeline."
                " A new timeline is created when omitted."
            )
        ),
    ] = None,
) -> ToolResult:
    """Analyze video shake and generate an OTIO timeline with a stabilize directive.

    The input media file is never modified (non-destructive, readOnly).
    Requires an ffmpeg build compiled with --enable-libvidstab.
    Runs ffmpeg vidstabdetect to generate a .trf transform file and writes a
    stabilize directive to timeline-level metadata["clipwright"]["stabilize"].
    Returns paths of the resulting timeline.otio and analysis.trf in artifacts.
    The .trf is consumed by clipwright-render (vidstabtransform) to apply stabilization.

    Output contract (data keys):
      severity        — float in [0.0, 1.0] (best-effort; None when .trf unparseable).
      recommendation  — "skip" | "apply" (advisory; "apply" when severity is None).
                        The calling agent makes the final decision on whether to
                        apply stabilization; recommendation is advisory only.
      shakiness       — int (1-10): vidstabdetect shakiness used for detection.
      accuracy        — int (1-15): vidstabdetect accuracy used for detection.
      smoothing       — int (0-1000): vidstabtransform smoothing consumed by render.
      trf_basename    — str: basename of the generated .trf analysis file.

    Delegates business logic to stabilize.detect_shake.
    Uses default DetectShakeOptions() when options is None.
    """
    resolved_options = options if options is not None else DetectShakeOptions()
    return detect_shake(
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
    clipwright-stabilize = "clipwright_stabilize.server:main"
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
