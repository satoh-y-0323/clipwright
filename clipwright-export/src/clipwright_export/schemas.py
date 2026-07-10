"""schemas.py — Pydantic schemas for ExportTimelineOptions / ExportChaptersOptions.

Defines the options models for clipwright-export. Core shared types
(MediaRef, TimeRange, Artifact, ToolResult) are imported from
clipwright.schemas and must not be redefined here (§6 convention contract).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExportTimelineOptions(BaseModel):
    """Options for exporting an OTIO timeline to an NLE-interchange format.

    Media reference absolutization is always performed and is not
    configurable (architecture-report §3.1, ADR-EX-2).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    format: Annotated[
        Literal["edl", "fcpxml"],
        Field(
            description=(
                "Target interchange format. 'edl' produces a CMX3600 EDL; "
                "'fcpxml' produces a Final Cut Pro XML. Required, no default."
            ),
        ),
    ]


class ExportChaptersOptions(BaseModel):
    """Options for exporting chapter/marker data derived from an OTIO timeline.

    marker_kind selects which clipwright metadata markers are treated as
    chapter boundaries; it is a free-form string, not a closed enum
    (architecture-report §3, ADR-EX-2).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    format: Annotated[
        Literal["youtube", "ffmetadata"],
        Field(
            description=(
                "Target chapter format. 'youtube' produces a plain-text "
                "timestamp list for video descriptions; 'ffmetadata' produces "
                "an FFmpeg metadata file with CHAPTER entries. Required, no "
                "default."
            ),
        ),
    ]

    marker_kind: Annotated[
        str,
        Field(
            default="scene_boundary",
            description=(
                "Value of metadata['clipwright']['kind'] on OTIO markers to "
                "treat as chapter boundaries. Free-form string (not a closed "
                "enum) so callers can target markers from any clipwright tool. "
                "Defaults to 'scene_boundary' (clipwright-scene markers)."
            ),
        ),
    ] = "scene_boundary"
