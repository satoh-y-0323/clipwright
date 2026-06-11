"""schemas.py — Pydantic schemas specific to clipwright-bgm.

Common types (MediaRef / Artifact / ToolResult, etc.) are defined centrally in
clipwright.schemas and are not redefined here.

DuckingOptions: User-facing ducking options.
DuckingDirective: Ducking directive written into BGM clip metadata.
BgmOptions: Input options for clipwright_add_bgm (user input layer).
BgmDirective: Directive schema written to BGM clip metadata["clipwright"]
    (writer layer, B9-r2).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DuckingOptions(BaseModel):
    """User-facing ducking options (ADR-B9).

    When enabled=True, sidechaincompress is applied at render time to automatically
    attenuate the BGM. threshold and ratio map to sidechaincompress parameters.
    """

    model_config = {"allow_inf_nan": False}

    enabled: bool = False
    threshold: Annotated[
        float,
        Field(
            default=0.05,
            description="Sidechain trigger threshold (linear amplitude, range 0–1).",
        ),
    ] = 0.05
    ratio: Annotated[
        float,
        Field(
            default=4.0,
            description="Compression ratio. Higher values produce stronger ducking.",
        ),
    ] = 4.0


class BgmOptions(BaseModel):
    """Options for clipwright_add_bgm (user input layer, ADR-B9).

    volume_db: BGM volume adjustment in dB. Range: -60 to 20.
    fade_in_sec: Fade-in duration in seconds. 0 means no afade is injected (ADR-B9-r3).
    fade_out_sec: Fade-out duration in seconds.
        0 means no afade is injected (ADR-B9-r3).
    ducking: Ducking options (default OFF).
    """

    model_config = {"allow_inf_nan": False}

    volume_db: Annotated[
        float,
        Field(
            ge=-60.0,
            le=20.0,
            description="BGM volume adjustment in dB. Range: [-60, 20].",
        ),
    ]
    fade_in_sec: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            description=(
                "Fade-in duration in seconds (ge=0). 0 means no fade (ADR-B9-r3)."
            ),
        ),
    ] = 0.0
    fade_out_sec: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            description=(
                "Fade-out duration in seconds (ge=0). 0 means no fade (ADR-B9-r3)."
            ),
        ),
    ] = 0.0
    ducking: DuckingOptions = Field(default_factory=DuckingOptions)


class DuckingDirective(BaseModel):
    """Ducking directive written into BGM clip metadata (writer layer, ADR-B9-r2).

    Co-located as BgmDirective.ducking.
    The render reader side reads the same field structure.
    allow_inf_nan=False is not propagated to child models automatically, so it is set
    explicitly here (SR L-1, M-1).
    Range constraints on threshold/ratio are based on the actual allowed range of
    sidechaincompress (CR L-6), confirmed via `ffmpeg -h filter=sidechaincompress`.
    """

    model_config = {"allow_inf_nan": False}

    enabled: bool = False
    threshold: Annotated[
        float,
        Field(
            default=0.05,
            gt=0.0,
            le=1.0,
            description=(
                "Sidechain trigger threshold (linear amplitude, range 0–1). "
                "ffmpeg sidechaincompress threshold range: (0, 1]."
            ),
        ),
    ] = 0.05
    ratio: Annotated[
        float,
        Field(
            default=4.0,
            ge=1.0,
            le=20.0,
            description=(
                "Compression ratio (ffmpeg sidechaincompress ratio range: [1, 20])."
            ),
        ),
    ] = 4.0


class BgmDirective(BaseModel):
    """Directive schema written to BGM clip metadata["clipwright"]
    (writer layer, ADR-B9-r2).

    Built by add_bgm and stored via .model_dump() into OTIO metadata.
    The render reader side defines the same fields with max_length=64
    (following NR-M-1).
    """

    model_config = {"allow_inf_nan": False}

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["bgm"]
    volume_db: Annotated[float, Field(ge=-60.0, le=20.0, allow_inf_nan=False)]
    fade_in_sec: Annotated[float, Field(ge=0.0)]
    fade_out_sec: Annotated[float, Field(ge=0.0)]
    ducking: DuckingDirective
