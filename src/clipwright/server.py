"""server.py — FastMCP primitive server (4 tools).

Each tool is a thin wrapper that calls the library layer
(media / otio_utils / operations / project / envelope) and converts
ClipwrightError into an envelope (error_result).
Business logic is kept out of the server layer (single responsibility).

Transport defaults to stdio (mcp.run(transport="stdio")).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

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
from clipwright.project import init_project as _init_project
from clipwright.schemas import Artifact, MediaInfo, ToolResult

# FastMCP instance (name = MCP server name)
mcp = FastMCP("clipwright")

# Marker truncation threshold (§13.2 DC-AS-004)
_MARKER_THRESHOLD = 50


def _inspect_media(path: str) -> MediaInfo:
    """Thin wrapper around clipwright.media.inspect_media.

    Exposing _inspect_media in the server module's namespace allows tests to
    patch clipwright.server._inspect_media (M-2).
    Because the implementation goes through the clipwright.media module,
    patching clipwright.media.inspect_media also works.
    """
    return _media.inspect_media(path)


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
) -> ToolResult:
    """Load timeline.otio and return a summary.

    Exactly one of project_dir or timeline_path must be specified (mutually exclusive).
    Providing both or neither is INVALID_INPUT (§13.2 DC-AS-004).

    marker count ≤ 50: returns the list in data.markers.
    marker count > 50: omits data.markers; returns data.markers_truncated=True and
    data.marker_count only (§13.2 DC-AS-004 / §13.5 DC-AM-001).

    The full list is available from the timeline.otio in artifacts.
    """
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
        resolved_path = str(Path(project_dir) / "timeline.otio")
    else:
        # Direct timeline_path: whitelist .otio extension (path-traversal guard)
        resolved = Path(str(timeline_path)).resolve()
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
            ErrorCode.OTIO_ERROR,
            "Failed to load timeline.otio",
            "Check that the file is a valid OTIO file.",
        )

    summary_dict = summarize_timeline(timeline)

    # Marker truncation formatting (§13.5 DC-AM-001 re: server's responsibility)
    marker_count: int = summary_dict["marker_count"]
    total_dur = summary_dict["total_duration"]
    data: dict[str, Any] = {
        "clip_count": summary_dict["clip_count"],
        "gap_count": summary_dict["gap_count"],
        "marker_count": marker_count,
        "total_duration": (
            total_dur.model_dump() if hasattr(total_dur, "model_dump") else total_dur
        ),
    }
    if marker_count <= _MARKER_THRESHOLD:
        # ≤ 50: return the markers list as-is
        raw_markers: list[dict[str, Any]] = []
        for m in summary_dict["markers"]:
            entry: dict[str, Any] = {}
            for k, v in m.items():
                entry[k] = v.model_dump() if hasattr(v, "model_dump") else v
            raw_markers.append(entry)
        data["markers"] = raw_markers
        data["markers_truncated"] = False
    else:
        # > 50: omit markers; return truncation flag and count only
        data["markers_truncated"] = True

    artifacts = [
        Artifact(role="timeline", path=resolved_path, format="otio").model_dump(),
    ]

    # Forward summarize_timeline warnings into the envelope (M-4)
    summary_warnings: list[str] = summary_dict.get("warnings", [])

    return ok_result(
        f"Timeline loaded: {timeline.name} "
        f"(clips={data['clip_count']}, gaps={data['gap_count']}"
        f", markers={marker_count})",
        data=data,
        artifacts=artifacts,
        warnings=summary_warnings if summary_warnings else None,
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
    resolved_path = str(Path(project_dir) / "timeline.otio")

    # Load the timeline
    try:
        timeline = load_timeline(resolved_path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.OTIO_ERROR,
            "Failed to load timeline.otio",
            "Initialise the project with init_project, then try again.",
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
