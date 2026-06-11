"""schemas.py — clipwright-render specific Pydantic schemas.

Common types (MediaRef / TimeRange / Artifact / ToolResult, etc.) are centrally
defined in clipwright.schemas; do not redefine them here.
Use `from clipwright.schemas import ...` when needed.
"""

from __future__ import annotations

import re
from typing import Annotated, Self

from pydantic import BaseModel, Field, field_validator, model_validator

# Characters forbidden in filtergraph/force_style separators and libass
# FontName interpretation risks: , : ' [ ] ; \ = # (ADR-S2-r2/DC-AM-004)
# # added: some libass versions misinterpret # in FontName as a colour code
# (SR-NEW).
_FONT_NAME_FORBIDDEN_CHARS_RE = re.compile(r"[,:'\\[\];=#]")

# Practical upper limit for font_size (set to a value libass does not reject;
# effectively unlimited)
_FONT_SIZE_MAX: int = 1_000_000_000

# Practical upper limit for margin_v (rejects values exceeding the maximum
# vertical resolution of 8K)
_MARGIN_V_MAX: int = 10_000


class SubtitleOptions(BaseModel):
    """Subtitle burn-in options (ADR-S2-r2 / ADR-S6-r2 / ADR-S6-r3).

    Pass to RenderOptions.subtitle so clipwright-render burns subtitles into the
    video. When subtitle=None (omitted), subtitle processing is skipped (backward
    compatible; ADR-S8).

    All style fields are Optional; when unspecified, libass defaults are used.
    """

    # allow_inf_nan=False added to model_config (consistent with BGM/Denoise
    # models; SR-V-001). Field-level allow_inf_nan=False is redundant once
    # model_config is set, but retained explicitly on the outline field for
    # defence-in-depth (ADR-S2-r2).
    model_config = {
        "extra": "forbid",
        "arbitrary_types_allowed": False,
        "allow_inf_nan": False,
    }

    path: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Path to subtitle file (required). Empty string not allowed"
                " (DC-AM-005). Single quote (') not allowed (prevents ffmpeg"
                " filtergraph quote syntax breakage; CR-E-001). Only .srt / .vtt"
                " / .ass extensions are accepted (validated in render.py). render.py"
                " resolves to an absolute path before passing to plan.py (ADR-S5-r2)."
            ),
        ),
    ]

    font_name: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Font family name. CJK (Japanese) font names are allowed"
                " (ADR-S2-r2). Filtergraph/force_style separator characters"
                " `, : ' [ ] ; \\ = #` are forbidden (DC-AM-004/SR-NEW). Leading/"
                "trailing whitespace affects libass FontName recognition; specify"
                " exactly as intended (S-L-5)."
            ),
        ),
    ] = None

    fonts_dir: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Path to font search directory. Passed to the fontsdir= option"
                " of the ffmpeg subtitles filter. Single quote (') not allowed"
                " (prevents ffmpeg filtergraph quote syntax breakage; CR-E-001)."
                " When unspecified, the fontsdir option is omitted."
            ),
        ),
    ] = None

    font_size: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            le=_FONT_SIZE_MAX,
            description=(
                "Font size in points. Must be a positive integer. When"
                " unspecified, libass default is used. Upper limit is"
                f" {_FONT_SIZE_MAX} (practical ceiling that libass does not"
                " reject; effectively unlimited)."
            ),
        ),
    ] = None

    font_color: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^#[0-9a-fA-F]{6}$",
            description=(
                "Text colour in #RRGGBB format."
                " Internally converted to ASS PrimaryColour (&H00BBGGRR; DC-AM-002)."
                " When unspecified, libass default is used."
            ),
        ),
    ] = None

    outline: Annotated[
        float | None,
        Field(
            default=None,
            ge=0.0,
            le=100.0,
            allow_inf_nan=False,
            description=(
                "Outline width. Non-negative float. Setting `0.0` adds"
                " `Outline=0` to force_style (no outline). `None` uses the libass"
                " default (outline enabled; omit = leave to libass)."
            ),
        ),
    ] = None

    alignment: Annotated[
        int | None,
        Field(
            default=None,
            ge=1,
            le=9,
            description=(
                "Subtitle display position (ASS v4+ numpad layout). 1=bottom-left,"
                " 2=bottom-center, 3=bottom-right, 4=middle-left, 5=center,"
                " 6=middle-right, 7=top-left, 8=top-center, 9=top-right. Only"
                " integers 1–9 are valid (DC-AM-001)."
            ),
        ),
    ] = None

    margin_v: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=_MARGIN_V_MAX,
            description=(
                "Vertical margin in pixels. Non-negative integer. When"
                " unspecified, libass default is used. Upper limit is"
                f" {_MARGIN_V_MAX} (rejects values exceeding the maximum"
                " vertical resolution of 8K)."
            ),
        ),
    ] = None

    @field_validator("font_name")
    @classmethod
    def _validate_font_name_no_forbidden_chars(cls, v: str | None) -> str | None:
        """Validate that font_name contains no filtergraph/force_style separator
        characters.

        Forbidden characters: , : ' [ ] ; \\ = # (ADR-S2-r2/DC-AM-004/SR-NEW).
        Japanese font names (CJK and other Unicode) are allowed. # is forbidden
        because some libass versions misinterpret # in FontName as a colour code,
        so it is blocked defensively (SR-NEW).
        """
        if v is None:
            return v
        if _FONT_NAME_FORBIDDEN_CHARS_RE.search(v):
            raise ValueError(
                "font_name must not contain filtergraph/force_style separator"
                " characters (, : ' [ ] ; \\ = #) (DC-AM-004/SR-NEW)."
            )
        return v

    @field_validator("path")
    @classmethod
    def _validate_path_no_single_quote(cls, v: str) -> str:
        """Validate that path contains no single quote.

        ffmpeg filtergraph syntax wraps paths as filename='{path}'. If the path
        contains a single quote, the ffmpeg parser raises a syntax error, so
        single quotes are blocked via allow-list (CR-E-001). Escaping `'` is
        fragile in ffmpeg filtergraph syntax, so blocking is preferred.
        """
        if "'" in v:
            raise ValueError(
                "path must not contain a single quote (')"
                " (prevents ffmpeg filtergraph quote syntax breakage; CR-E-001)."
            )
        return v

    @field_validator("fonts_dir")
    @classmethod
    def _validate_fonts_dir_no_single_quote(cls, v: str | None) -> str | None:
        """Validate that fonts_dir contains no single quote.

        Same reason as path (prevents fontsdir='{dir}' syntax breakage;
        CR-E-001).
        """
        if v is None:
            return v
        if "'" in v:
            raise ValueError(
                "fonts_dir must not contain a single quote (')"
                " (prevents ffmpeg filtergraph quote syntax breakage; CR-E-001)."
            )
        return v


