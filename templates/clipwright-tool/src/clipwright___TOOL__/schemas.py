"""schemas.py — clipwright-__TOOL__ tool-specific Pydantic schemas.

Common types (MediaRef / TimeRange / Artifact / ToolResult etc) are defined
centrally in clipwright.schemas, so not redefined here (CONVENTIONS §2 common type reuse).
This module contains only tool-specific input options.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class __Action__Options(BaseModel):
    """Options for clipwright___ACTION__.

    FastMCP generates schemas from Annotated[type, Field(description=...)].
    description is read by AI, so write purpose, default, constraints concisely.
    Replace fields with tool-specific ones (example_threshold is template example).
    """

    example_threshold: Annotated[
        float,
        Field(
            default=0.5,
            gt=0,
            description=(
                "(TODO: Sample template field. "
                "Replace with actual detection/formatting parameters.) "
                "gt=0 constraint: values <= 0 rejected as INVALID_INPUT."
            ),
        ),
    ] = 0.5
