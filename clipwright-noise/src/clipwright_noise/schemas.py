"""schemas.py — clipwright-noise 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。

DetectNoiseOptions: clipwright_detect_noise の入力オプション。
DenoiseDirective: timeline-level metadata["clipwright"]["denoise"] に書く指示スキーマ。
AfftdnParams: afftdn フィルタパラメータ（DenoiseDirective.params の中身）。
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator


class DetectNoiseOptions(BaseModel):
    """clipwright_detect_noise のオプション（設計 §2.1）。

    `track` フィールドは廃止（ADR-N7）。入力第1音声を一律処理する。
    """

    backend: Annotated[
        Literal["afftdn", "deepfilternet"],
        Field(
            default="afftdn",
            description=(
                "denoise バックエンド。"
                '"afftdn"（既定）は ffmpeg afftdn フィルタを render 時に適用する。'
                '"deepfilternet" は注記のみ生成し render 適用は未対応（初版）。'
            ),
        ),
    ] = "afftdn"

    strength: Annotated[
        Literal["light", "medium", "strong"],
        Field(
            default="medium",
            description=(
                "afftdn の nr（noise reduction dB）に写像する強度。"
                "light=6 / medium=12 / strong=24（確定値）。"
                "deepfilternet backend 時は参照しない。"
            ),
        ),
    ] = "medium"


class AfftdnParams(BaseModel):
    """afftdn フィルタパラメータ（設計 §2.2・DC-AS-006）。

    render が DenoiseDirective.params を AfftdnParams で再検証する際に使用する。
    backend=="afftdn" のときのみ適用される。
    """

    nr: Annotated[
        float,
        Field(ge=0.01, le=97, description="noise reduction (dB)。範囲 [0.01, 97]。"),
    ]
    nf: Annotated[
        float,
        Field(ge=-80, le=-20, description="noise floor (dB)。範囲 [-80, -20]。"),
    ]
    nt: Annotated[
        Literal["w", "v"],
        Field(default="w", description="noise type。w=white noise（既定）、v=vinyl。"),
    ] = "w"


class DenoiseDirective(BaseModel):
    """timeline-level metadata に書く denoise 指示スキーマ（設計 §2.2）。

    noise が生成し render が検証読込する。
    backend=="afftdn" のとき params は AfftdnParams 相当の dict（render が再検証）。
    backend=="deepfilternet" のとき params は {} 固定（初版）。
    """

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["denoise"]
    backend: Literal["afftdn", "deepfilternet"]
    params: dict[str, Any]
    measured_noise_floor_db: Annotated[float, Field(ge=-200.0, le=0.0)] | None = None

    @field_validator("measured_noise_floor_db", mode="before")
    @classmethod
    def _reject_non_finite(cls, v: object) -> object:
        """inf / nan を拒否する（CWE-20: 不正な数値の排除）。"""
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("measured_noise_floor_db に inf / nan は使用できません。")
        return v
