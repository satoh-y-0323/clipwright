"""server.py — FastMCP primitive server (4 tools).

Each tool is a thin wrapper that calls the library layer
(media / otio_utils / operations / project / envelope) and converts
ClipwrightError into an envelope (error_result).
Business logic is kept out of the server layer (single responsibility).

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, TypeAdapter, ValidationError

import clipwright.media as _media
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.operations import (
    AddClipOp,
    AddGapOp,
    AddMarkerOp,
    Operation,
    apply_operations,
)
from clipwright.otio_utils import load_timeline, save_timeline, summarize_timeline
from clipwright.pathpolicy import validate_source_or_basename
from clipwright.project import init_project as _init_project
from clipwright.schemas import Artifact, MediaInfo, ToolResult

# FastMCP instance (name = MCP server name)
mcp = FastMCP("clipwright")

# Pagination defaults (ADR-RD-14)
_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 500


def _paginate(
    items: list[dict[str, Any]], offset: int, limit: int
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Return (page, truncated, next_offset) for a detail list.

    Args:
        items: Full list of items.
        offset: 0-based start position.
        limit: Maximum items per page (already validated and clamped).

    Returns:
        Tuple of (page_items, has_more, next_offset).
        next_offset is the start position of the next page, or None if at end.
    """
    end = offset + limit
    page = items[offset:end]
    has_more = end < len(items)
    next_offset = end if has_more else None
    return page, has_more, next_offset


