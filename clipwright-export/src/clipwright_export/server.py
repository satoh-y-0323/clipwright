"""server.py — MCP server for clipwright-export timeline/chapter export.

Exposes two MCP tools: clipwright_export_timeline and clipwright_export_chapters.
Both delegate all business logic to their domain functions
(timeline_export.export_timeline / chapters.export_chapters); no logic here.
"""

from __future__ import annotations

from typing import Annotated

from clipwright.envelope import error_result
from clipwright.schemas import ToolResult
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_export.chapters import export_chapters
from clipwright_export.schemas import ExportChaptersOptions, ExportTimelineOptions
from clipwright_export.timeline_export import export_timeline

mcp = FastMCP("clipwright-export")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_export_timeline(
    timeline: Annotated[str, Field(description="Input OTIO timeline file path.")],
    output: Annotated[
        str,
        Field(
            description=(
                "Output exchange file path. Its extension must match the format "
                "(edl -> .edl, fcpxml -> .fcpxml) and must differ from the input "
                "timeline path. The input OTIO and its media are never modified; "
                "only this new sidecar file is written."
            )
        ),
    ],
    options: Annotated[
        ExportTimelineOptions | None,
        Field(
            description=(
                "Timeline export options. The required 'format' field selects the "
                "interchange format: 'edl' (CMX3600 EDL) or 'fcpxml' (Final Cut Pro "
                "XML). No default; options must be provided."
            )
        ),
    ] = None,
) -> ToolResult:
    """Export an OTIO timeline to an NLE-interchange file (transform type).

    Input contract (transform): OTIO timeline -> new sidecar exchange file. The
    input OTIO file and its media are never modified; only a new file is written.

    format enumerates 'edl' (CMX3600 EDL) and 'fcpxml' (Final Cut Pro XML). Media
    references are absolutized for NLE hand-off. Non-integer (NTSC 23.976/29.97)
    frame rates are rejected before any write. clipwright-specific edit data the
    exchange format cannot carry (captions, overlays, color grades, etc.) is
    reported in 'warnings' — keep the source OTIO as master and re-run
    clipwright-render to bake those into a flat MP4.

    readOnlyHint=True: the tool writes only a freshly created output file; the
    new-file write is outside the readOnly scope, which refers to existing
    resources.
    """
    if options is None:
        return error_result(
            "INVALID_INPUT",
            "options is required but was not provided.",
            (
                "Pass options with the required 'format' field "
                '(e.g., {"format": "edl"} or {"format": "fcpxml"}).'
            ),
        )
    result = export_timeline(timeline=timeline, output=output, options=options)
    if isinstance(result, ToolResult):
        return result
    return ToolResult.model_validate(result)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_export_chapters(
    timeline: Annotated[str, Field(description="Input OTIO timeline file path.")],
    output: Annotated[
        str,
        Field(
            description=(
                "Output chapter sidecar file path. Its extension must match the "
                "format (youtube -> .txt; ffmetadata -> .txt/.ffmeta/.ffmetadata) "
                "and must differ from the input timeline path. The input OTIO and "
                "its media are never modified; only this new file is written."
            )
        ),
    ],
    options: Annotated[
        ExportChaptersOptions | None,
        Field(
            description=(
                "Chapter export options. The required 'format' field selects "
                "'youtube' (plain-text timestamp list for a video description) or "
                "'ffmetadata' (FFmpeg metadata CHAPTER file). Optional 'marker_kind' "
                "selects which clipwright markers are treated as chapter boundaries "
                "(default 'scene_boundary'). No format default; options must be "
                "provided."
            )
        ),
    ] = None,
) -> ToolResult:
    """Export chapter data from an OTIO timeline to a text sidecar (transform type).

    Input contract (transform): OTIO timeline -> new sidecar chapter file. The
    input OTIO file and its media are never modified; only a new file is written.

    format enumerates 'youtube' (plain-text timestamp list for a video
    description) and 'ffmetadata' (FFmpeg metadata file with CHAPTER entries).
    Chapters are derived from clipwright markers of options.marker_kind (default
    'scene_boundary'). Zero matching markers is a success with a warning. Format
    constraints that cannot be repaired automatically (e.g. YouTube requiring a
    00:00 first chapter and at least 3 chapters) are surfaced in 'warnings' —
    review them before publishing; markers are never fabricated.

    readOnlyHint=True: the tool writes only a freshly created sidecar file (it
    does not invoke ffmpeg mux); the new-file write is outside the readOnly scope,
    which refers to existing resources.
    """
    if options is None:
        return error_result(
            "INVALID_INPUT",
            "options is required but was not provided.",
            (
                "Pass options with the required 'format' field "
                '(e.g., {"format": "youtube"} or {"format": "ffmetadata"}).'
            ),
        )
    result = export_chapters(timeline=timeline, output=output, options=options)
    if isinstance(result, ToolResult):
        return result
    return ToolResult.model_validate(result)


def main() -> None:
    """Entry point for the clipwright-export MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
