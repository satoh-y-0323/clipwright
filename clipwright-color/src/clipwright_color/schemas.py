"""schemas.py — Pydantic models for clipwright-color.

Common types (MediaRef / Artifact / ToolResult) are imported from core (clipwright)
and are never redefined here.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class DetectColorOptions(BaseModel):
    """Options for clipwright_detect_color.

    target_luma: target average luma (0-255 scale). Default 128 (mid-grey).
    sample_interval_sec: ffmpeg fps=1/interval downsampling step (seconds, >0).
    saturation/contrast/gamma: optional eq override; None = leave neutral default.
    temperature/tint: optional WB override [-1,1] axes; None = use auto gray-world.
    lut: optional caller-provided .cube path; None = no 3D-LUT applied.
    """

    model_config = {
        "extra": "forbid",
        "allow_inf_nan": False,
        "hide_input_in_errors": True,
    }

    target_luma: Annotated[
        float,
        Field(
            default=128.0,
            ge=0.0,
            le=255.0,
            allow_inf_nan=False,
            description="Target average luma on the 0-255 scale. Default 128.",
        ),
    ] = 128.0

    sample_interval_sec: Annotated[
        float,
        Field(
            default=1.0,
            gt=0.0,
            le=3600.0,
            allow_inf_nan=False,
            description=(
                "Frame sampling interval in seconds (ffmpeg fps=1/interval)."
                " Must be > 0. Default 1.0."
            ),
        ),
    ] = 1.0

    # FR-1 — caller eq overrides (None = leave neutral; no behavioural change)
    saturation: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    contrast: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    gamma: Annotated[float, Field(ge=0.1, le=10.0)] | None = None

    # FR-3 — caller WB override in normalised [-1,1] axes (None = use auto gray-world)
    temperature: Annotated[float, Field(ge=-1.0, le=1.0)] | None = None
    tint: Annotated[float, Field(ge=-1.0, le=1.0)] | None = None

    # FR-5 — caller-provided 3D-LUT path (.cube)
    lut: Annotated[str, Field(min_length=1, max_length=4096)] | None = None

    @field_validator("lut")
    @classmethod
    def _validate_lut_no_injection_chars(cls, v: str | None) -> str | None:
        """Validate that lut path contains no single quote or control character.

        ffmpeg filtergraph syntax wraps .cube paths in single quotes; a path
        containing a single quote would break the quoting and allow injection
        (CWE-78).  Control characters (< \\x20) are similarly unsafe and are
        rejected here before the path reaches validate_source_file.
        Fixed wording does not echo the path value (CWE-209).
        """
        if v is None:
            return v
        if "'" in v or any(c < "\x20" for c in v):
            raise ValueError(
                "lut must not contain a single quote or control character"
                " (prevents ffmpeg argument injection; CWE-78)."
            )
        return v


class BrightnessMeasured(BaseModel):
    """Raw signalstats measurement (writer side).

    yavg is the mean of per-frame lavfi.signalstats.YAVG values.
    ymin/ymax are optional aggregates (min of YMIN, max of YMAX) for diagnostics.
    sampled_frames is the number of frames that produced a YAVG value.
    inf/nan are rejected; the caller degrades to measured=None (parity with
    loudness U-1).
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    yavg: Annotated[float, Field(ge=0.0, le=255.0)]
    ymin: Annotated[float, Field(ge=0.0, le=255.0)] | None = None
    ymax: Annotated[float, Field(ge=0.0, le=255.0)] | None = None
    sampled_frames: Annotated[int, Field(ge=0)]
    # FR-2 — median chroma from signalstats (ADR-CO-9); None when unavailable
    uavg: Annotated[float, Field(ge=0.0, le=255.0)] | None = None
    vavg: Annotated[float, Field(ge=0.0, le=255.0)] | None = None


class EqParams(BaseModel):
    """eq filter parameters consumed by clipwright-render (writer side).

    Defaults are neutral so the full eq string can always be applied
    unconditionally on the render side. Ranges match ffmpeg eq filter limits.
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    brightness: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    contrast: Annotated[float, Field(ge=0.0, le=2.0)] = 1.0
    saturation: Annotated[float, Field(ge=0.0, le=2.0)] = 1.0
    gamma: Annotated[float, Field(ge=0.1, le=10.0)] = 1.0


class WhiteBalanceParams(BaseModel):
    """colorbalance midtone shifts; neutral = all 0. Reader mirror in render (ADR-CO-3).

    Maps 1:1 to ffmpeg colorbalance rm/gm/bm parameters. Range [-1, 1] per channel.
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    r: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    g: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    b: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0


class ColorDirective(BaseModel):
    """Directive written to timeline metadata["clipwright"]["color"].

    Generated by clipwright-color and read/validated by clipwright-render.
    scope is timeline-level only (per_clip deferred). measured is optional
    (None when measurement failed; parity with loudness U-1).
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    tool: Annotated[str, Field(max_length=64)] = "clipwright-color"
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["color"]
    target_luma: Annotated[float, Field(ge=0.0, le=255.0, allow_inf_nan=False)]
    measured: BrightnessMeasured | None = None
    eq: EqParams
    # FR-6 — new optional fields; None = render no-op (FR-10/AC-8 backward compat)
    white_balance: WhiteBalanceParams | None = None
    lut: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
