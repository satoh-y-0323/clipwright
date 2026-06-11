"""server.py — MCP server and CLI entry point for clipwright-render.

Thin wrapper that delegates all business logic to render.py.
ClipwrightError conversion is done on the render.py side; double conversion is
not performed here.

Default transport is stdio (mcp.run(transport="stdio")).
The CLI parses arguments with argparse and calls render_timeline.
"""

from __future__ import annotations

import argparse
import sys
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

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
        Field(description="Input OTIO timeline file path."),
    ],
    output: Annotated[
        str,
        Field(description="Output video file path (.mp4/.mkv/.mov/.webm)."),
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
) -> dict[str, Any]:
    """MCP tool that materialises an OTIO timeline with FFmpeg.

    Non-destructive: the input timeline file and source media are never modified.
    The output is a newly generated video file whose path is returned in artifacts.

    Business logic is delegated to render.render_timeline.
    When options is None, default RenderOptions() is used.
    """
    resolved_options = options if options is not None else RenderOptions()
    return render_timeline(
        timeline=timeline,
        output=output,
        options=resolved_options,
        dry_run=dry_run,
    )


# ===========================================================================
# CLI entry point (DC-GP-003 / §6.3)
# ===========================================================================


def main() -> None:
    """CLI entry point.

    clipwright-render <timeline> <output> [--dry-run] [--video-codec C]
      [--audio-codec C] [--width W --height H] [--fps F] [--crf N] [--overwrite]

    Parses arguments with argparse, builds RenderOptions, and calls render_timeline.
    Shares render.py logic with the MCP tool (DC-GP-003).
    """
    parser = argparse.ArgumentParser(
        prog="clipwright-render",
        description="Materialise an OTIO timeline with FFmpeg",
    )

    # Positional arguments
    parser.add_argument("timeline", help="Input OTIO timeline file path")
    parser.add_argument("output", help="Output video file path")

    # Optional arguments
    parser.add_argument(
        "--dry-run", action="store_true", help="Return plan only (ffmpeg not executed)"
    )
    parser.add_argument(
        "--video-codec", dest="video_codec", metavar="C", help="Output video codec"
    )
    parser.add_argument(
        "--audio-codec", dest="audio_codec", metavar="C", help="Output audio codec"
    )
    parser.add_argument(
        "--width", type=int, metavar="W", help="Output video width (pair with height)"
    )
    parser.add_argument(
        "--height", type=int, metavar="H", help="Output video height (pair with width)"
    )
    parser.add_argument("--fps", type=float, metavar="F", help="Output frame rate")
    parser.add_argument("--crf", type=int, metavar="N", help="Video quality (CRF 0-51)")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing output file"
    )

    args = parser.parse_args()

    # Build RenderOptions with Pydantic (includes validation)
    try:
        options = RenderOptions(
            video_codec=args.video_codec,
            audio_codec=args.audio_codec,
            width=args.width,
            height=args.height,
            fps=args.fps,
            crf=args.crf,
            overwrite=args.overwrite,
        )
    except ValidationError as exc:
        # Do not expose Pydantic internal details; show only the invalid field
        # names (Sec Low-2)
        fields = ", ".join(str(loc[0]) for e in exc.errors() if (loc := e.get("loc")))
        detail = (
            f"{fields} value(s) do not satisfy constraints"
            if fields
            else "input is invalid"
        )
        print(
            f"Option validation error: {detail}. Run with --help for usage.",
            file=sys.stderr,
        )
        sys.exit(1)

    result = render_timeline(
        timeline=args.timeline,
        output=args.output,
        options=options,
        dry_run=args.dry_run,
    )

    if result.get("ok"):
        print(result.get("summary", ""))
    else:
        error = result.get("error", {})
        print(f"Error: {error.get('message', '')}", file=sys.stderr)
        print(f"Hint: {error.get('hint', '')}", file=sys.stderr)
        sys.exit(1)


# ===========================================================================
# Entry point (MCP stdio)
# ===========================================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