def _dump_models(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic values inside a detail entry to plain dicts.

    Used for both clip and marker entries to convert RationalTimeModel
    and other Pydantic objects to plain dicts for JSON serialization.
    """
    result: dict[str, Any] = {}
    for k, v in entry.items():
        result[k] = v.model_dump() if hasattr(v, "model_dump") else v
    return result


def _inspect_media(path: str) -> MediaInfo:
    """Thin wrapper around clipwright.media.inspect_media.

    Exposing _inspect_media in the server module's namespace allows tests to
    patch clipwright.server._inspect_media (M-2).
    Because the implementation goes through the clipwright.media module,
    patching clipwright.media.inspect_media also works.
    """
    return _media.inspect_media(path)


def _resolve_project_timeline(project_dir: str) -> str | ToolResult:
    """Resolve timeline.otio path from project_dir.

    Validates that <project_dir>/timeline.otio exists and is a regular file.
    Returns the resolved path string on success, or FILE_NOT_FOUND ToolResult
    on failure.
    """
    project_path = Path(project_dir)
    timeline_otio = project_path / "timeline.otio"
    if not timeline_otio.is_file():
        return error_result(
            ErrorCode.FILE_NOT_FOUND,
            "timeline.otio not found in project_dir",
            "Initialise the project with clipwright_init_project, then try again.",
        )
    return str(timeline_otio)


# ===========================================================================
# clipwright_init_project
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def clipwright_init_project(
    project_dir: Annotated[
        str,
        Field(
            description=(
                "Path to the project directory to initialise."
                " Created if it does not exist."
            )
        ),
    ],
    name: Annotated[
        str,
        Field(description="Project name (recorded in clipwright.json)."),
    ],
    force: Annotated[
        bool,
        Field(
            description=(
                "When True, reinitialises an existing project non-destructively"
                " (§13.2 DC-AM-007)."
            )
        ),
    ] = False,
) -> ToolResult:
    """Initialise a project directory.

    Creates the sources / artifacts / outputs subdirectories, a clipwright.json
    manifest, and an empty timeline.otio (with V1/A1 tracks).

    force=True is non-destructive: preserves existing media files and timeline.otio,
    and only regenerates the manifest and ensures directories exist (§13.2 DC-AM-007).
    """
    try:
        _init_project(project_dir, name, force=force)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.INTERNAL,
            "An unexpected error occurred",
            "Please report with reproduction steps.",
        )

    proj = Path(project_dir)
    manifest_path = proj / "clipwright.json"
    timeline_path = proj / "timeline.otio"

    artifacts = [
        Artifact(role="manifest", path=str(manifest_path), format="json").model_dump(),
        Artifact(role="timeline", path=str(timeline_path), format="otio").model_dump(),
    ]

    return ok_result(
        f"Project '{name}' initialised: {project_dir}",
        data={"project_dir": str(proj), "name": name},
        artifacts=artifacts,
    )


# ===========================================================================
# clipwright_inspect_media
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_inspect_media(
    path: Annotated[str, Field(description="Path to the media file to probe.")],
) -> ToolResult:
    """Probe a media file with ffprobe and return its information.

    ffprobe is located via CLIPWRIGHT_FFPROBE env var, then PATH (ADR-3).
    If ffprobe is not found, DEPENDENCY_MISSING is returned on the first call
    (no startup check; §13.3 DC-GP-001).

    The hint includes instructions for installing via winget on Windows.
    The dependency check is performed by resolve_tool inside _inspect_media
    (M-2: avoids double invocation).
    """
    try:
        media_info = _inspect_media(path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.INTERNAL,
            "An unexpected error occurred",
            "Please report with reproduction steps.",
        )

    data: dict[str, Any] = {
        "path": media_info.path,
        "container": media_info.container,
        "duration": (media_info.duration.model_dump() if media_info.duration else None),
        "streams": [s.model_dump() for s in media_info.streams],
        "start_timecode": media_info.start_timecode,
    }
    video_streams = [s for s in media_info.streams if s.codec_type == "video"]
    audio_streams = [s for s in media_info.streams if s.codec_type == "audio"]
    duration_sec = (
        media_info.duration.value / media_info.duration.rate
        if media_info.duration and media_info.duration.rate > 0
        else None
    )
    summary = (
        f"Media probe complete: {path} "
        f"(video: {len(video_streams)} stream(s), audio: {len(audio_streams)} stream(s)"
        + (f", duration={duration_sec:.2f}s" if duration_sec is not None else "")
        + ")"
    )

    return ok_result(summary, data=data)


# ===========================================================================
# clipwright_read_timeline
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_read_timeline(
    project_dir: Annotated[
        str | None,
        Field(
            description=(
                "Path to the project directory."
                " Mutually exclusive with timeline_path (specify exactly one)."
            )
        ),
    ] = None,
    timeline_path: Annotated[
        str | None,
        Field(
            description=(
                "Direct path to a timeline.otio file."
                " Mutually exclusive with project_dir (specify exactly one)."
            )
        ),
    ] = None,
    section: Annotated[
        Literal["clips", "markers"] | None,
        Field(
            description=(
                "Which detail list to page through."
                " Omit for an overview that returns the first page of both"
                ' clips and markers. Set to "clips" or "markers" to page'
                " through that one list only (the other list is omitted)."
                " offset is only accepted together with section."
            )
        ),
    ] = None,
    offset: Annotated[
        int,
        Field(
            description=(
                "0-based start position inside the section's list."
                " Only valid together with section; the overview always"
                " starts at 0. Pass the clips_next_offset /"
                " markers_next_offset value from the previous response to"
                " fetch the next page."
            )
        ),
    ] = 0,
    limit: Annotated[
        int,
        Field(
            description=(
                "Maximum number of entries returned per list"
                " (default 50, maximum 500). Values above the maximum are clamped"
                " and reported in warnings. Note: limit controls response size,"
                " not the cost of reading a large timeline; both clips and markers"
                " are always fully loaded regardless of limit."
            )
        ),
    ] = 50,
    marker_kind: Annotated[
        str | None,
        Field(
            description=(
                "Return only markers whose clipwright metadata kind equals"
                ' this value (e.g. "caption", "scene", "silence").'
                " Omit to return markers of every kind."
                " Does not affect clips or marker_count."
            )
        ),
    ] = None,
) -> ToolResult:
    """Load timeline.otio and return a summary with optional pagination.

    **Input contract:**
    Exactly one of project_dir or timeline_path must be specified (mutually
    exclusive). Providing both or neither is INVALID_INPUT.

    **Overview vs. section-specific read:**
    When section is None (default), an overview is returned showing the first
    page of both clips and markers (up to `limit` items each). This is the
    recommended starting point to explore a timeline. Specify section="clips" or
    section="markers" to page through a single list only (the other is omitted).

    **Clip index semantics:**
    Each clip entry includes an index that counts only clips in that track
    (Gap and Transition items are excluded). The index is not a page position;
    it identifies the clip's position within its track for use with other tools.
    To determine current page position, use offset and limit.

    **Pagination with marker_kind filter:**
    marker_kind filters the markers list in data.markers without affecting
    marker_count (which remains the total). When using marker_kind, pass the
    markers_next_offset from the previous response to continue pagination.

    **Paging example:**
    1. Call with no arguments to get the overview.
    2. If clips_truncated=True, call again with section="clips" and
       offset=clips_next_offset.
    3. Repeat until clips_next_offset is None or clips_truncated=False.
    """
    # ===== Input validation (before path resolution, to avoid wasted I/O) =====

    # Paging argument validation (ADR-RD-2/RD-3)
    # Check negative offset first (prior to section-dependency check)
    if offset < 0:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "offset must be zero or greater",
            (
                "Pass offset=0 for the first page,"
                " or the *_next_offset value from the previous response."
            ),
        )

    if section is None and offset != 0:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "offset is only supported together with section",
            (
                'Pass section="clips" or section="markers" when paging,'
                " or omit offset for the overview."
            ),
        )

    if limit <= 0:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "limit must be greater than zero",
            "Pass limit between 1 and 500 (default 50).",
        )

    # Clamp limit to max (ADR-RD-3)
    warnings_list: list[str] = []
    effective_limit = limit
    if limit > _MAX_PAGE_LIMIT:
        effective_limit = _MAX_PAGE_LIMIT
        warnings_list.append("limit was clamped to 500 (maximum page size).")

    # Mutually exclusive input validation (§13.2 DC-AS-004)
    if project_dir is None and timeline_path is None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "Specify either project_dir or timeline_path",
            (
                "Provide a project directory path in project_dir,"
                " or a full path to timeline.otio in timeline_path."
            ),
        )
    if project_dir is not None and timeline_path is not None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "project_dir and timeline_path cannot both be specified",
            (
                "Specify only one."
                " When project_dir is given, <project_dir>/timeline.otio is used."
            ),
        )

    # Resolve the timeline path
    if project_dir is not None:
        project_result = _resolve_project_timeline(project_dir)
        if isinstance(project_result, ToolResult):
            return project_result
        resolved_path = project_result
    else:
        # Direct timeline_path: validate before resolve (ADR-PB-2)
        timeline_path_str = str(timeline_path)
        try:
            validate_source_or_basename(
                timeline_path_str,
                message=(
                    f"timeline_path does not exist: {Path(timeline_path_str).name}"
                ),
                hint="Specify a valid path to an existing .otio file.",
            )
        except ClipwrightError as exc:
            return error_result(exc.code, exc.message, exc.hint)
        # Whitelist .otio extension (path-traversal guard)
        resolved = Path(timeline_path_str).resolve()
        if resolved.suffix != ".otio":
            return error_result(
                ErrorCode.PATH_NOT_ALLOWED,
                f"timeline_path must point to a .otio file: {resolved.name}",
                "Specify a file path with the .otio extension.",
            )
        if not resolved.is_file():
            return error_result(
                ErrorCode.FILE_NOT_FOUND,
                f"timeline_path does not exist: {resolved.name}",
                "Specify a valid path to an existing .otio file.",
            )
        resolved_path = str(resolved)

    try:
        timeline = load_timeline(resolved_path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.INTERNAL,
            "An unexpected error occurred",
            "Please report with reproduction steps.",
        )

    summary_dict = summarize_timeline(timeline)

    # ===== Pagination processing (ADR-RD-1 through RD-11) =====

    total_dur = summary_dict["total_duration"]
    clip_count: int = summary_dict["clip_count"]
    gap_count: int = summary_dict["gap_count"]
    marker_count: int = summary_dict["marker_count"]

    # All clips and markers (untruncated, as dicts from summarize_timeline)
    all_clips: list[dict[str, Any]] = summary_dict.get("clips", [])
    all_markers: list[dict[str, Any]] = summary_dict.get("markers", [])

    # Apply marker_kind filter if specified (ADR-RD-10)
    # Filter the dict-converted markers from summarize_timeline by kind
    if marker_kind is not None:
        all_markers = [m for m in all_markers if m.get("kind") == marker_kind]

    # Determine which section to return (ADR-RD-1)
    # Initialize all variables to satisfy type narrowing
    clips_page: list[dict[str, Any]] = []
    clips_truncated: bool | None = None
    clips_next_offset: int | None = None
    markers_page: list[dict[str, Any]] = []
    markers_truncated: bool | None = None
    markers_next_offset: int | None = None
    section_used: str | None = None

    if section is None:
        # Overview: first page of both clips and markers
        clips_page, clips_truncated, clips_next_offset = _paginate(
            all_clips, 0, effective_limit
        )
        markers_page, markers_truncated, markers_next_offset = _paginate(
            all_markers, 0, effective_limit
        )
        section_used = None
    elif section == "clips":
        # Clips section: check offset range before paging (ADR-RD-2)
        if offset > 0 and offset >= len(all_clips):
            num_clips = len(all_clips)
            return error_result(
                ErrorCode.INVALID_INPUT,
                f"offset {offset} is past the end of the clips list "
                f"({num_clips} entries)",
                f"Pass an offset below {num_clips}, "
                "or omit offset to start from the beginning.",
            )
        clips_page, clips_truncated, clips_next_offset = _paginate(
            all_clips, offset, effective_limit
        )
        markers_page = []
        markers_truncated = None
        markers_next_offset = None
        section_used = "clips"
    elif section == "markers":
        # Markers section: check offset range after filtering (ADR-RD-10)
        if offset > 0 and offset >= len(all_markers):
            num_markers = len(all_markers)
            return error_result(
                ErrorCode.INVALID_INPUT,
                f"offset {offset} is past the end of the markers list "
                f"({num_markers} entries)",
                f"Pass an offset below {num_markers}, "
                "or omit offset to start from the beginning.",
            )
        markers_page, markers_truncated, markers_next_offset = _paginate(
            all_markers, offset, effective_limit
        )
        clips_page = []
        clips_truncated = None
        clips_next_offset = None
        section_used = "markers"
    else:
        # Defence-in-depth: in-process calls bypass type validation
        # (decorator returns a plain function; Pydantic checks do not run).
        # This branch protects against invalid section values even when
        # the type system is not enforced.
        return error_result(
            ErrorCode.INVALID_INPUT,
            f"section must be 'clips', 'markers', or None, got {section!r}",
            "Omit section for an overview, or pass 'clips' or 'markers'.",
        )

    # Build data dict (ADR-RD-4)
    data: dict[str, Any] = {
        "clip_count": clip_count,
        "gap_count": gap_count,
        "marker_count": marker_count,
        "total_duration": (
            total_dur.model_dump() if hasattr(total_dur, "model_dump") else total_dur
        ),
        "offset": offset,
        "limit": effective_limit,
        "marker_kind": marker_kind,
    }

    # Add clips data (omit if section="markers")
    if section_used != "markers":
        data["clips"] = [_dump_models(c) for c in clips_page]
        data["clips_truncated"] = clips_truncated
        data["clips_next_offset"] = clips_next_offset

    # Add markers data (omit if section="clips")
    if section_used != "clips":
        data["markers"] = [_dump_models(m) for m in markers_page]
        data["markers_truncated"] = markers_truncated
        data["markers_next_offset"] = markers_next_offset

    # Build summary text (ADR-RD-4, §3.5)
    base_summary = (
        f"Timeline loaded: {timeline.name} "
        f"(clips={clip_count}, gaps={gap_count}, markers={marker_count}). "
    )
    if section_used is None:
        # Overview: first page of both clips and markers
        # Check len first, before truncated (to avoid "all 0" descriptions)
        clips_page_desc = (
            "no clips"
            if len(clips_page) == 0
            else (
                f"all {len(clips_page)} clips"
                if not clips_truncated
                else f"clips 0-{len(clips_page) - 1} of {clip_count}"
            )
        )
        markers_page_desc = (
            "no markers"
            if len(markers_page) == 0
            else (
                f"all {len(markers_page)} markers"
                if not markers_truncated
                else f"markers 0-{len(markers_page) - 1} of {marker_count}"
            )
        )

        if len(clips_page) == 0 and len(markers_page) == 0:
            # Both empty: no pagination advice needed
            summary_text = (
                base_summary + f"Showing {clips_page_desc} and {markers_page_desc}."
            )
        elif not clips_truncated and not markers_truncated:
            # Both fit on first page: no pagination advice needed
            summary_text = (
                base_summary + f"Showing {clips_page_desc} and {markers_page_desc}."
            )
        else:
            # At least one section is truncated: advise only on truncated sections
            sections_with_more = []
            if clips_truncated:
                sections_with_more.append('section="clips"')
            if markers_truncated:
                sections_with_more.append('section="markers"')

            sections_str = " or ".join(sections_with_more)
            summary_text = (
                base_summary
                + f"Showing {clips_page_desc} and {markers_page_desc}; "
                + f"call again with {sections_str} plus offset to page further."
            )
    elif section_used == "clips":
        # Clips section
        if len(clips_page) == 0:
            summary_text = base_summary + "Timeline has no clips."
        elif clips_truncated:
            end_clip = offset + len(clips_page) - 1
            summary_text = (
                base_summary
                + f"Showing clips {offset}-{end_clip} of {clip_count}; "
                + f"pass offset={clips_next_offset} for the next page."
            )
        else:
            end_clip = offset + len(clips_page) - 1
            summary_text = (
                base_summary
                + f"Showing clips {offset}-{end_clip} of {clip_count}; "
                + "this is the last page."
            )
    else:
        # Markers section
        if len(markers_page) == 0:
            summary_text = (
                base_summary
                + f"No markers match kind={marker_kind!r} "
                + f"({marker_count} markers in total)."
            )
        elif markers_truncated:
            end_marker = offset + len(markers_page) - 1
            num_all = len(all_markers)
            summary_text = (
                base_summary
                + f"Showing markers {offset}-{end_marker} of {num_all} "
                + f"with kind={marker_kind!r} ({marker_count} markers in total); "
                + f"pass offset={markers_next_offset} for the next page."
            )
        else:
            end_marker = offset + len(markers_page) - 1
            num_all = len(all_markers)
            summary_text = (
                base_summary
                + f"Showing markers {offset}-{end_marker} of {num_all} "
                + f"with kind={marker_kind!r} ({marker_count} markers in total); "
                + "this is the last page."
            )

    artifacts = [
        Artifact(role="timeline", path=resolved_path, format="otio").model_dump(),
    ]

    # Collect all warnings (from summarize_timeline + clamping)
    all_warnings: list[str] = []
    if warnings_list:
        all_warnings.extend(warnings_list)
    if summary_dict.get("warnings"):
        all_warnings.extend(summary_dict["warnings"])

    return ok_result(
        summary_text,
        data=data,
        artifacts=artifacts,
        warnings=all_warnings if all_warnings else None,
    )


# ===========================================================================
# clipwright_write_timeline
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def clipwright_write_timeline(
    project_dir: Annotated[
        str,
        Field(
            description=(
                "Path to the project directory. Targets <project_dir>/timeline.otio."
            )
        ),
    ],
    operations: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Declarative operation list. Each element specifies its type via the op"
                " field. Supported ops: add_clip / add_gap / add_marker."
                " All-or-nothing: if any op is invalid, none are applied (§13.1)."
            )
        ),
    ],
    validate_only: Annotated[
        bool,
        Field(
            description=(
                "When True, validates only without writing to the timeline (dry-run)."
            )
        ),
    ] = False,
) -> ToolResult:
    """Append a declarative operation list to timeline.otio.

    Appends to the existing timeline without discarding its contents
    (§13.2 DC-AM-001 append semantics). Existing content is never cleared.
    Rationale for destructiveHint=False: source media is immutable;
    timeline.otio is written atomically (no corruption).

    validate_only=True: validates only, returns applied_count=0.
    timeline.otio is not updated.

    data contains the ValidationReport (valid/operation_count/applied_count/errors).
    """
    project_result = _resolve_project_timeline(project_dir)
    if isinstance(project_result, ToolResult):
        return project_result
    resolved_path = project_result

    # Load the timeline
    try:
        timeline = load_timeline(resolved_path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.INTERNAL,
            "An unexpected error occurred",
            "Please report with reproduction steps.",
        )

    # Convert to Pydantic types (rejects unknown_op and other invalid ops here)
    op_adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
    typed_ops: list[AddClipOp | AddGapOp | AddMarkerOp] = []
    parse_errors: list[dict[str, Any]] = []

    for i, raw_op in enumerate(operations):
        try:
            typed_op = op_adapter.validate_python(raw_op)
            typed_ops.append(typed_op)
        except ValidationError as exc:
            first_msg = exc.errors()[0]["msg"] if exc.errors() else "unknown error"
            parse_errors.append(
                {
                    "index": i,
                    "code": ErrorCode.UNSUPPORTED_OPERATION,
                    "message": (
                        f"op {i}: {exc.error_count()} validation error(s): {first_msg}"
                    ),
                }
            )

    if parse_errors:
        # Pydantic validation failure (invalid op type, etc.) → input schema violation.
        # Return ok=False via error_result (§6.4 contract).
        first_err = parse_errors[0]
        hint_detail = first_err.get("message", "")
        return error_result(
            ErrorCode.INVALID_INPUT,
            f"Input validation failed for operations: {len(parse_errors)} error(s)",
            (
                f"Supported ops are add_clip / add_gap / add_marker only. {hint_detail}"
                if hint_detail
                else "Supported ops are add_clip / add_gap / add_marker only."
            ),
        )

    # apply_operations (all-or-nothing / validate_only support)
    report = apply_operations(timeline, typed_ops, validate_only=validate_only)

    # Save only when apply succeeded and validate_only is False
    if report.valid and not validate_only and len(typed_ops) > 0:
        try:
            save_timeline(timeline, resolved_path)
        except Exception:
            return error_result(
                ErrorCode.OTIO_ERROR,
                "Failed to save timeline.otio",
                "Check disk space and write permissions.",
            )

    report_data = {
        "valid": report.valid,
        "operation_count": report.operation_count,
        "applied_count": report.applied_count,
        "errors": [e.model_dump() for e in report.errors],
    }

    if report.valid:
        if validate_only:
            summary = (
                f"validate_only: validated {report.operation_count} operation(s)"
                " (not applied)"
            )
        else:
            summary = f"Applied {report.applied_count} operation(s) to the timeline"
    else:
        summary = f"Operation validation failed: {len(report.errors)} error(s)"

    artifacts = [
        Artifact(role="timeline", path=resolved_path, format="otio").model_dump(),
    ]

    return ok_result(summary, data=report_data, artifacts=artifacts)


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """Entry point that starts the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
