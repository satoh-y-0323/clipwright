"""schemas.py — clipwright-wrap-specific Pydantic schemas.

Common types (MediaRef / Artifact / ToolResult, etc.) are defined
centrally in clipwright.schemas; this module does not redefine them.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class WrapCaptionsOptions(BaseModel):
    """Options for clipwright_wrap_captions (WR-AD-05).

    language selects the budoux parser.
    All 4 languages confirmed loadable in spike-budoux (DC-AM-005).
    max_chars is the maximum number of characters per line
    (each character counts as 1; len() check).
    max_lines is the maximum number of lines per cue
    (excess is subject to warnings; WR-AD-15(1)).
    """

    language: Annotated[
        str,
        Field(
            default="ja",
            max_length=7,
            pattern=r"^(ja|zh-hans|zh-hant|th)$",
            description=(
                "Language code to select the budoux phrase-boundary parser. "
                "Supported languages: ja / zh-hans / zh-hant / th. "
                "All 4 languages confirmed in spike-budoux (DC-AM-005). "
                "Unsupported values are rejected with INVALID_INPUT."
            ),
        ),
    ] = "ja"

    max_chars: Annotated[
        int,
        Field(
            default=16,
            gt=0,
            description=(
                "Maximum number of characters per line"
                " (each character counts as 1; len() check). "
                "Default is ~16 full-width characters, following"
                " Japanese subtitle conventions (WR-AD-05). "
                "A line break is inserted just before the limit is exceeded"
                " (greedy fill; WR-AD-04). "
                "If a single phrase segment exceeds the limit on its own,"
                " it is placed on one line without splitting. "
                "gt=0 constraint: 0 or below is rejected with INVALID_INPUT."
            ),
        ),
    ] = 16

    max_lines: Annotated[
        int,
        Field(
            default=2,
            gt=0,
            description=(
                "Maximum number of lines per cue. Excess is recorded in warnings. "
                "The original text is preserved without truncation"
                " (WR-AD-15(1); requirement §2). "
                "gt=0 constraint: 0 or below is rejected with INVALID_INPUT."
            ),
        ),
    ] = 2