class RenderOptions(BaseModel):
    """Conversion options for clipwright_render (DC-AM-004).

    All fields are Optional; when unspecified, the source codec/resolution/fps
    etc. are inherited and ffmpeg defaults are used.

    Resolution pair constraint: width and height must both be specified or both
    be None. Specifying only one makes the scale filter incomplete and raises
    ValidationError.
    """  # noqa: E501

    video_codec: Annotated[
        str | None,
        Field(
            default=None,
            max_length=64,
            pattern=r"^[a-zA-Z0-9_\-]+$",
            description=(
                "Output video codec. Examples: libx264 / libx265 / copy. Inherits"
                " source when unspecified. Only alphanumerics, underscores, and"
                " hyphens are allowed (max 64 chars)."
            ),
        ),
    ] = None

    audio_codec: Annotated[
        str | None,
        Field(
            default=None,
            max_length=64,
            pattern=r"^[a-zA-Z0-9_\-]+$",
            description=(
                "Output audio codec. Examples: aac / opus / mp3. Inherits source"
                " when unspecified. Only alphanumerics, underscores, and hyphens"
                " are allowed (max 64 chars)."
            ),
        ),
    ] = None

    width: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Output video width in pixels. Must be specified together with"
                " height. Inherits source when unspecified."
            ),
        ),
    ] = None

    height: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Output video height in pixels. Must be specified together with"
                " width. Inherits source when unspecified."
            ),
        ),
    ] = None

    fps: Annotated[
        float | None,
        Field(
            default=None,
            gt=0.0,
            description=(
                "Output frame rate. Inherits source when unspecified (assumes"
                " single-source CFR)."
            ),
        ),
    ] = None

    crf: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=51,
            description=(
                "Video quality (CRF value). Range: 0–51. 0 is highest quality."
                " Uses ffmpeg default when unspecified."
            ),
        ),
    ] = None

    overwrite: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "When True, overwrites an existing output file. Default is False"
                " (reject overwrite)."
            ),
        ),
    ] = False

    subtitle: Annotated[
        SubtitleOptions | None,
        Field(
            default=None,
            description=(
                "Subtitle burn-in options. When specified, subtitles are"
                " hard-burned into the video (ADR-S1). None (omitted) skips"
                " subtitle processing (backward compatible; ADR-S8)."
            ),
        ),
    ] = None

    @model_validator(mode="after")
    def _validate_resolution_pair(self) -> Self:
        """width and height must both be specified or both be None.

        Specifying only one makes the ffmpeg scale filter incomplete, so it is
        forbidden (DC-AM-004).
        """
        width_set = self.width is not None
        height_set = self.height is not None
        if width_set != height_set:
            raise ValueError(
                "width and height must be specified together or both omitted"
                " (specifying only one is invalid)"
            )
        return self
